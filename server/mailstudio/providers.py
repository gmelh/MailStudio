"""Sending providers: how a finished message leaves the building.

A provider reports one of three outcomes for each message:

    SENT    the provider accepted it
    RETRY   a temporary problem (rate limit, network, 4xx); safe to try again later
    FAILED  rejected for good (bad address, 5xx); never retried

Production installs send through Amazon SES (see `ses.py`). SMTP bulk sending is for development
only (for example with Mailpit), because plain SMTP can't report bounces or
complaints back, so the suppression list would go stale. It is refused unless
MAILSTUDIO_ALLOW_SMTP_SENDING=1 is set.
"""

from __future__ import annotations

import contextlib
import enum
import os
import smtplib
import sqlite3
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

from .mailer import TIMEOUT_SECONDS
from .repositories.settings import SesSettings, Smtp, get_group


class Outcome(enum.Enum):
    SENT = "sent"
    RETRY = "retry"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Result:
    outcome: Outcome
    message_id: str | None = None
    error: str | None = None


class Provider(Protocol):
    def send(self, message: EmailMessage) -> Result: ...

    def close(self) -> None: ...


class ProviderUnavailable(Exception):
    """No provider is configured, so sending can't happen at all."""


def smtp_sending_allowed() -> bool:
    return os.environ.get("MAILSTUDIO_ALLOW_SMTP_SENDING") == "1"


def configured_provider(conn: sqlite3.Connection) -> Provider:
    """The provider set up for this install: Amazon SES when connected."""
    from .ses import SesProvider  # imported here: ses builds on this module

    ses = get_group(conn, "ses", SesSettings)
    if ses.configured:
        return SesProvider(ses)
    if smtp_sending_allowed():
        return SmtpProvider(get_group(conn, "smtp", Smtp))
    raise ProviderUnavailable(
        "No sending provider is connected. Connect Amazon SES in Settings to send campaigns."
    )


class SmtpProvider:
    """Keeps one connection open and reconnects when it drops."""

    def __init__(self, config: Smtp) -> None:
        if not config.host:
            raise ProviderUnavailable("SMTP isn't set up. Add the server details in Settings.")
        self.config = config
        self._client: smtplib.SMTP | None = None

    def _connect(self) -> smtplib.SMTP:
        c = self.config
        if c.security == "tls":
            client: smtplib.SMTP = smtplib.SMTP_SSL(
                c.host, c.port, timeout=TIMEOUT_SECONDS, context=ssl.create_default_context()
            )
        else:
            client = smtplib.SMTP(c.host, c.port, timeout=TIMEOUT_SECONDS)
            if c.security == "starttls":
                client.starttls(context=ssl.create_default_context())
        if c.username:
            client.login(c.username, c.password)
        return client

    def send(self, message: EmailMessage) -> Result:
        try:
            if self._client is None:
                self._client = self._connect()
            self._client.send_message(message)
            return Result(Outcome.SENT, message_id=message["Message-ID"])
        except smtplib.SMTPRecipientsRefused as exc:
            codes = [code for code, _ in exc.recipients.values()]
            outcome = Outcome.RETRY if all(400 <= c < 500 for c in codes) else Outcome.FAILED
            return Result(outcome, error=f"Recipient refused: {exc.recipients}")
        except smtplib.SMTPResponseException as exc:
            self.close()
            outcome = Outcome.RETRY if 400 <= exc.smtp_code < 500 else Outcome.FAILED
            return Result(outcome, error=f"{exc.smtp_code} {exc.smtp_error!r}")
        except (OSError, smtplib.SMTPException) as exc:
            # Connection-level problems: the message wasn't accepted, so retry later.
            self.close()
            return Result(Outcome.RETRY, error=f"{type(exc).__name__}: {exc}")

    def close(self) -> None:
        if self._client is not None:
            with contextlib.suppress(OSError, smtplib.SMTPException):
                self._client.quit()
            self._client = None
