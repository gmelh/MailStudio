"""/api/v1/setup: the getting-started checklist shown to new owners."""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Request

from .. import aws_setup, ses
from ..config import Settings as AppSettings
from ..deps import Db, ReadKey
from ..providers import ProviderUnavailable
from ..repositories import settings
from ..repositories.settings import Business, Sender, SesSettings

router = APIRouter(prefix="/setup", tags=["Setup"])

STEPS = ("business", "sender", "ses", "domain", "notifications", "production", "audience")


@router.get("", summary="What's set up so far")
def setup_status(request: Request, conn: Db, _: ReadKey) -> dict[str, Any]:
    """Each step is checked live: Amazon calls are made only once SES is connected."""
    app: AppSettings = request.app.state.settings
    business = settings.get_group(conn, "business", Business)
    sender = settings.get_group(conn, "sender", Sender)
    config = settings.get_group(conn, "ses", SesSettings)
    domain = str(sender.from_email).rpartition("@")[2] if sender.from_email else ""
    done = dict.fromkeys(STEPS, False)
    done["business"] = bool(business.name and business.address)
    done["sender"] = bool(sender.from_email and sender.from_name)
    done["audience"] = (
        conn.execute("SELECT 1 FROM list_members WHERE status = 'subscribed' LIMIT 1").fetchone() is not None
    )

    if config.configured:
        try:
            client = ses.client(config)
            account = ses.account(client)
            done["ses"] = True
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

    return {
        "steps": [{"key": key, "done": done[key]} for key in STEPS],
        "complete": all(done.values()),
        "domain": domain,
        "base_url": app.base_url,
        "https": app.base_url.startswith("https://"),
    }
