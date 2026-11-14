"""Install-wide settings, stored as one JSON value per group."""

from __future__ import annotations

import json
import secrets
import sqlite3
from typing import Any

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
