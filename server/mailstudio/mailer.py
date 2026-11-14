"""Building messages and sending them over SMTP (used for test sends)."""

from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from .repositories.settings import Smtp

TIMEOUT_SECONDS = 20


@dataclass(frozen=True, slots=True)
class Message:
    to: str
    from_name: str
    from_email: str
    reply_to: str
    subject: str
    html: str
    text: str
    unsubscribe_url: str


def build(message: Message) -> EmailMessage:
    email = EmailMessage()
    email["From"] = formataddr((message.from_name, message.from_email))
    email["To"] = message.to
    email["Subject"] = message.subject
    if message.reply_to:
        email["Reply-To"] = message.reply_to
    domain = message.from_email.rpartition("@")[2] or None
    email["Message-ID"] = make_msgid(domain=domain)
    if message.unsubscribe_url:  # empty for one-off emails like signup confirmations
        # RFC 8058 one-click unsubscribe, required by Gmail and Yahoo for bulk mail.
        email["List-Unsubscribe"] = f"<{message.unsubscribe_url}>"
        email["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    email.set_content(message.text)
    email.add_alternative(message.html, subtype="html")
    return email


class SmtpError(Exception):
    pass


def send_smtp(config: Smtp, emails: list[EmailMessage]) -> None:
    if not config.host:
        raise SmtpError("SMTP isn't set up yet. Add the server details in Settings.")
    try:
        if config.security == "tls":
            client: smtplib.SMTP = smtplib.SMTP_SSL(
                config.host, config.port, timeout=TIMEOUT_SECONDS, context=ssl.create_default_context()
            )
        else:
            client = smtplib.SMTP(config.host, config.port, timeout=TIMEOUT_SECONDS)
        with client:
            if config.security == "starttls":
                client.starttls(context=ssl.create_default_context())
            if config.username:
                client.login(config.username, config.password)
            for email in emails:
                client.send_message(email)
    except (OSError, smtplib.SMTPException) as exc:
        raise SmtpError(f"{type(exc).__name__}: {exc}") from exc
