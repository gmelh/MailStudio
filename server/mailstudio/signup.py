"""Public signup forms (/f/{list_id}) and double opt-in confirmation (/c/{token}).

A form posts ordinary HTML form fields, so it works from the hosted page or pasted
into any website. Signing up never reveals whether an address is already known:
everyone sees the same thank-you, and only new or pending people get an email.

Confirmation links show a page with a button, and only the button's POST confirms.
Mail scanners open links automatically, so a GET must never subscribe anyone.
"""

from __future__ import annotations

import html
import ipaddress
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import EmailStr, TypeAdapter, ValidationError

from . import mailer, tokens
from .config import Settings as AppSettings
from .db import connect, transaction
from .deps import Db
from .document import Brand
from .providers import Outcome, ProviderUnavailable, configured_provider
from .repositories import contacts, lists, settings, suppressions
from .repositories.settings import Business, Sender
from .schemas import ConsentIn, SignupForm, normalise_email

log = logging.getLogger("mailstudio.signup")
router = APIRouter(include_in_schema=False)
_EMAIL = TypeAdapter(EmailStr)
HONEYPOT = "website"  # hidden field: people leave it empty, form-filling bots don't


class SignupGuard:
    """Limits signups per client address, and confirmation emails per address, so the
    form can't be used to flood an inbox. In memory: one process serves the install."""

    def __init__(self, per_ip: int = 20, window: float = 3600, resend_after: float = 600) -> None:
        self.per_ip, self.window, self.resend_after = per_ip, window, resend_after
        self._signups: dict[str, list[float]] = {}
        self._emailed: dict[str, float] = {}
        self._lock = threading.Lock()

    def allow_signup(self, ip: str) -> bool:
        moment = time.monotonic()
        with self._lock:
            recent = [t for t in self._signups.get(ip, []) if moment - t < self.window]
            if len(recent) >= self.per_ip:
                self._signups[ip] = recent
                return False
            self._signups[ip] = [*recent, moment]
            return True

    def allow_email(self, key: str) -> bool:
        moment = time.monotonic()
        with self._lock:
            if moment - self._emailed.get(key, -self.resend_after) < self.resend_after:
                return False
            self._emailed[key] = moment
            if len(self._emailed) > 10_000:  # forget old entries
                self._emailed = {k: t for k, t in self._emailed.items() if moment - t < self.resend_after}
            return True


def _guard(request: Request) -> SignupGuard:
    guard: SignupGuard | None = getattr(request.app.state, "signup_guard", None)
    if guard is None:
        guard = request.app.state.signup_guard = SignupGuard()
    return guard


