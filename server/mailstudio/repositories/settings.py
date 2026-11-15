"""Install-wide settings, stored as one JSON value per group."""

from __future__ import annotations

import json
import secrets
import sqlite3
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field

from ..db import now
from ..document import Brand


class Business(BaseModel):
    name: str = Field("", max_length=200)
    address: str = Field("", max_length=500, description="Postal address shown in every email's footer")


class Sender(BaseModel):
    from_name: str = Field("", max_length=200)
    from_email: EmailStr | None = None
    reply_to: EmailStr | None = None


class Smtp(BaseModel):
    """Used for test sends."""

    host: str = Field("", max_length=255)
    port: int = Field(587, ge=1, le=65535)
    username: str = Field("", max_length=255)
    password: str = Field("", max_length=1000)
    security: str = Field("starttls", pattern="^(starttls|tls|none)$")


class SesSettings(BaseModel):
    """Amazon SES connection."""

    region: str = Field("", max_length=30, examples=["ap-southeast-2"])
    access_key_id: str = Field("", max_length=128)
    secret_access_key: str = Field("", max_length=256)
    configuration_set: str = Field(
        "", max_length=64, description="Optional SES configuration set for every send"
    )
    notification_topic_arn: str = Field(
        "", max_length=256, description="The SNS topic SES publishes bounces and complaints to"
    )

    @property
    def configured(self) -> bool:
        return bool(self.region and self.access_key_id and self.secret_access_key)


ProviderName = Literal["", "ses", "postmark", "mailgun", "smtp2go", "authsmtp", "smtp"]
PROVIDER_LABELS: dict[str, str] = {
    "ses": "Amazon SES",
    "postmark": "Postmark",
    "mailgun": "Mailgun",
    "smtp2go": "SMTP2GO",
    "authsmtp": "AuthSMTP",
    "smtp": "SMTP server",
}


class ProviderChoice(BaseModel):
    """Which service sends campaigns. Empty on installs from before there was a choice:
    those send with Amazon SES if it's connected (see `active_provider`)."""

    active: ProviderName = ""


class PostmarkSettings(BaseModel):
    server_token: str = Field("", max_length=200)
    message_stream: str = Field(
        "broadcast",
        max_length=100,
        description="The Broadcast message stream's ID (Postmark's default: broadcast)",
    )
    own_unsubscribes: bool = Field(
        False,
        description="True once Postmark has turned on 'Manage unsubscribes on your own' for the stream. "
        "Until then Postmark adds its own unsubscribe link and headers, "
        "so MailStudio leaves its headers out.",
    )

    @property
    def configured(self) -> bool:
        return bool(self.server_token and self.message_stream)


class MailgunSettings(BaseModel):
    api_key: str = Field("", max_length=200)
    domain: str = Field("", max_length=253, examples=["mg.yourbakery.com.au"])
    region: Literal["us", "eu"] = "us"
    webhook_signing_key: str = Field("", max_length=200)

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.domain)


class Smtp2goSettings(BaseModel):
    api_key: str = Field("", max_length=200)

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


class RelaySettings(BaseModel):
    """An SMTP relay for campaigns (AuthSMTP, or any SMTP server), and the mailbox its
    bounces come back to. SMTP can't report bounces any other way."""

    host: str = Field("", max_length=255)
    port: int = Field(587, ge=1, le=65535)
    username: str = Field("", max_length=255)
    password: str = Field("", max_length=1000)
    security: str = Field("starttls", pattern="^(starttls|tls|none)$")
    bounce_address: EmailStr | None = Field(
        None, description="Envelope sender: where bounces go. AuthSMTP needs it authorised on the account."
    )
    imap_host: str = Field("", max_length=255)
    imap_port: int = Field(993, ge=1, le=65535)
    imap_username: str = Field("", max_length=255)
    imap_password: str = Field("", max_length=1000)

    @property
    def configured(self) -> bool:
        return bool(self.host)

    @property
    def mailbox_configured(self) -> bool:
        return bool(self.bounce_address and self.imap_host and self.imap_username and self.imap_password)


class Sending(BaseModel):
    rate_per_second: int = Field(
        10,
        ge=1,
        le=200,
        description="Emails per second. Keep under your provider's limit (SES starts at 14).",
    )


GROUPS: dict[str, type[BaseModel]] = {
    "business": Business,
    "sender": Sender,
    "brand": Brand,
    "smtp": Smtp,
    "sending": Sending,
    "ses": SesSettings,
    "provider": ProviderChoice,
    "postmark": PostmarkSettings,
    "mailgun": MailgunSettings,
    "smtp2go": Smtp2goSettings,
    "relay": RelaySettings,
}

# Fields never sent back to the browser; it's told only whether each one is set.
SECRETS: dict[str, tuple[str, ...]] = {
    "smtp": ("password",),
    "ses": ("secret_access_key",),
    "postmark": ("server_token",),
    "mailgun": ("api_key", "webhook_signing_key"),
    "smtp2go": ("api_key",),
    "relay": ("password", "imap_password"),
}


def get_group[M: BaseModel](conn: sqlite3.Connection, key: str, model: type[M]) -> M:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return model.model_validate(json.loads(row["value"])) if row else model()


def put_group(conn: sqlite3.Connection, key: str, value: BaseModel) -> None:
    _put(conn, key, value.model_dump(mode="json"))


def _put(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, json.dumps(value), now()),
    )


def active_provider(conn: sqlite3.Connection) -> str:
    """The provider campaigns go through, or "" when none is chosen."""
    chosen = get_group(conn, "provider", ProviderChoice).active
    if chosen:
        return chosen
    return "ses" if get_group(conn, "ses", SesSettings).configured else ""


def get_value(conn: sqlite3.Connection, key: str) -> Any:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else None


def put_value(conn: sqlite3.Connection, key: str, value: Any) -> None:
    _put(conn, key, value)


def secret_key(conn: sqlite3.Connection) -> bytes:
    """The install's signing secret, created on first use."""
    row = conn.execute("SELECT value FROM settings WHERE key = 'secret_key'").fetchone()
    if row:
        return bytes.fromhex(json.loads(row["value"]))
    value = secrets.token_hex(32)
    conn.execute(
        "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES ('secret_key', ?, ?)",
        (json.dumps(value), now()),
    )
    row = conn.execute("SELECT value FROM settings WHERE key = 'secret_key'").fetchone()
    return bytes.fromhex(json.loads(row["value"]))
