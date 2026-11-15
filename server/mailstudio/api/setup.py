"""/api/v1/setup: the getting-started checklist shown to new owners."""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Request

from .. import aws_setup, ses
from .. import domain as domain_checks
from ..config import Settings as AppSettings
from ..deps import Db, ReadKey
from ..providers import ProviderUnavailable
from ..repositories import settings
from ..repositories.settings import Business, Sender, SesSettings
from . import sending

router = APIRouter(prefix="/setup", tags=["Setup"])

STEPS = ("business", "sender", "provider", "domain", "notifications", "production", "audience")
"""All the steps; `production` (leaving the SES sandbox) only applies to Amazon SES."""


@router.get("", summary="What's set up so far")
def setup_status(request: Request, conn: Db, _: ReadKey) -> dict[str, Any]:
    """Each step is checked live: Amazon calls are made only once SES is connected."""
    app: AppSettings = request.app.state.settings
    business = settings.get_group(conn, "business", Business)
    sender = settings.get_group(conn, "sender", Sender)
    active = settings.active_provider(conn)
    domain = str(sender.from_email).rpartition("@")[2] if sender.from_email else ""
    steps = [s for s in STEPS if s != "production" or active in ("ses", "")]
    done = dict.fromkeys(steps, False)
    done["business"] = bool(business.name and business.address)
    done["sender"] = bool(sender.from_email and sender.from_name)
    done["audience"] = (
        conn.execute("SELECT 1 FROM list_members WHERE status = 'subscribed' LIMIT 1").fetchone() is not None
    )

    config = settings.get_group(conn, "ses", SesSettings)
    if active == "ses" and config.configured:
        try:
            client = ses.client(config)
            account = ses.account(client)
            done["provider"] = True
            done["production"] = account.production_access
            if domain:
                identity = ses.get_identity(client, domain)
                done["domain"] = bool(identity and identity.verified and identity.dkim_status == "SUCCESS")
            if config.notification_topic_arn:
                status = aws_setup.subscription_status(
                    aws_setup.Clients(config), config.notification_topic_arn, f"{app.base_url}/webhooks/ses"
                )
                done["notifications"] = status == "confirmed"
        except (ClientError, BotoCoreError, ProviderUnavailable):
            pass  # shown as not done; the step's own screen explains the error
    elif active:
        done["provider"] = sending.provider_configured(conn, active) and sending.last_check_ok(conn, active)
        if domain:
            records = domain_checks.check(domain, None, active)
            done["domain"] = bool(domain_checks.summary(domain, None, records, active)["ready"])
        done["notifications"] = sending.bounce_status(conn, active, app.base_url) == "confirmed"

    return {
        "steps": [{"key": key, "done": done[key]} for key in steps],
        "complete": all(done.values()),
        "provider": active or None,
        "domain": domain,
        "base_url": app.base_url,
        "https": app.base_url.startswith("https://"),
    }
