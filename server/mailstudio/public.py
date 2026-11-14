"""Public pages reached from emails: the unsubscribe link at /u/{token}.

GET only shows a confirmation page. Mail scanners and link previews fetch
links automatically, so a GET must never unsubscribe anyone. POST does the
work, both from the page's button and from mail apps' one-click unsubscribe
(RFC 8058: `List-Unsubscribe=One-Click` in the form body).
"""

from __future__ import annotations

import html
import json
import logging
import re
import sqlite3

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, HTMLResponse

from . import notifications, tokens
from .db import transaction
from .deps import Db
from .repositories import campaigns, contacts, lists, settings
from .repositories.settings import Business, SesSettings

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