def _ip(request: Request) -> str | None:
    """The client's address, recorded with consent. None when it isn't a real IP."""
    host = request.client.host if request.client else ""
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Pages

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body{{margin:0;font:16px/1.6 {font};background:{outer};color:{text}}}
  main{{max-width:460px;margin:8vh auto;padding:32px;background:{background};border-radius:{radius}px;
        box-shadow:0 8px 30px #263b6514}}
  .logo{{display:block;max-width:180px;max-height:70px;margin:0 auto 20px}}
  h1{{font:700 24px/1.25 {heading_font};margin:0 0 10px;color:{heading};text-align:center}}
  p{{margin:0 0 18px;text-align:center}}
  form{{display:flex;flex-direction:column;gap:14px;margin-top:8px}}
  label{{display:flex;flex-direction:column;gap:5px;font-size:14px;font-weight:600}}
  input{{font:inherit;padding:11px 12px;border:1px solid #c9d1de;border-radius:8px;background:#fff;color:#243044}}
  input:focus{{outline:3px solid {accent}44;border-color:{accent}}}
  .row{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
  button{{font:inherit;font-weight:700;padding:12px 22px;border:0;border-radius:{radius}px;
          background:{accent};color:{button_text};cursor:pointer}}
  .error{{padding:10px 12px;border-radius:8px;background:#fbecea;color:#a3342e;text-align:left}}
  .hp{{position:absolute;left:-10000px;width:1px;height:1px;overflow:hidden}}
  small{{display:block;margin-top:22px;color:{muted};text-align:center;font-size:13px}}
  @media (max-width:480px){{main{{margin:0;border-radius:0;min-height:100vh}} .row{{grid-template-columns:1fr}}}}
</style></head>
<body><main>{logo}<h1>{title}</h1>{body}<small>{footer}</small></main></body></html>"""


def _page(conn: Db, title: str, body: str, *, status: int = 200) -> HTMLResponse:
    brand = settings.get_group(conn, "brand", Brand)
    business = settings.get_group(conn, "business", Business)
    p = brand.light
    content = _PAGE.format(
        title=html.escape(title),
        body=body,
        font=brand.fonts.body.replace(";", ""),
        heading_font=brand.fonts.heading.replace(";", ""),
        outer=p.outer,
        background=p.background,
        text=p.text,
        heading=p.heading,
        accent=p.accent,
        button_text=p.buttonText,
        muted=p.text + "aa",
        radius=min(brand.radius + 4, 20),
        logo=f'<img class="logo" src="{html.escape(brand.logo)}" alt="">' if brand.logo else "",
        footer=html.escape(business.name),
    )
    return HTMLResponse(content, status_code=status, headers={"Cache-Control": "no-store"})


def _form_html(form: SignupForm, action: str, values: dict[str, str], error: str = "") -> str:
    def field(name: str, label: str, kind: str = "text", required: bool = False, auto: str = "") -> str:
        return (
            f'<label>{label}<input name="{name}" type="{kind}" value="{html.escape(values.get(name, ""))}"'
            f'{" required" if required else ""} autocomplete="{auto}"></label>'
        )

    names = (
        '<div class="row">'
        + field("first_name", "First name", auto="given-name")
        + field("last_name", "Last name", auto="family-name")
        + "</div>"
        if form.ask_name
        else ""
    )
    return (
        (f"<p>{html.escape(form.intro)}</p>" if form.intro else "")
        + (f'<p class="error" role="alert">{html.escape(error)}</p>' if error else "")
        + f'<form method="post" action="{html.escape(action)}">'
        + field("email", "Email", "email", required=True, auto="email")
        + names
        + f'<div class="hp" aria-hidden="true"><label>Leave empty<input name="{HONEYPOT}" tabindex="-1"'
        ' autocomplete="off"></label></div>'
        + f'<button type="submit">{html.escape(form.button)}</button></form>'
    )


def _signup_form(conn: Db, list_id: str) -> tuple[dict[str, Any], SignupForm] | None:
    found = lists.get(conn, list_id)
    if found is None:
        return None
    form = SignupForm.model_validate(found["signup"])
    return (found, form) if form.enabled else None


def _unavailable(conn: Db) -> HTMLResponse:
    return _page(conn, "Signup isn't open", "<p>This signup form isn't available right now.</p>", status=404)


@router.get("/f/{list_id}", response_class=HTMLResponse)
def signup_page(list_id: str, conn: Db) -> HTMLResponse:
    found = _signup_form(conn, list_id)
    if found is None:
        return _unavailable(conn)
    mailing_list, form = found
    return _page(conn, form.title or mailing_list["name"], _form_html(form, f"/f/{list_id}", {}))


@router.post("/f/{list_id}", response_model=None)
async def signup(list_id: str, request: Request, conn: Db, background: BackgroundTasks) -> Response:
    found = _signup_form(conn, list_id)
    if found is None:
        return _unavailable(conn)
    mailing_list, form = found
    data = await request.form()
    values = {k: str(data.get(k) or "").strip()[:200] for k in ("email", "first_name", "last_name")}
    title = form.title or mailing_list["name"]

    def thanks() -> Response:
        if form.redirect_url:
            return RedirectResponse(form.redirect_url, status_code=303)
        default = (
            "Thanks! We've sent you an email: click the link in it to confirm."
            if mailing_list["double_opt_in"]
            else "Thanks, you're subscribed."
        )
        return _page(conn, "Thank you", f"<p>{html.escape(form.thanks or default)}</p>")

    if str(data.get(HONEYPOT) or ""):
        return thanks()  # a bot: say nothing useful
    ip = _ip(request)
    if not _guard(request).allow_signup(ip or "unknown"):
        return _page(
            conn, title, "<p>Too many signups from here. Please try again in an hour.</p>", status=429
        )
    try:
        email = normalise_email(str(_EMAIL.validate_python(values["email"])))
    except ValidationError:
        body = _form_html(form, f"/f/{list_id}", values, "That doesn't look like an email address.")
        return _page(conn, title, body, status=400)

    send_to: str | None = None
    with transaction(conn):
        if suppressions.is_suppressed(conn, email):
            return thanks()  # never emailed again; don't reveal why
        contact_id = contacts.id_for_email(conn, email)
        if contact_id is None:
            contact_id = contacts.create(
                conn, email=email, first_name=values["first_name"], last_name=values["last_name"]
            )
        else:
            contacts.merge(
                conn, contact_id, first_name=values["first_name"], last_name=values["last_name"], fields={}
            )
        source = f"Signup form ({title})"[:200]
        if mailing_list["double_opt_in"]:
            outcome = lists.request_confirmation(conn, list_id, contact_id, source, ip)
            if outcome == "pending" and _guard(request).allow_email(f"{list_id}:{email}"):
                send_to = contact_id
        else:
            consent = ConsentIn.model_validate({"source": source, "ip": ip})
            lists.subscribe(conn, list_id, contact_id, consent, resubscribe=True)
    if send_to:
        background.add_task(send_confirmation, request.app.state.settings, list_id, send_to)
    return thanks()


@router.get("/c/{token}", response_class=HTMLResponse)
def confirm_page(token: str, conn: Db) -> HTMLResponse:
    with transaction(conn):
        parsed = tokens.read_confirm(settings.secret_key(conn), token)
    found = lists.get(conn, parsed[1]) if parsed else None
    if parsed is None or found is None:
        return _bad_link(conn)
    return _page(
        conn,
        "Confirm your subscription",
        f"<p>One more step: confirm you'd like to get <strong>{html.escape(found['name'])}</strong>.</p>"
        '<form method="post"><button type="submit">Yes, subscribe me</button></form>',
    )


@router.post("/c/{token}", response_class=HTMLResponse)
def confirm(token: str, request: Request, conn: Db) -> HTMLResponse:
    with transaction(conn):
        parsed = tokens.read_confirm(settings.secret_key(conn), token)
        if parsed is None or lists.get(conn, parsed[1]) is None or contacts.get(conn, parsed[0]) is None:
            return _bad_link(conn)
        outcome = lists.confirm(conn, parsed[1], parsed[0], _ip(request))
    if outcome == "expired":
        return _page(
            conn,
            "This link has expired",
            "<p>Confirmation links work for 14 days. Please sign up again and we'll send a new one.</p>",
            status=410,
        )
    return _page(conn, "You're subscribed", "<p>Thanks for confirming. You'll get our next email.</p>")


def _bad_link(conn: Db) -> HTMLResponse:
    return _page(
        conn,
        "This link doesn't work",
        "<p>The confirmation link is incomplete or has been changed. Try the link in the email again.</p>",
        status=400,
    )


# ---------------------------------------------------------------------------
# The confirmation email


@dataclass(frozen=True)
class ConfirmationEmail:
    subject: str
    html: str
    text: str


def confirmation_email(brand: Brand, business: Business, list_name: str, url: str) -> ConfirmationEmail:
    p = brand.light
    e = html.escape
    who = business.name or "us"
    subject = f"Please confirm your subscription to {list_name}"
    logo = (
        f'<img src="{e(brand.logo)}" alt="{e(who)}" style="display:block;margin:0 auto 24px;max-width:180px;max-height:70px">'
        if brand.logo
        else f'<p style="margin:0 0 24px;text-align:center;font-weight:700;letter-spacing:1px">{e(who.upper())}</p>'
    )
    body_font = e(brand.fonts.body)
    markup = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{e(subject)}</title></head>
<body style="margin:0;padding:0;background:{p.outer}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{p.outer}"><tr><td align="center" style="padding:32px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background:{p.background};border-radius:{brand.radius}px">
<tr><td style="padding:36px 32px;font-family:{body_font};font-size:16px;line-height:1.6;color:{p.text}">
{logo}
<h1 style="margin:0 0 14px;font-family:{e(brand.fonts.heading)};font-size:24px;line-height:1.25;color:{p.heading};text-align:center">Confirm your subscription</h1>
<p style="margin:0 0 26px;text-align:center">Tap the button to start getting <strong>{e(list_name)}</strong> from {e(who)}.</p>
<p style="margin:0 0 26px;text-align:center"><a href="{e(url)}" style="display:inline-block;padding:13px 26px;border-radius:{min(brand.radius + 1, 24)}px;background:{p.accent};color:{p.buttonText};font-weight:700;text-decoration:none">Confirm subscription</a></p>
<p style="margin:0;font-size:13px;color:{p.text};text-align:center">If you didn't ask for this, ignore this email and you won't hear from us.</p>
</td></tr></table>
<p style="margin:18px 0 0;font-family:{body_font};font-size:12px;line-height:1.6;color:{p.text};text-align:center">{e(business.name)}{"<br>" + e(business.address) if business.address else ""}</p>
</td></tr></table></body></html>"""
    text = (
        f"Confirm your subscription\n\nOpen this link to start getting {list_name} from {who}:\n{url}\n\n"
        "If you didn't ask for this, ignore this email and you won't hear from us.\n\n"
        f"{business.name}\n{business.address}".strip()
    )
    return ConfirmationEmail(subject=subject, html=markup, text=text)


def send_confirmation(app: AppSettings, list_id: str, contact_id: str) -> bool:
    """Runs after the response is sent, with its own connection. Returns whether it was sent."""
    conn = connect(app.database_path)
    try:
        with transaction(conn):
            secret = settings.secret_key(conn)
        contact = contacts.get(conn, contact_id)
        mailing_list = lists.get(conn, list_id)
        sender = settings.get_group(conn, "sender", Sender)
        if contact is None or mailing_list is None:
            return False
        if not sender.from_email:
            log.warning(
                "No sender address in Settings, so the confirmation to %s wasn't sent", contact["email"]
            )
            return False
        url = f"{app.base_url}/c/{tokens.make_confirm(secret, contact_id, list_id)}"
        email = confirmation_email(
            settings.get_group(conn, "brand", Brand),
            settings.get_group(conn, "business", Business),
            mailing_list["name"],
            url,
        )
        message = mailer.build(
            mailer.Message(
                to=contact["email"],
                from_name=sender.from_name,
                from_email=str(sender.from_email),
                reply_to=str(sender.reply_to or ""),
                subject=email.subject,
                html=email.html,
                text=email.text,
                unsubscribe_url="",
            )
        )
        provider = configured_provider(conn)
        try:
            result = provider.send(message)
        finally:
            provider.close()
        if result.outcome is not Outcome.SENT:
            log.warning("Confirmation to %s wasn't sent: %s", contact["email"], result.error)
            return False
        return True
    except ProviderUnavailable as exc:
        log.warning("Confirmation email not sent: %s", exc)
        return False
    finally:
        conn.close()
