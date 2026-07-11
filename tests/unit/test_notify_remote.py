"""[NO-05][NO-07..09][SE-05] Deterministic remote-adapter contracts."""

from __future__ import annotations

import io
import json
import smtplib
import urllib.error
from dataclasses import replace
from email.message import EmailMessage
from types import SimpleNamespace

import pytest

from ftmon.config import ChannelConfig, SecretRef
from ftmon.model import Notification
from ftmon.notify import (
    NtfyNotifier,
    PermanentDelivery,
    RetryableDelivery,
    SmtpNotifier,
    WebhookNotifier,
)
from ftmon.notify.http import MAX_RESPONSE_BYTES, TIMEOUT_SECONDS, SafeRedirectHandler, post


class Response(io.BytesIO):
    def __init__(self, body: bytes = b"ok", status: int = 200):
        super().__init__(body)
        self.status = status
        self.bytes_read = 0

    def read(self, size=-1):
        value = super().read(size)
        self.bytes_read += len(value)
        return value

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class Opener:
    def __init__(self, response=None, error=None):
        self.response = response or Response()
        self.error = error
        self.request = None
        self.timeout = None

    def open(self, request, timeout):
        self.request, self.timeout = request, timeout
        if self.error:
            raise self.error
        return self.response


def note():
    return Notification(7, "escalate", 3, "ftmon: disk / is full", "92% used", 1234.5)


def config(settings, secret_name="TOKEN"):
    return ChannelConfig(True, 2, tuple(settings.items()), SecretRef(env=secret_name))


def test_ntfy_uses_fixed_headers_quoted_topic_and_ten_second_timeout(monkeypatch):
    """[NO-08][NO-09] Only configured routing enters the URL."""
    monkeypatch.setenv("TOKEN", "ntfy-secret")
    opener = Opener()
    notifier = NtfyNotifier(
        config({"base_url": "https://notify.example", "topic": "host one/a"}), opener=opener
    )

    result = notifier.deliver(note())

    assert result.status_code == 200
    assert opener.timeout == TIMEOUT_SECONDS == 10
    assert opener.request.full_url == "https://notify.example/host%20one%2Fa"
    assert opener.request.data == b"ftmon: disk / is full\n\n92% used"
    assert opener.request.get_header("Authorization") == "Bearer ntfy-secret"
    assert opener.request.get_header("Priority") == "5"
    assert opener.request.get_header("Tags") == "ftmon,escalate,error"
    assert opener.request.get_header("Title") == "FTMON error"


def test_ntfy_never_copies_hostile_title_into_headers(monkeypatch):
    """[NO-08] Rendered incident content belongs only in the bounded body."""
    monkeypatch.setenv("TOKEN", "ntfy-secret")
    opener = Opener()
    notifier = NtfyNotifier(
        config({"base_url": "https://notify.example", "topic": "host"}), opener=opener
    )
    hostile = Notification(7, "open", 2, "disk\r\nX-Injected: yes\x00", "full", 1.0)

    notifier.deliver(hostile)

    assert opener.request.get_header("Title") == "FTMON warning"
    assert b"X-Injected" in opener.request.data
    assert len(opener.request.data) <= 512


def test_webhook_emits_documented_schema(monkeypatch):
    """[NO-08] Payload is bounded rendered data, not raw incident attributes."""
    monkeypatch.setenv("URL", "https://hooks.example/secret/path")
    opener = Opener()
    notifier = WebhookNotifier(config({}, "URL"), opener=opener)
    enriched = replace(note(), monitor="disk", entity_id="/")

    notifier.deliver(enriched)

    assert json.loads(opener.request.data) == {
        "schema": "ftmon.notify.v1",
        "incident_id": 7,
        "kind": "escalate",
        "severity": 3,
        "title": "ftmon: disk / is full",
        "body": "92% used",
        "monitor": "disk",
        "entity": "/",
        "timestamp": 1234.5,
    }
    assert opener.request.get_header("Content-type") == "application/json"


