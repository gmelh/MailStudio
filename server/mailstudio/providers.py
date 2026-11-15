"""Sending providers: how a finished message leaves the building.

A provider reports one of three outcomes for each message:

    SENT    the provider accepted it
    RETRY   a temporary problem (rate limit, network, 4xx); safe to try again later
    FAILED  rejected for good (bad address, 5xx); never retried

Owners choose one in Settings: Amazon SES (`ses.py`), Postmark, Mailgun or SMTP2GO
(`esp.py`), or an SMTP relay such as AuthSMTP. The HTTP providers report bounces and
complaints by webhook; an SMTP relay sends with a bounce address whose mailbox
MailStudio reads (`bounces.py`), since plain SMTP can't report them any other way.

With nothing chosen, MAILSTUDIO_ALLOW_SMTP_SENDING=1 sends through the test-email server
in Settings, for development only (for example with Mailpit).
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
from .repositories.settings import (
    MailgunSettings,
    PostmarkSettings,
    RelaySettings,
    SesSettings,
    Smtp,
    Smtp2goSettings,
    active_provider,
    get_group,
)


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
    """The provider chosen in Settings."""
    # Imported here: these modules build on this one.
    from .esp import MailgunProvider, PostmarkProvider, Smtp2goProvider
    from .ses import SesProvider

    active = active_provider(conn)
    if active == "ses":
        return SesProvider(get_group(conn, "ses", SesSettings))
    if active == "postmark":
        return PostmarkProvider(get_group(conn, "postmark", PostmarkSettings))
    if active == "mailgun":
        return MailgunProvider(get_group(conn, "mailgun", MailgunSettings))
    if active == "smtp2go":
        return Smtp2goProvider(get_group(conn, "smtp2go", Smtp2goSettings))
    if active in ("authsmtp", "smtp"):
        relay = get_group(conn, "relay", RelaySettings)
        return SmtpProvider(relay, envelope_from=str(relay.bounce_address or "") or None)
    if smtp_sending_allowed():
        return SmtpProvider(get_group(conn, "smtp", Smtp))
    raise ProviderUnavailable("No sending service is connected. Choose one in Settings to send campaigns.")


class SmtpProvider:
    """Keeps one connection open and reconnects when it drops. `envelope_from` is the
    SMTP MAIL FROM address, where bounces are delivered."""

    def __init__(self, config: Smtp | RelaySettings, envelope_from: str | None = None) -> None:
        if not config.host:
            raise ProviderUnavailable("SMTP isn't set up. Add the server details in Settings.")
        self.config = config
        self.envelope_from = envelope_from
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
            self._client.send_message(message, from_addr=self.envelope_from)
            return Result(Outcome.SENT, message_id=str(message["Message-ID"] or "").strip("<>") or None)
        except smtplib.SMTPRecipientsRefused as exc:
            codes = [code for code, _ in exc.recipients.values()]
            outcome = Outcome.RETRY if all(400 <= c < 500 for c in codes) else Outcome.FAILED
            return Result(outcome, error=f"Recipient refused: {exc.recipients}")
        except smtplib.SMTPSenderRefused as exc:
            self.close()
            if exc.smtp_code >= 500:  # every message would be refused the same way
                raise ProviderUnavailable(
                    f"The SMTP server won't send from {exc.sender}. "
                    "AuthSMTP and similar services need both the "
                    f"from address and the bounce address authorised on the account. ({exc.smtp_code} "
                    f"{exc.smtp_error!r})"
                ) from exc
            return Result(Outcome.RETRY, error=f"Sender refused for now: {exc.smtp_code} {exc.smtp_error!r}")
        except smtplib.SMTPAuthenticationError as exc:
            self.close()
            raise ProviderUnavailable(
                f"The SMTP server refused the username or password ({exc.smtp_code})."
            ) from exc
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
