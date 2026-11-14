"""Bounce, complaint and delivery notifications from Amazon SES, delivered by SNS.

Every message is checked before it's trusted:

1. Its signature must verify against an Amazon SNS signing certificate, fetched
   only from an https://sns.<region>.amazonaws.com address.
2. It must come from the SNS topic configured in Settings. A valid signature only
   proves SNS sent it; anyone with an AWS account can make SNS send us messages,
   so the topic check is what makes it *our* SES account talking.

Permanent bounces and complaints suppress the address everywhere. Notifications
are matched to the campaign and contact through the SES message ID recorded when
the email was sent.
"""

from __future__ import annotations

import base64
import logging
import re
import sqlite3
import urllib.request
from collections.abc import Callable
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .db import now
from .ids import new_id
from .repositories import suppressions

log = logging.getLogger("mailstudio.notifications")

_SNS_HOST = re.compile(r"^sns\.[a-z0-9-]+\.amazonaws\.com(\.cn)?$")
_SIGNED_FIELDS = {
    "Notification": ("Message", "MessageId", "Subject", "Timestamp", "TopicArn", "Type"),
    "SubscriptionConfirmation": (
        "Message",
        "MessageId",
        "SubscribeURL",
        "Timestamp",
        "Token",
        "TopicArn",
        "Type",
    ),
    "UnsubscribeConfirmation": (
        "Message",
        "MessageId",
        "SubscribeURL",
        "Timestamp",
        "Token",
        "TopicArn",
        "Type",
    ),
}
FETCH_TIMEOUT = 5


class InvalidNotification(Exception):
    pass


def is_sns_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "https" and bool(_SNS_HOST.match(parts.hostname or ""))


@lru_cache(maxsize=16)
def fetch_certificate(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT) as response:
        data: bytes = response.read(64 * 1024)
        return data


def verify(message: dict[str, Any], get_certificate: Callable[[str], bytes] | None = None) -> None:
    """Raise InvalidNotification unless `message` carries a valid SNS signature."""
    get_certificate = get_certificate or fetch_certificate
    kind = message.get("Type")
    if kind not in _SIGNED_FIELDS:
        raise InvalidNotification(f"Unknown message type {kind!r}")
    cert_url = str(message.get("SigningCertURL", ""))
    if not is_sns_url(cert_url) or not urlsplit(cert_url).path.endswith(".pem"):
        raise InvalidNotification("Signing certificate isn't hosted by Amazon SNS")

    version = str(message.get("SignatureVersion"))
    algorithm: hashes.HashAlgorithm
    if version == "1":
        algorithm = hashes.SHA1()
    elif version == "2":
        algorithm = hashes.SHA256()
    else:
        raise InvalidNotification(f"Unsupported signature version {version!r}")

    signed = "".join(f"{field}\n{message[field]}\n" for field in _SIGNED_FIELDS[kind] if field in message)
    try:
        certificate = x509.load_pem_x509_certificate(get_certificate(cert_url))
        key = certificate.public_key()
        if not isinstance(key, rsa.RSAPublicKey):
            raise InvalidNotification("Unexpected certificate key type")
        key.verify(
            base64.b64decode(message.get("Signature", "")), signed.encode(), padding.PKCS1v15(), algorithm
        )
    except (InvalidSignature, ValueError) as exc:
        raise InvalidNotification("Signature doesn't match") from exc
    except OSError as exc:
        raise InvalidNotification(f"Couldn't fetch the signing certificate: {exc}") from exc


def confirm_subscription(url: str) -> None:
    """Visiting SubscribeURL is how SNS subscriptions are confirmed."""
    if not is_sns_url(url):
        raise InvalidNotification("SubscribeURL isn't an Amazon SNS address")
    with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT):
        pass


# ---------------------------------------------------------------------------
# Handling SES events


def _job_for(conn: sqlite3.Connection, message_id: str, email: str) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT campaign_id, contact_id FROM send_jobs WHERE provider_message_id = ? AND email = ?",
        (message_id, email),
    ).fetchone()
    return row


def _record(conn: sqlite3.Connection, kind: str, email: str, message_id: str, detail: str) -> None:
    job = _job_for(conn, message_id, email)
    conn.execute(
        "INSERT OR IGNORE INTO events (id, type, email, campaign_id, contact_id, provider_message_id, detail,"
        " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            new_id("event"),
            kind,
            email,
            job["campaign_id"] if job else None,
            job["contact_id"] if job else None,
            message_id,
            detail[:1000],
            now(),
        ),
    )


def _addresses(entries: list[dict[str, Any]]) -> list[str]:
    return [str(e["emailAddress"]).strip().lower() for e in entries if e.get("emailAddress")]


def handle_ses_event(conn: sqlite3.Connection, event: dict[str, Any]) -> str:
    """Apply one SES event (identity notification or configuration-set event). Returns what happened."""
    kind = event.get("notificationType") or event.get("eventType")
    message_id = str(event.get("mail", {}).get("messageId", ""))

    if kind == "Bounce":
        bounce = event.get("bounce", {})
        permanent = bounce.get("bounceType") == "Permanent"
        for entry in bounce.get("bouncedRecipients", []):
            email = str(entry.get("emailAddress", "")).strip().lower()
            if not email:
                continue
            detail = f"{bounce.get('bounceSubType', '')}: {entry.get('diagnosticCode', '')}".strip(": ")
            _record(conn, "bounce" if permanent else "soft_bounce", email, message_id, detail)
            if permanent:
                suppressions.add(conn, email, "bounce", detail)
        return "bounce" if permanent else "soft_bounce"

    if kind == "Complaint":
        complaint = event.get("complaint", {})
        detail = str(complaint.get("complaintFeedbackType", "") or "Marked as spam")
        for email in _addresses(complaint.get("complainedRecipients", [])):
            _record(conn, "complaint", email, message_id, detail)
            suppressions.add(conn, email, "complaint", detail)
        return "complaint"

    if kind == "Delivery":
        for email in _addresses(
            [{"emailAddress": r} for r in event.get("delivery", {}).get("recipients", [])]
        ):
            _record(conn, "delivery", email, message_id, "")
        return "delivery"

    return "ignored"
