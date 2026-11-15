"""Public pages reached from emails: the unsubscribe link at /u/{token}.

GET only shows a confirmation page. Mail scanners and link previews fetch
links automatically, so a GET must never unsubscribe anyone. POST does the
work, both from the page's button and from mail apps' one-click unsubscribe
(RFC 8058: `List-Unsubscribe=One-Click` in the form body).
"""

from __future__ import annotations

import hmac
import html
import json
import logging
import re
import sqlite3
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qsl

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, HTMLResponse

from . import notifications, render, social, tokens
from .db import now, transaction
from .deps import Db
from .repositories import campaigns, contacts, lists, settings
from .repositories.settings import Business, MailgunSettings, SesSettings

log = logging.getLogger("mailstudio.webhooks")

router = APIRouter(include_in_schema=False)

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex"><title>{title}</title>
<style>
  body{{margin:0;font:16px/1.6 system-ui,-apple-system,"Segoe UI",Arial,sans-serif;
        background:#f5f7fb;color:#243044}}
  main{{max-width:440px;margin:12vh auto;padding:32px;background:#fff;border-radius:12px;
        box-shadow:0 8px 30px #263b6514;text-align:center}}
  h1{{font-size:22px;margin:0 0 12px}} p{{margin:0 0 20px;color:#53617b}}
  button{{font:inherit;font-weight:600;padding:11px 22px;border:0;border-radius:8px;
          background:#4768da;color:#fff;cursor:pointer}}
  small{{display:block;margin-top:24px;color:#8b98aa}}
</style></head>
<body><main><h1>{title}</h1>{body}<small>{business}</small></main></body></html>"""


def _page(conn: sqlite3.Connection, title: str, body: str, status: int = 200) -> HTMLResponse:
    business = settings.get_group(conn, "business", Business).name
    content = _PAGE.format(title=html.escape(title), body=body, business=html.escape(business))
    return HTMLResponse(content, status_code=status, headers={"Cache-Control": "no-store"})


def _invalid(conn: sqlite3.Connection) -> HTMLResponse:
    return _page(
        conn,
        "This link doesn't work",
        "<p>The unsubscribe link is incomplete or has been changed. "
        "Try the link in the email again, or reply to the email and ask to be removed.</p>",
        status=400,
    )


def _preview(conn: sqlite3.Connection) -> HTMLResponse:
    return _page(
        conn,
        "Unsubscribe preview",
        "<p>This is where subscribers land when they unsubscribe. "
        "Links in previews and test emails don't unsubscribe anyone.</p>",
    )


@router.get("/u/{token}", response_class=HTMLResponse)
def unsubscribe_page(token: str, conn: Db) -> HTMLResponse:
    if token == "preview":
        return _preview(conn)
    if token == "shared":
        return _page(
            conn,
            "This is a shared copy",
            "<p>This email was shared on the web, so its unsubscribe link doesn't know who you are. "
            "To unsubscribe, use the link at the bottom of the email you received.</p>",
        )
    parsed = tokens.read(settings.secret_key(conn), token)
    if parsed is None:
        return _invalid(conn)
    contact = contacts.get(conn, parsed[0])
    who = f" <strong>{html.escape(contact['email'])}</strong>" if contact else ""
    return _page(
        conn,
        "Unsubscribe?",
        f"<p>Stop sending emails like this to{who}?</p>"
        f'<form method="post"><button type="submit">Unsubscribe</button></form>',
    )


@router.post("/u/{token}", response_class=HTMLResponse)
async def unsubscribe(token: str, request: Request, conn: Db) -> HTMLResponse:
    if token == "preview":
        return _preview(conn)
    parsed = tokens.read(settings.secret_key(conn), token)
    if parsed is None:
        return _invalid(conn)
    form = await request.form()
    one_click = form.get("List-Unsubscribe") == "One-Click"
    source = "One-click unsubscribe from mail app" if one_click else "Unsubscribe link"
    unsubscribe_contact(conn, *parsed, source=source)
    return _page(conn, "You're unsubscribed", "<p>You won't get these emails any more.</p>")


def unsubscribe_contact(conn: sqlite3.Connection, contact_id: str, campaign_id: str, *, source: str) -> None:
    """Remove the contact from the lists the campaign was sent to. If the campaign
    no longer exists or targeted none of their lists, remove them from every list:
    when in doubt, honour the request more broadly, not less."""
    with transaction(conn):
        if contacts.get(conn, contact_id) is not None:
            subscribed = [
                r["list_id"]
                for r in conn.execute(
                    "SELECT list_id FROM list_members WHERE contact_id = ? AND status != 'unsubscribed'",
                    (contact_id,),
                )
            ]
            targeted = set(campaigns.target_lists(conn, campaign_id))
            chosen = [i for i in subscribed if i in targeted] or subscribed
            for list_id in chosen:
                lists.unsubscribe(conn, list_id, contact_id, f"{source} ({campaign_id})")


# ---------------------------------------------------------------------------
# Amazon SES notifications (via SNS)


@router.post("/webhooks/ses")
async def ses_webhook(request: Request, conn: Db) -> Response:
    """SNS posts JSON with a text/plain content type. Answers 200 once handled;
    anything else makes SNS retry later."""
    try:
        message = json.loads(await request.body())
        if not isinstance(message, dict):
            raise ValueError("not an object")
        notifications.verify(message)
    except (ValueError, notifications.InvalidNotification) as exc:
        log.warning("Rejected SNS message: %s", exc)
        return Response("invalid notification", status_code=400)

    expected = settings.get_group(conn, "ses", SesSettings).notification_topic_arn
    if not expected or message.get("TopicArn") != expected:
        log.warning("Ignored SNS message from unexpected topic %s", message.get("TopicArn"))
        return Response("unexpected topic", status_code=403)

    kind = message["Type"]
    if kind == "SubscriptionConfirmation":
        try:
            notifications.confirm_subscription(message["SubscribeURL"])
        except (OSError, notifications.InvalidNotification) as exc:
            log.warning("Couldn't confirm SNS subscription: %s", exc)
            return Response("confirmation failed", status_code=502)
        log.info("Confirmed SNS subscription to %s", expected)
    elif kind == "Notification":
        try:
            event = json.loads(message["Message"])
        except ValueError:
            return Response("invalid SES event", status_code=400)
        with transaction(conn):
            outcome = notifications.handle_ses_event(conn, event)
        log.info("SES %s for message %s", outcome, event.get("mail", {}).get("messageId"))
    return Response(status_code=200)


# ---------------------------------------------------------------------------
# Postmark, Mailgun and SMTP2GO webhooks

_HANDLERS: dict[str, Callable[[sqlite3.Connection, dict[str, Any]], str]] = {
    "postmark": notifications.handle_postmark_event,
    "mailgun": notifications.handle_mailgun_event,
    "smtp2go": notifications.handle_smtp2go_event,
}


async def _payload(request: Request) -> dict[str, Any]:
    """JSON, or a form post (SMTP2GO can send either)."""
    raw = (await request.body()).decode("utf-8", errors="replace")
    if "application/x-www-form-urlencoded" in request.headers.get("content-type", ""):
        return dict(parse_qsl(raw, keep_blank_values=True))
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("not an object")
    return value


@router.post("/webhooks/{provider}/{token}")
async def provider_webhook(provider: str, token: str, request: Request, conn: Db) -> Response:
    """Answers 200 once handled (including events MailStudio doesn't use), so the
    provider doesn't retry them. A wrong token gets 404, as if the address didn't exist."""
    handler = _HANDLERS.get(provider)
    expected = notifications.webhook_token(settings.secret_key(conn))
    if handler is None or not hmac.compare_digest(token, expected):
        return Response("Not found", status_code=404)
    try:
        payload = await _payload(request)
    except ValueError:
        return Response("invalid payload", status_code=400)
    if provider == "mailgun":
        key = settings.get_group(conn, "mailgun", MailgunSettings).webhook_signing_key
        if key:
            try:
                notifications.verify_mailgun(payload, key)
            except notifications.BadSignature as exc:
                log.warning("Rejected Mailgun webhook: %s", exc)
                return Response("bad signature", status_code=403)
    with transaction(conn):
        outcome = handler(conn, payload)
        seen = settings.get_value(conn, notifications.WEBHOOKS_SEEN) or {}
        settings.put_value(conn, notifications.WEBHOOKS_SEEN, {**seen, provider: now()})
    log.info("%s webhook: %s", provider, outcome)
    return Response(status_code=200)


# ---------------------------------------------------------------------------
# Web versions ("View in browser")

_NOINDEX = '<meta name="robots" content="noindex">'


def _web_version(
    conn: sqlite3.Connection, campaign_id: str, values: dict[str, str], personal: bool
) -> Response:
    row = conn.execute("SELECT snapshot FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
    if not row or not row["snapshot"]:
        return _page(conn, "This email isn't available", "<p>It may have been removed.</p>", status=404)
    snapshot = json.loads(row["snapshot"])
    content = render.personalise(snapshot["html"], values, escape=True)
    content = content.replace("<head>", f"<head>{_NOINDEX}" if personal else "<head>", 1)
    headers = (
        {"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex"}  # it may show the reader's details
        if personal
        else {"Cache-Control": "public, max-age=300"}
    )
    return HTMLResponse(content, headers=headers)


@router.get("/v/preview", response_class=HTMLResponse)
def web_version_preview(conn: Db) -> HTMLResponse:
    return _page(
        conn,
        "Web version preview",
        "<p>Once a campaign is sent, “View in browser” opens the reader's own copy of it here.</p>",
    )


@router.get("/v/s/{token}", response_class=HTMLResponse)
def shared_web_version(token: str, request: Request, conn: Db) -> Response:
    """A copy anyone can see: no personal details, and an unsubscribe link that says
    where to unsubscribe instead."""
    campaign_id = tokens.read_share(settings.secret_key(conn), token)
    if campaign_id is None:
        return _page(
            conn, "This link doesn't work", "<p>It's incomplete or has been changed.</p>", status=404
        )
    base = request.app.state.settings.base_url
    blank = {"email": "", "first_name": "", "last_name": "", "fields": {}}
    values = render.merge_values(blank, f"{base}/u/shared", f"{base}/v/s/{token}")
    return _web_version(conn, campaign_id, values, personal=False)


@router.get("/v/{token}", response_class=HTMLResponse)
def web_version(token: str, request: Request, conn: Db) -> Response:
    """The reader's own copy, personalised like their email."""
    parsed = tokens.read(settings.secret_key(conn), token)
    if parsed is None:
        return _page(
            conn, "This link doesn't work", "<p>It's incomplete or has been changed.</p>", status=404
        )
    contact_id, campaign_id = parsed
    contact = contacts.get(conn, contact_id) or {"email": "", "first_name": "", "last_name": "", "fields": {}}
    base = request.app.state.settings.base_url
    values = render.merge_values(contact, f"{base}/u/{token}", f"{base}/v/{token}")
    return _web_version(conn, campaign_id, values, personal=True)


# ---------------------------------------------------------------------------
# Social icons


@router.get("/social/{name}.png")
def social_icon(name: str, request: Request) -> Response:
    """Public, like uploaded images. Made on first use, then kept; they never change."""
    app_settings = request.app.state.settings
    path = social.icon_file(name, app_settings.sections_dir / "social", app_settings.data_dir / "social")
    if path is None:
        return Response("Not found", status_code=404)
    return FileResponse(
        path, media_type="image/png", headers={"Cache-Control": "public, max-age=31536000, immutable"}
    )


# ---------------------------------------------------------------------------
# Uploaded images


@router.get("/assets/{file_name}")
def asset_file(file_name: str, request: Request) -> Response:
    """Public, because mail apps load images without logging in. Names are random IDs,
    so they can't be guessed or listed. Files never change, so they cache for a year."""
    if not re.fullmatch(r"ast_[0-9A-Z]{26}\.(jpg|png|gif)", file_name):
        return Response("Not found", status_code=404)
    path = request.app.state.settings.assets_dir / file_name
    if not path.is_file():
        return Response("Not found", status_code=404)
    return FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})
