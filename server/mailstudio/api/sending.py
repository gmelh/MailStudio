"""/api/v1/sending: which sending service is connected and how it's doing, a live
connection check, bounce handling, and the sending domain's DNS."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Request
from pydantic import Field

from .. import aws_setup, bounces, esp, notifications, ses
from .. import domain as domain_checks
from ..config import Settings as AppSettings
from ..db import now, transaction
from ..deps import Db, ReadKey, WriteKey
from ..errors import ProblemError
from ..providers import ProviderUnavailable, smtp_sending_allowed
from ..repositories import settings
from ..repositories.settings import (
    PROVIDER_LABELS,
    MailgunSettings,
    PostmarkSettings,
    RelaySettings,
    SesSettings,
    Smtp2goSettings,
)
from ..schemas import Strict

if TYPE_CHECKING:
    from mypy_boto3_sesv2 import SESV2Client

router = APIRouter(prefix="/sending", tags=["Sending setup"])

_DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


class DomainIn(Strict):
    domain: Annotated[str, Field(min_length=3, max_length=253, examples=["acme.example"])]


def _domain(value: str) -> str:
    cleaned = value.strip().lower().rstrip(".")
    if not _DOMAIN.match(cleaned):
        raise ProblemError(
            422, "bad-domain", "Not a domain name", f"{value!r} isn't a valid domain like acme.com.au."
        )
    return cleaned


def _ses_call[T](conn: Db, call: Callable[[SESV2Client], T]) -> T:
    config = settings.get_group(conn, "ses", SesSettings)
    try:
        return call(ses.client(config))
    except ProviderUnavailable as exc:
        raise ProblemError(409, "ses-not-connected", "Amazon SES isn't connected", str(exc)) from None
    except ClientError as exc:
        raise ProblemError(502, "ses-error", "Amazon SES returned an error", ses.explain(exc)) from None
    except BotoCoreError as exc:
        raise ProblemError(502, "ses-unreachable", "Couldn't reach Amazon SES", str(exc)) from None


WEBHOOK_PROVIDERS = ("postmark", "mailgun", "smtp2go")
RELAYS = ("authsmtp", "smtp")
CHECK_KEY = "provider_check"
"""Settings key: the last connection check's result, used by the setup checklist."""


def webhook_url(conn: Db, base_url: str, provider: str) -> str:
    token = notifications.webhook_token(settings.secret_key(conn))
    return f"{base_url.rstrip('/')}/webhooks/{provider}/{token}"


def provider_configured(conn: Db, provider: str) -> bool:
    if provider == "ses":
        return settings.get_group(conn, "ses", SesSettings).configured
    if provider == "postmark":
        return settings.get_group(conn, "postmark", PostmarkSettings).configured
    if provider == "mailgun":
        return settings.get_group(conn, "mailgun", MailgunSettings).configured
    if provider == "smtp2go":
        return settings.get_group(conn, "smtp2go", Smtp2goSettings).configured
    if provider in RELAYS:
        return settings.get_group(conn, "relay", RelaySettings).configured
    return False


def bounce_status(conn: Db, provider: str, base_url: str) -> str:
    """confirmed, pending, none or unknown, for the non-SES providers."""
    if provider in WEBHOOK_PROVIDERS:
        seen = (settings.get_value(conn, notifications.WEBHOOKS_SEEN) or {}).get(provider)
        return "confirmed" if seen else "pending"
    if provider in RELAYS:
        if not settings.get_group(conn, "relay", RelaySettings).mailbox_configured:
            return "none"
        state = settings.get_value(conn, bounces.STATUS_KEY) or {}
        if state.get("error"):
            return "unknown"
        return "confirmed" if state.get("checked_at") else "pending"
    return "none"


@router.get("/account", summary="Sending service and its status")
def sending_account(request: Request, conn: Db, _: ReadKey) -> dict[str, Any]:
    base_url = request.app.state.settings.base_url
    active = settings.active_provider(conn)
    if active == "ses":
        return _ses_account(conn, base_url)
    if not active:
        return {
            "provider": "smtp (development)" if smtp_sending_allowed() else None,
            "label": None,
            "notifications": "none",
            "ses": None,
            "webhook_url": None,
            "mailbox": None,
            "warnings": []
            if smtp_sending_allowed()
            else ["Choose a sending service in Settings to send campaigns."],
        }

    label = PROVIDER_LABELS[active]
    warnings = []
    if not provider_configured(conn, active):
        warnings.append(f"Add your {label} details in Settings to send campaigns.")
    status = bounce_status(conn, active, base_url)
    hook = webhook_url(conn, base_url, active) if active in WEBHOOK_PROVIDERS else None
    mailbox = settings.get_value(conn, bounces.STATUS_KEY) if active in RELAYS else None
    if active in WEBHOOK_PROVIDERS and status != "confirmed":
        warnings.append(
            f"Bounces and spam reports: add MailStudio's webhook address in {label}. This turns to done once "
            f"{label} sends its first event (use its test button)."
        )
        if not base_url.startswith("https://"):
            warnings.append(f"{label} can only reach MailStudio once it's on a secure https:// address.")
    if active in RELAYS:
        if status == "none":
            warnings.append(
                "Bounces: add a bounce address and its mailbox (IMAP) so MailStudio can stop emailing "
                "addresses "
                "that don't work."
            )
        elif status == "unknown":
            warnings.append(f"Couldn't check the bounce mailbox: {(mailbox or {}).get('error', '')}")
    return {
        "provider": active,
        "label": label,
        "notifications": status,
        "ses": None,
        "webhook_url": hook,
        "mailbox": mailbox,
        "warnings": warnings,
    }


