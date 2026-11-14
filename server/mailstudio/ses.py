"""Amazon SES (API v2): sending, the sending domain, and the account status.

We send raw MIME built by `mailer.build`, so every message keeps its own
List-Unsubscribe headers. The SDK's automatic retries are switched off: the send
queue does its own retrying, and an SDK retry after a timeout could send twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
from typing import TYPE_CHECKING, Any

import boto3
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

from .providers import Outcome, ProviderUnavailable, Result
from .repositories.settings import SesSettings

if TYPE_CHECKING:
    from mypy_boto3_sesv2 import SESV2Client
else:
    SESV2Client = Any


# Errors that mean "nothing can be sent right now": pause the campaign, don't fail each email.
_STOP_SENDING = {
    "AccountSuspendedException": "Amazon SES has suspended sending for this account.",
    "SendingPausedException": "Sending is paused in Amazon SES for this account or configuration set.",
    "MailFromDomainNotVerifiedException": "The sender's domain isn't verified in Amazon SES yet.",
}
_TEMPORARY = {
    "TooManyRequestsException",
    "LimitExceededException",
    "ServiceUnavailable",
    "InternalFailure",
    "ThrottlingException",
    "RequestTimeout",
}
_DELIVERY_UNKNOWN = "Timed out waiting for Amazon SES; delivery unknown, so not retried to avoid a duplicate"


def client(settings: SesSettings) -> SESV2Client:
    if not settings.configured:
        raise ProviderUnavailable("Amazon SES isn't connected. Add the region and access keys in Settings.")
    return boto3.client(
        "sesv2",
        region_name=settings.region,
        aws_access_key_id=settings.access_key_id,
        aws_secret_access_key=settings.secret_access_key,
        config=Config(retries={"max_attempts": 1, "mode": "standard"}, connect_timeout=10, read_timeout=30),
    )


def _error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


class SesProvider:
    def __init__(self, settings: SesSettings, ses: SESV2Client | None = None) -> None:
        self.settings = settings
        self.ses = ses or client(settings)

    def send(self, message: EmailMessage) -> Result:
        request: dict[str, Any] = {"Content": {"Raw": {"Data": message.as_bytes()}}}
        if self.settings.configuration_set:
            request["ConfigurationSetName"] = self.settings.configuration_set
        try:
            response = self.ses.send_email(**request)
        except ClientError as exc:
            code = _error_code(exc)
            detail = f"{code}: {exc.response.get('Error', {}).get('Message', '')}"
            if code in _STOP_SENDING:
                raise ProviderUnavailable(f"{_STOP_SENDING[code]} ({detail})") from exc
            if code == "MessageRejected" and "not verified" in detail:
                # Unverified sender, or the account is still in the SES sandbox: every send would fail.
                raise ProviderUnavailable(
                    "Amazon SES rejected the message because an address isn't verified. Check the sending "
                    f"domain is verified and the account has production access. ({detail})"
                ) from exc
            outcome = Outcome.RETRY if code in _TEMPORARY else Outcome.FAILED
            return Result(outcome, error=detail)
        except ReadTimeoutError:
            # The request reached SES; it may have been accepted. Never risk a second copy.
            return Result(Outcome.FAILED, error=_DELIVERY_UNKNOWN)
        except (ConnectTimeoutError, EndpointConnectionError) as exc:
            return Result(Outcome.RETRY, error=f"Couldn't reach Amazon SES: {exc}")
        except BotoCoreError as exc:
            return Result(Outcome.RETRY, error=f"{type(exc).__name__}: {exc}")
        return Result(Outcome.SENT, message_id=response["MessageId"])

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Account and domain


@dataclass(frozen=True, slots=True)
class Account:
    production_access: bool
    """False while the account is in the SES sandbox (can only send to verified addresses)."""
    sending_enabled: bool
    max_24_hour_send: float
    max_send_rate: float
    sent_last_24_hours: float


def account(ses: SESV2Client) -> Account:
    data = ses.get_account()
    quota = data.get("SendQuota", {})
    return Account(
        production_access=bool(data.get("ProductionAccessEnabled")),
        sending_enabled=bool(data.get("SendingEnabled")),
        max_24_hour_send=float(quota.get("Max24HourSend", 0)),
        max_send_rate=float(quota.get("MaxSendRate", 0)),
        sent_last_24_hours=float(quota.get("SentLast24Hours", 0)),
    )


@dataclass(frozen=True, slots=True)
class Identity:
    domain: str
    verified: bool
    dkim_status: str
    dkim_tokens: list[str]


def get_identity(ses: SESV2Client, domain: str) -> Identity | None:
    try:
        data = ses.get_email_identity(EmailIdentity=domain)
    except ClientError as exc:
        if _error_code(exc) == "NotFoundException":
            return None
        raise
    dkim = data.get("DkimAttributes", {})
    return Identity(
        domain=domain,
        verified=bool(data.get("VerifiedForSendingStatus")),
        dkim_status=str(dkim.get("Status", "NOT_STARTED")),
        dkim_tokens=list(dkim.get("Tokens", [])),
    )


def create_identity(ses: SESV2Client, domain: str) -> Identity:
    """Register the domain with SES using Easy DKIM. Safe to call again."""
    existing = get_identity(ses, domain)
    if existing:
        return existing
    data = ses.create_email_identity(EmailIdentity=domain)
    dkim = data.get("DkimAttributes", {})
    return Identity(
        domain=domain,
        verified=bool(data.get("VerifiedForSendingStatus")),
        dkim_status=str(dkim.get("Status", "PENDING")),
        dkim_tokens=list(dkim.get("Tokens", [])),
    )
