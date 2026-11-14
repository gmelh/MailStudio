"""/api/v1/settings: business details, sender defaults, brand profile and SMTP."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..config import Settings as AppSettings
from ..deps import Db, ReadKey, WriteKey, fingerprint, run_write
from ..document import Brand
from ..repositories import settings
from ..repositories.settings import Business, Sender, Sending, SesSettings, Smtp
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


class SettingsOut(BaseModel):
    business: Business
    sender: Sender
    brand: Brand
    smtp: SmtpOut
    sending: Sending
    ses: SesOut
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
        return 200, _read(conn, request.app.state.settings)

    # The fingerprint must not carry the SMTP password; it's only used with idempotency keys anyway.
    return run_write(conn, key, None, fingerprint(request, None), action)