def _ses_account(conn: Db, base_url: str) -> dict[str, Any]:
    config = settings.get_group(conn, "ses", SesSettings)
    if not config.configured:
        return {
            "provider": "ses",
            "label": "Amazon SES",
            "notifications": "none",
            "ses": None,
            "webhook_url": None,
            "mailbox": None,
            "warnings": ["Add your Amazon SES access keys in Settings to send campaigns."],
        }
    account = _ses_call(conn, ses.account)
    warnings = []
    if not account.production_access:
        warnings.append(
            "Your SES account is in the sandbox: it can only send to verified addresses. "
            "Request production access in the Amazon SES console."
        )
    if not account.sending_enabled:
        warnings.append("Sending is disabled for this SES account.")
    rate = settings.get_group(conn, "sending", settings.Sending).rate_per_second
    if account.max_send_rate and rate > account.max_send_rate:
        warnings.append(
            f"Your sending rate ({rate}/s) is above your SES limit ({account.max_send_rate:g}/s). "
            "Lower it in Settings to avoid throttling."
        )
    notifications_status = _notification_status(config, base_url)
    if notifications_status != "confirmed":
        warnings.append(
            "Bounce and complaint notifications aren't set up, so bad addresses won't be suppressed."
            if notifications_status == "none"
            else "Bounce and complaint notifications are waiting for Amazon to confirm the connection."
            if notifications_status == "pending"
            else "Couldn't check bounce and complaint notifications."
        )
    return {
        "provider": "ses",
        "label": "Amazon SES",
        "notifications": notifications_status,
        "ses": {
            "region": config.region,
            "production_access": account.production_access,
            "sending_enabled": account.sending_enabled,
            "max_send_rate": account.max_send_rate,
            "max_24_hour_send": account.max_24_hour_send,
            "sent_last_24_hours": account.sent_last_24_hours,
        },
        "webhook_url": None,
        "mailbox": None,
        "warnings": warnings,
    }


def run_check(conn: Db, provider: str) -> str:
    """Try the saved credentials for real. Raises esp.CheckFailed with what to fix."""
    import imaplib
    import smtplib
    import ssl

    from ..providers import SmtpProvider

    if not provider_configured(conn, provider):
        raise esp.CheckFailed(f"Add your {PROVIDER_LABELS.get(provider, provider)} details first.")
    try:
        if provider == "ses":
            account = ses.account(ses.client(settings.get_group(conn, "ses", SesSettings)))
            return "Connected to Amazon SES" + (
                "" if account.production_access else " (sandbox: verified addresses only)"
            )
        if provider == "postmark":
            return esp.check_postmark(settings.get_group(conn, "postmark", PostmarkSettings))
        if provider == "mailgun":
            return esp.check_mailgun(settings.get_group(conn, "mailgun", MailgunSettings))
        if provider == "smtp2go":
            return esp.check_smtp2go(settings.get_group(conn, "smtp2go", Smtp2goSettings))
        relay = settings.get_group(conn, "relay", RelaySettings)
        smtp = SmtpProvider(relay)
        try:
            smtp._connect().quit()
        except smtplib.SMTPAuthenticationError:
            raise esp.CheckFailed("The SMTP server refused the username or password.") from None
        except (OSError, smtplib.SMTPException) as exc:
            raise esp.CheckFailed(f"Couldn't connect to {relay.host}:{relay.port}: {exc}") from None
        message = f"Connected to {relay.host}."
        if relay.mailbox_configured:
            try:
                bounces.connect(relay).logout()
            except (OSError, imaplib.IMAP4.error, ssl.SSLError) as exc:
                raise esp.CheckFailed(
                    f"Sending works, but the bounce mailbox didn't let MailStudio in: {exc}"
                ) from None
            message += " The bounce mailbox works too."
        else:
            message += " Add the bounce mailbox so bad addresses are removed automatically."
        return message
    except (esp.Unreachable, esp.NoAnswer) as exc:
        raise esp.CheckFailed(f"Couldn't reach {PROVIDER_LABELS.get(provider, provider)}: {exc}") from None
    except ClientError as exc:
        raise esp.CheckFailed(f"Amazon SES: {ses.explain(exc)}") from None
    except (BotoCoreError, ProviderUnavailable) as exc:
        raise esp.CheckFailed(f"Amazon SES: {exc}") from None


