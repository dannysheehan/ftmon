"""Authenticated SMTP submission adapter (NO-05, NO-07..09)."""

from __future__ import annotations

import smtplib
import ssl
from collections.abc import Callable
from email.message import EmailMessage

from ftmon.config import ChannelConfig
from ftmon.model import Notification
from ftmon.notify.base import DeliveryResult, PermanentDelivery, RetryableDelivery

SMTPFactory = Callable[..., smtplib.SMTP]


class SmtpNotifier:
    name = "smtp"

    def __init__(
        self,
        config: ChannelConfig,
        *,
        smtp_factory: SMTPFactory | None = None,
        smtp_ssl_factory: SMTPFactory | None = None,
    ) -> None:
        settings = dict(config.settings)
        if config.secret is None:
            raise PermanentDelivery("smtp_missing_password")
        self._password = config.secret.resolve()
        self._host = settings.get("host")
        self._port = settings.get("port", 587)
        self._tls = settings.get("tls", "starttls")
        self._username = settings.get("username")
        self._sender = settings.get("from")
        recipients = settings.get("to")
        if not all(isinstance(v, str) and v for v in (self._host, self._username, self._sender)):
            raise PermanentDelivery("smtp_incomplete_config")
        if not isinstance(recipients, list) or not recipients:
            raise PermanentDelivery("smtp_incomplete_config")
        self._recipients = tuple(str(item) for item in recipients)
        self._smtp_factory = smtp_factory or smtplib.SMTP
        self._smtp_ssl_factory = smtp_ssl_factory or smtplib.SMTP_SSL

    def deliver(self, n: Notification) -> DeliveryResult:
        try:
            message = EmailMessage()
            # EmailMessage rejects CR/LF, but explicit sanitising also covers
            # other C0/DEL controls and makes an adversarial title deliverable.
            message["Subject"] = _safe_subject(n.title)
            message["From"] = self._sender
            message["To"] = ", ".join(self._recipients)
            message.set_content(n.body)
            context = ssl.create_default_context()
            if self._tls == "implicit":
                client_context = self._smtp_ssl_factory(
                    self._host, self._port, timeout=10, context=context
                )
            else:
                client_context = self._smtp_factory(self._host, self._port, timeout=10)
            with client_context as client:
                if self._tls == "starttls":
                    client.starttls(context=context)
                # TLS is established before credentials leave the process.
                client.login(self._username, self._password._reveal())
                client.send_message(message)
        except smtplib.SMTPResponseException as exc:
            self._raise_status(exc.smtp_code)
        except smtplib.SMTPRecipientsRefused as exc:
            codes = [value[0] for value in exc.recipients.values()]
            self._raise_status(max(codes, default=500))
        except ValueError:
            raise PermanentDelivery("smtp_message_invalid") from None
        except (TimeoutError, OSError, smtplib.SMTPException):
            raise RetryableDelivery("smtp_transport") from None
        return DeliveryResult(status_code=250)

    @staticmethod
    def _raise_status(code: int) -> None:
        if 400 <= code < 500:
            raise RetryableDelivery("smtp_status", status_code=code) from None
        raise PermanentDelivery("smtp_status", status_code=code) from None


def _safe_subject(value: str) -> str:
    cleaned = "".join(" " if ord(char) < 32 or ord(char) == 127 else char for char in value)
    return cleaned[:160]
