"""/api/v1/settings: business details, sender defaults, brand profile, the sending
service and its credentials, and the test-email server.

Secrets (passwords, keys, tokens) are never sent back: each comes back as `<name>_set`.
When updating, leave a secret out (or send null) to keep it, or send "" to clear it."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field

from ..config import Settings as AppSettings
from ..deps import Db, ReadKey, WriteKey, fingerprint, run_write
from ..document import Brand
from ..repositories import settings
from ..repositories.settings import (
    SECRETS,
    Business,
    MailgunSettings,
    PostmarkSettings,
    ProviderChoice,
    ProviderName,
    RelaySettings,
    Sender,
    Sending,
    SesSettings,
    Smtp,
    Smtp2goSettings,
)
from ..schemas import Strict

router = APIRouter(prefix="/settings", tags=["Settings"])


class SmtpOut(BaseModel):
    host: str
    port: int
    username: str
    security: str
    password_set: bool


class SesOut(BaseModel):
    region: str
    access_key_id: str
    configuration_set: str
    notification_topic_arn: str
    secret_set: bool


class SesIn(Strict):
    region: str = ""
    access_key_id: str = ""
    secret_access_key: str | None = None
    """Omit to keep the current secret; send "" to clear it."""
    configuration_set: str = ""
    notification_topic_arn: str = ""


class ProviderOut(BaseModel):
    active: ProviderName
    """The service campaigns go through ("" when none is chosen yet)."""


class PostmarkOut(BaseModel):
    message_stream: str
    own_unsubscribes: bool
    server_token_set: bool


class PostmarkIn(Strict):
    server_token: str | None = None
    message_stream: str = Field("broadcast", max_length=100)
    own_unsubscribes: bool = False


class MailgunOut(BaseModel):
    domain: str
    region: Literal["us", "eu"]
    api_key_set: bool
    webhook_signing_key_set: bool


class MailgunIn(Strict):
    api_key: str | None = None
    domain: str = Field("", max_length=253)
    region: Literal["us", "eu"] = "us"
    webhook_signing_key: str | None = None


class Smtp2goOut(BaseModel):
    api_key_set: bool


class Smtp2goIn(Strict):
    api_key: str | None = None


class RelayOut(BaseModel):
    host: str
    port: int
    username: str
    security: str
    bounce_address: str | None
    imap_host: str
    imap_port: int
    imap_username: str
    password_set: bool
    imap_password_set: bool


class RelayIn(Strict):
    host: str = Field("", max_length=255)
    port: int = Field(587, ge=1, le=65535)
    username: str = Field("", max_length=255)
    password: str | None = None
    security: str = Field("starttls", pattern="^(starttls|tls|none)$")
    bounce_address: EmailStr | None = None
    imap_host: str = Field("", max_length=255)
    imap_port: int = Field(993, ge=1, le=65535)
    imap_username: str = Field("", max_length=255)
    imap_password: str | None = None


class SettingsOut(BaseModel):
    business: Business
    sender: Sender
    brand: Brand
    smtp: SmtpOut
    sending: Sending
    ses: SesOut
    provider: ProviderOut
    postmark: PostmarkOut
    mailgun: MailgunOut
    smtp2go: Smtp2goOut
    relay: RelayOut
    base_url: str
    """Public address of this install (set by the installer, read-only here)."""


class SmtpIn(Strict):
    host: str = ""
    port: int = 587
    username: str = ""
    password: str | None = None
    """Omit to keep the current password; send "" to clear it."""
    security: str = "starttls"


class SettingsUpdate(Strict):
    """Each group given replaces that group entirely; groups left out are unchanged."""

    business: Business | None = None
    sender: Sender | None = None
    brand: Brand | None = None
    smtp: SmtpIn | None = None
    sending: Sending | None = None
    ses: SesIn | None = None
    provider: ProviderChoice | None = None
    postmark: PostmarkIn | None = None
    mailgun: MailgunIn | None = None
    smtp2go: Smtp2goIn | None = None
    relay: RelayIn | None = None


_CREDENTIALS: dict[str, type[BaseModel]] = {
    "postmark": PostmarkSettings,
    "mailgun": MailgunSettings,
    "smtp2go": Smtp2goSettings,
    "relay": RelaySettings,
}


def _masked(conn: Db, name: str, model: type[BaseModel]) -> dict[str, Any]:
    secrets = SECRETS.get(name, ())
    value = settings.get_group(conn, name, model)
    return {
        **value.model_dump(mode="json", exclude=set(secrets)),
        **{f"{secret}_set": bool(getattr(value, secret)) for secret in secrets},
    }


def _merge_secrets(conn: Db, name: str, model: type[BaseModel], given: BaseModel) -> BaseModel:
    """Secrets left out (None) keep their saved value."""
    current = settings.get_group(conn, name, model)
    values = given.model_dump()
    for secret in SECRETS.get(name, ()):
        if values.get(secret) is None:
            values[secret] = getattr(current, secret)
    return model.model_validate(values)


def _read(conn: Db, app: AppSettings) -> dict[str, Any]:
    smtp = settings.get_group(conn, "smtp", Smtp)
    ses = settings.get_group(conn, "ses", SesSettings)
    return {
        "business": settings.get_group(conn, "business", Business),
        "sender": settings.get_group(conn, "sender", Sender),
        "brand": settings.get_group(conn, "brand", Brand),
        "smtp": {**smtp.model_dump(exclude={"password"}), "password_set": bool(smtp.password)},
        "sending": settings.get_group(conn, "sending", Sending),
        "ses": {
            **ses.model_dump(exclude={"secret_access_key"}),
            "secret_set": bool(ses.secret_access_key),
        },
        "provider": {"active": settings.active_provider(conn)},
        **{name: _masked(conn, name, model) for name, model in _CREDENTIALS.items()},
        "base_url": app.base_url,
    }


@router.get("", response_model=SettingsOut, summary="Get settings")
def get_settings(request: Request, conn: Db, _: ReadKey) -> dict[str, Any]:
    return _read(conn, request.app.state.settings)


@router.patch("", response_model=SettingsOut, summary="Update settings")
def update_settings(body: SettingsUpdate, request: Request, conn: Db, key: WriteKey) -> JSONResponse:
    def action() -> tuple[int, Any]:
        for name in ("business", "sender", "brand", "sending"):
            value = getattr(body, name)
            if value is not None:
                settings.put_group(conn, name, value)
        if body.smtp is not None:
            current = settings.get_group(conn, "smtp", Smtp)
            values = body.smtp.model_dump()
            if values["password"] is None:
                values["password"] = current.password
            settings.put_group(conn, "smtp", Smtp.model_validate(values))
        if body.ses is not None:
            current_ses = settings.get_group(conn, "ses", SesSettings)
            values = body.ses.model_dump()
            if values["secret_access_key"] is None:
                values["secret_access_key"] = current_ses.secret_access_key
            settings.put_group(conn, "ses", SesSettings.model_validate(values))
        if body.provider is not None:
            settings.put_group(conn, "provider", body.provider)
        for name, model in _CREDENTIALS.items():
            given = getattr(body, name)
            if given is not None:
                settings.put_group(conn, name, _merge_secrets(conn, name, model, given))
        return 200, _read(conn, request.app.state.settings)

    # The fingerprint must not carry the SMTP password; it's only used with idempotency keys anyway.
    return run_write(conn, key, None, fingerprint(request, None), action)