@router.post("/check", summary="Check the sending service's connection")
def check_connection(conn: Db, _: WriteKey) -> dict[str, Any]:
    """Tries the chosen service's saved credentials (and, for SMTP relays, the bounce
    mailbox) and says what to fix. The result also ticks off the setup checklist."""
    active = settings.active_provider(conn)
    if not active:
        raise ProblemError(409, "no-provider", "No sending service chosen", "Choose one in Settings first.")
    try:
        result = {"ok": True, "message": run_check(conn, active)}
    except esp.CheckFailed as exc:
        result = {"ok": False, "message": str(exc)}
    with transaction(conn):
        settings.put_value(conn, CHECK_KEY, {"provider": active, **result, "checked_at": now()})
    return {"provider": active, **result}


def last_check_ok(conn: Db, provider: str) -> bool:
    state = settings.get_value(conn, CHECK_KEY) or {}
    return bool(state.get("provider") == provider and state.get("ok"))


@router.post("/domain", summary="Set up a sending domain")
def add_domain(body: DomainIn, conn: Db, _: WriteKey) -> dict[str, Any]:
    """With Amazon SES, registers the domain with SES and returns the DNS records to add.
    Other services verify domains in their own dashboards, so this checks DMARC and SPF.
    Safe to repeat."""
    name = _domain(body.domain)
    active = settings.active_provider(conn)
    if active != "ses":
        return domain_checks.summary(name, None, domain_checks.check(name, None, active), active)
    identity = _ses_call(conn, lambda client: ses.create_identity(client, name))
    return domain_checks.summary(name, identity, domain_checks.check(name, identity))


@router.get("/domain/{name}", summary="Check a sending domain's DNS")
def check_domain(name: str, conn: Db, _: ReadKey) -> dict[str, Any]:
    """Looks up each record live. With SES, `ready` is true once SES has verified the
    domain and DKIM and DMARC are in place; with other services, once DMARC is in place
    (they check their own DKIM records)."""
    cleaned = _domain(name)
    active = settings.active_provider(conn)
    if active != "ses":
        return domain_checks.summary(cleaned, None, domain_checks.check(cleaned, None, active), active)
    identity = _ses_call(conn, lambda client: ses.get_identity(client, cleaned))
    return domain_checks.summary(cleaned, identity, domain_checks.check(cleaned, identity))


def _webhook(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/webhooks/ses"


def _notification_status(config: SesSettings, base_url: str) -> str:
    if not config.notification_topic_arn:
        return "none"
    try:
        return aws_setup.subscription_status(
            aws_setup.Clients(config), config.notification_topic_arn, _webhook(base_url)
        )
    except (ClientError, BotoCoreError):
        return "unknown"


@router.post("/notifications", summary="Set up bounce and complaint notifications")
def setup_notifications(request: Request, conn: Db, _: WriteKey) -> dict[str, Any]:
    """Creates an SNS topic and an SES configuration set in your AWS account, saves them
    in Settings, and subscribes this install's /webhooks/ses. Safe to repeat. Needs
    this install on https:// (SNS only delivers to valid HTTPS addresses)."""
    app: AppSettings = request.app.state.settings
    config = settings.get_group(conn, "ses", SesSettings)
    if not config.configured:
        raise ProblemError(409, "ses-not-connected", "Amazon SES isn't connected", "Connect it first.")
    if not app.base_url.startswith("https://"):
        raise ProblemError(
            422,
            "needs-https",
            "This MailStudio isn't on https://",
            "Amazon only sends notifications to secure (https://) addresses. The installer sets this up; "
            f"this install's address is {app.base_url}.",
        )
    try:
        clients = aws_setup.Clients(config)
        topic = aws_setup.prepare(clients)
        # Saved before subscribing: Amazon's confirmation message may arrive at once,
        # and the webhook only accepts messages from the saved topic.
        updated = config.model_copy(
            update={"notification_topic_arn": topic, "configuration_set": aws_setup.CONFIGURATION_SET}
        )
        with transaction(conn):
            settings.put_group(conn, "ses", updated)
        aws_setup.subscribe(clients, topic, _webhook(app.base_url))
    except ClientError as exc:
        error = exc.response.get("Error", {})
        hint = (
            " The IAM user needs the permissions listed in the setup guide."
            if error.get("Code") in ("AccessDenied", "AccessDeniedException", "AuthorizationError")
            else ""
        )
        raise ProblemError(
            502, "aws-error", "Amazon returned an error", f"{error.get('Code')}: {error.get('Message')}{hint}"
        ) from None
    except BotoCoreError as exc:
        raise ProblemError(502, "aws-unreachable", "Couldn't reach Amazon", str(exc)) from None
    return {
        "topic_arn": topic,
        "configuration_set": aws_setup.CONFIGURATION_SET,
        "status": _notification_status(updated, app.base_url),
    }


@router.get("/iam-policy", summary="The AWS permissions MailStudio needs")
def iam_policy(_: ReadKey) -> dict[str, Any]:
    return aws_setup.IAM_POLICY
