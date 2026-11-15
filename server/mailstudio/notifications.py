"""Bounce, complaint and delivery notifications from the sending provider.

Amazon SES notifications come through SNS; Postmark, Mailgun and SMTP2GO post
webhooks (at the end of this module); SMTP relays bounce to a mailbox (`bounces.py`).

For SES:

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
import hashlib
import hmac
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


# ---------------------------------------------------------------------------
# Postmark, Mailgun and SMTP2GO webhooks
#
# These arrive at /webhooks/<provider>/<token>, where the token is a secret only this
# install knows (see `webhook_token`). Mailgun also signs each event, and that
# signature is checked when its signing key is in Settings.


WEBHOOKS_SEEN = "webhooks_seen"
"""Settings key: when each provider's webhook last delivered something, for Settings to show."""


def webhook_token(secret: bytes) -> str:
    """The unguessable part of this install's webhook addresses."""
    return hmac.new(secret, b"provider-webhooks", hashlib.sha256).hexdigest()[:32]


def _apply(
    conn: sqlite3.Connection, kind: str, email: str, message_id: str, detail: str, provider: str
) -> str:
    """Record one bounce, complaint or unsubscribe, and suppress the address when it's
    permanent. Returns what happened."""
    email = email.strip().lower()
    if not email:
        return "ignored"
    if kind == "unsubscribe":
        suppressions.add(conn, email, "manual", f"Unsubscribed through {provider}")
        return kind
    _record(conn, kind, email, message_id, detail)
    if kind == "bounce":
        suppressions.add(conn, email, "bounce", detail)
    elif kind == "complaint":
        suppressions.add(conn, email, "complaint", detail)
    return kind


def handle_postmark_event(conn: sqlite3.Connection, event: dict[str, Any]) -> str:
    record = event.get("RecordType")
    message_id = str(event.get("MessageID") or "")
    if record == "Bounce":
        # HardBounce and a few others are permanent; Transient, SoftBounce, DnsError and
        # the like may clear up on their own.
        permanent = event.get("Type") in ("HardBounce", "BadEmailAddress", "ManuallyDeactivated", "Blocked")
        detail = f"{event.get('Name', event.get('Type', ''))}: {event.get('Description', '')}".strip(": ")
        return _apply(
            conn,
            "bounce" if permanent else "soft_bounce",
            str(event.get("Email", "")),
            message_id,
            detail,
            "Postmark",
        )
    if record == "SpamComplaint":
        return _apply(
            conn, "complaint", str(event.get("Email", "")), message_id, "Marked as spam", "Postmark"
        )
    if record == "SubscriptionChange" and event.get("SuppressSending"):
        reason = event.get("SuppressionReason")
        kind = {"HardBounce": "bounce", "SpamComplaint": "complaint"}.get(str(reason), "unsubscribe")
        return _apply(conn, kind, str(event.get("Recipient", "")), message_id, str(reason or ""), "Postmark")
    if record == "Delivery":
        return _apply(conn, "delivery", str(event.get("Recipient", "")), message_id, "", "Postmark")
    return "ignored"


class BadSignature(Exception):
    pass


def verify_mailgun(payload: dict[str, Any], signing_key: str) -> None:
    signature = payload.get("signature")
    if not isinstance(signature, dict):
        raise BadSignature("No signature")
    signed = f"{signature.get('timestamp', '')}{signature.get('token', '')}".encode()
    expected = hmac.new(signing_key.encode(), signed, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(signature.get("signature", ""))):
        raise BadSignature("Signature doesn't match")


def handle_mailgun_event(conn: sqlite3.Connection, payload: dict[str, Any]) -> str:
    event = payload.get("event-data")
    if not isinstance(event, dict):
        return "ignored"
    kind = event.get("event")
    email = str(event.get("recipient", ""))
    headers = event.get("message", {}).get("headers", {}) if isinstance(event.get("message"), dict) else {}
    message_id = str(headers.get("message-id", "")).strip("<>")
    if kind == "failed":
        status = event.get("delivery-status", {}) if isinstance(event.get("delivery-status"), dict) else {}
        detail = str(status.get("description") or status.get("message") or event.get("reason", ""))
        permanent = event.get("severity") == "permanent"
        return _apply(conn, "bounce" if permanent else "soft_bounce", email, message_id, detail, "Mailgun")
    if kind == "complained":
        return _apply(conn, "complaint", email, message_id, "Marked as spam", "Mailgun")
    if kind == "unsubscribed":
        return _apply(conn, "unsubscribe", email, message_id, "", "Mailgun")
    if kind == "delivered":
        return _apply(conn, "delivery", email, message_id, "", "Mailgun")
    return "ignored"


def handle_smtp2go_event(conn: sqlite3.Connection, event: dict[str, Any]) -> str:
    kind = str(event.get("event", ""))
    email = str(event.get("rcpt") or event.get("email") or "")
    message_id = str(event.get("email_id", ""))
    if kind == "bounce":
        permanent = str(event.get("bounce", "hard")).lower() != "soft"
        detail = str(event.get("context") or event.get("message") or "")
        return _apply(conn, "bounce" if permanent else "soft_bounce", email, message_id, detail, "SMTP2GO")
    if kind == "spam":
        return _apply(conn, "complaint", email, message_id, "Marked as spam", "SMTP2GO")
    if kind == "unsubscribe":
        return _apply(conn, "unsubscribe", email, message_id, "", "SMTP2GO")
    if kind == "delivered":
        return _apply(conn, "delivery", email, message_id, "", "SMTP2GO")
    return "ignored"
