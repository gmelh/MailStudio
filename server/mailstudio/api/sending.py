"""/api/v1/sending: which provider is connected, the SES account, and the sending domain."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Request
from pydantic import Field

from .. import aws_setup, ses
from .. import domain as domain_checks
from ..config import Settings as AppSettings
from ..db import transaction
from ..deps import Db, ReadKey, WriteKey
from ..errors import ProblemError
from ..providers import ProviderUnavailable, smtp_sending_allowed
from ..repositories import settings
from ..repositories.settings import SesSettings
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
        error = exc.response.get("Error", {})
        raise ProblemError(
            502, "ses-error", "Amazon SES returned an error", f"{error.get('Code')}: {error.get('Message')}"
        ) from None
    except BotoCoreError as exc:
        raise ProblemError(502, "ses-unreachable", "Couldn't reach Amazon SES", str(exc)) from None


@router.get("/account", summary="Sending provider and SES account status")
def sending_account(request: Request, conn: Db, _: ReadKey) -> dict[str, Any]:
    config = settings.get_group(conn, "ses", SesSettings)
    base_url = request.app.state.settings.base_url
    if not config.configured:
        return {
            "provider": "smtp (development)" if smtp_sending_allowed() else None,
            "notifications": "none",
            "ses": None,
            "warnings": []
            if smtp_sending_allowed()
            else ["Connect Amazon SES in Settings to send campaigns."],
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
    notifications = _notification_status(config, base_url)
    if notifications != "confirmed":
        warnings.append(
            "Bounce and complaint notifications aren't set up, so bad addresses won't be suppressed."
            if notifications == "none"
            else "Bounce and complaint notifications are waiting for Amazon to confirm the connection."
            if notifications == "pending"
            else "Couldn't check bounce and complaint notifications."
        )
    return {
        "provider": "ses",
        "notifications": notifications,
        "ses": {
            "region": config.region,
            "production_access": account.production_access,
            "sending_enabled": account.sending_enabled,
            "max_send_rate": account.max_send_rate,
            "max_24_hour_send": account.max_24_hour_send,
            "sent_last_24_hours": account.sent_last_24_hours,
        },
        "warnings": warnings,
    }


@router.post("/domain", summary="Set up a sending domain")
def add_domain(body: DomainIn, conn: Db, _: WriteKey) -> dict[str, Any]:
    """Registers the domain with Amazon SES and returns the DNS records to add.
    Safe to repeat."""
    name = _domain(body.domain)
    identity = _ses_call(conn, lambda client: ses.create_identity(client, name))
    return domain_checks.summary(name, identity, domain_checks.check(name, identity))


@router.get("/domain/{name}", summary="Check a sending domain's DNS")
def check_domain(name: str, conn: Db, _: ReadKey) -> dict[str, Any]:
    """Looks up each record live. `ready` is true once SES has verified the domain
    and DKIM and DMARC are in place."""
    cleaned = _domain(name)
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
            "This Mail Studio isn't on https://",
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


@router.get("/iam-policy", summary="The AWS permissions Mail Studio needs")
def iam_policy(_: ReadKey) -> dict[str, Any]:
    return aws_setup.IAM_POLICY