@pytest.mark.parametrize("status,retryable", [(408, True), (429, True), (503, True), (401, False)])
def test_http_status_classification_and_safe_retry_after(status, retryable):
    """[NO-07] Response content is discarded and status families are fixed."""
    error = urllib.error.HTTPError(
        "https://hooks.example/private", status, "receiver secret", {"Retry-After": "60"},
        io.BytesIO(b"do not persist this response"),
    )
    expected = RetryableDelivery if retryable else PermanentDelivery
    with pytest.raises(expected) as caught:
        post("https://hooks.example/private", b"{}", {}, opener=Opener(error=error))
    assert caught.value.status_code == status
    assert caught.value.retry_after == "60"
    assert "private" not in str(caught.value)
    assert "persist" not in str(caught.value)


def test_http_response_read_is_capped():
    response = Response(b"x" * (MAX_RESPONSE_BYTES * 2))
    post("https://example.test/hook", b"{}", {}, opener=Opener(response=response))
    assert response.bytes_read == MAX_RESPONSE_BYTES


def test_unhandled_redirect_response_is_permanent():
    """[NO-08] A transport that declines a redirect cannot report success."""
    with pytest.raises(PermanentDelivery) as caught:
        post("https://example.test/hook", b"{}", {}, opener=Opener(response=Response(status=302)))
    assert caught.value.status_code == 302


def test_redirect_handler_rejects_https_downgrade():
    request = SimpleNamespace(full_url="https://example.test/hook")
    with pytest.raises(urllib.error.URLError):
        SafeRedirectHandler().redirect_request(
            request, None, 302, "moved", {}, "http://example.test/hook"
        )


class FakeSMTP:
    def __init__(self, host, port, **kwargs):
        self.host, self.port, self.kwargs = host, port, kwargs
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def starttls(self, *, context):
        self.calls.append(("starttls", context))

    def login(self, username, password):
        self.calls.append(("login", username, password))

    def send_message(self, message: EmailMessage):
        self.calls.append(("send", message))


def smtp_config():
    return config(
        {
            "host": "mail.example", "port": 587, "tls": "starttls",
            "username": "ftmon", "from": "ftmon@example", "to": ["ops@example"],
        },
        "PASSWORD",
    )


def test_smtp_establishes_tls_before_authentication_and_sends_plain_text(monkeypatch):
    """[NO-09][SE-05] Credentials are revealed only after STARTTLS succeeds."""
    monkeypatch.setenv("PASSWORD", "smtp-secret")
    clients = []

    def factory(*args, **kwargs):
        clients.append(FakeSMTP(*args, **kwargs))
        return clients[-1]

    result = SmtpNotifier(smtp_config(), smtp_factory=factory).deliver(note())

    assert result.status_code == 250
    client = clients[0]
    assert client.kwargs == {"timeout": 10}
    assert [call[0] for call in client.calls] == ["starttls", "login", "send"]
    assert client.calls[1][1:] == ("ftmon", "smtp-secret")
    message = client.calls[2][1]
    assert message["Subject"] == note().title
    assert message.get_content().strip() == note().body


def test_smtp_sanitizes_and_caps_hostile_subject(monkeypatch):
    """[NO-08][SE-05] Incident text cannot inject email headers or crash delivery."""
    monkeypatch.setenv("PASSWORD", "smtp-secret")
    clients = []

    def factory(*args, **kwargs):
        clients.append(FakeSMTP(*args, **kwargs))
        return clients[-1]

    hostile = Notification(7, "open", 2, "disk\r\nBcc: bad@example\x00" + "x" * 300, "full", 1.0)
    SmtpNotifier(smtp_config(), smtp_factory=factory).deliver(hostile)

    subject = str(clients[0].calls[-1][1]["Subject"])
    assert "\r" not in subject and "\n" not in subject and "\x00" not in subject
    assert "Bcc: bad@example" in subject
    assert len(subject) == 160


@pytest.mark.parametrize("code,error", [(451, RetryableDelivery), (550, PermanentDelivery)])
def test_smtp_response_family_classification(monkeypatch, code, error):
    monkeypatch.setenv("PASSWORD", "smtp-secret")

    class FailingSMTP(FakeSMTP):
        def login(self, username, password):
            raise smtplib.SMTPResponseException(code, b"unsafe receiver response")

    with pytest.raises(error) as caught:
        SmtpNotifier(smtp_config(), smtp_factory=FailingSMTP).deliver(note())
    assert caught.value.status_code == code
    assert "unsafe" not in str(caught.value)
