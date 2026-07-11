"""Hardened stdlib HTTP transport shared by remote adapters (NO-08, SE-05)."""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Mapping
from urllib.parse import urlsplit

from ftmon.notify.base import DeliveryResult, PermanentDelivery, RetryableDelivery

TIMEOUT_SECONDS = 10.0
MAX_RESPONSE_BYTES = 8 * 1024


class _DowngradeRedirect(urllib.error.URLError):
    pass


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Permit normal redirects but never discard HTTPS transport security."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        if urlsplit(req.full_url).scheme == "https" and urlsplit(newurl).scheme != "https":
            raise _DowngradeRedirect("HTTPS redirect downgrade rejected")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def build_http_opener() -> urllib.request.OpenerDirector:
    # urllib's default HTTPS handler uses the platform trust store and verifies
    # hostnames.  Supplying no custom SSLContext is deliberate (NO-08).
    return urllib.request.build_opener(SafeRedirectHandler())


def post(
    url: str,
    body: bytes,
    headers: Mapping[str, str],
    *,
    opener: urllib.request.OpenerDirector | None = None,
) -> DeliveryResult:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    transport = opener or build_http_opener()
    try:
        with transport.open(request, timeout=TIMEOUT_SECONDS) as response:
            # Bound reads even on success: a compromised endpoint must not make
            # the delivery worker retain an arbitrary response in memory.
            response.read(MAX_RESPONSE_BYTES)
            status = int(response.getcode())
    except urllib.error.HTTPError as exc:
        # Read only a bounded amount and deliberately discard it. Receiver text
        # is not safe error data and must never reach persistent diagnostics.
        exc.read(MAX_RESPONSE_BYTES)
        retry_after = exc.headers.get("Retry-After") if exc.headers is not None else None
        error = (
            RetryableDelivery
            if exc.code in (408, 429) or exc.code >= 500
            else PermanentDelivery
        )
        raise error("http_status", status_code=exc.code, retry_after=retry_after) from None
    except _DowngradeRedirect:
        raise PermanentDelivery("redirect_downgrade") from None
    except (TimeoutError, OSError, urllib.error.URLError):
        raise RetryableDelivery("http_transport") from None
    if not 200 <= status < 300:
        error = RetryableDelivery if status in (408, 429) or status >= 500 else PermanentDelivery
        raise error("http_status", status_code=status)
    return DeliveryResult(status_code=status)
