"""Reading bounces from a mailbox, for SMTP relays such as AuthSMTP.

Campaigns sent through a relay use the bounce address as their envelope sender, so
receiving servers send their bounce reports there. The worker checks that mailbox over
IMAP every few minutes and applies what it finds, like the providers' webhooks do:

- Delivery status reports (RFC 3464, `multipart/report; report-type=delivery-status`):
  a 5.x.x status is a permanent bounce and suppresses the address; 4.x.x is a soft bounce.
- Spam reports (ARF, `report-type=feedback-report`): a complaint, which suppresses.
- Older plain-text bounces ("Returned mail: see transcript for details") that carry the
  original message: the failed address is taken from the transcript when it's clear.

Messages it understood are marked read; anything else is left unread for a person.
The original Message-ID, carried in the report, links a bounce to its campaign.
"""

from __future__ import annotations

import contextlib
import email
import email.policy
import imaplib
import logging
import re
import sqlite3
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage, Message
from typing import Literal

from . import notifications
from .db import transaction
from .repositories import settings
from .repositories.settings import RelaySettings

log = logging.getLogger("mailstudio.bounces")

POLL_MINUTES = 5
STATUS_KEY = "bounce_mailbox"
"""Settings key: when the mailbox was last checked, and the last error, for Settings to show."""
TIMEOUT_SECONDS = 30
MAX_PER_POLL = 200

Kind = Literal["bounce", "soft_bounce", "complaint"]


@dataclass(frozen=True, slots=True)
class Finding:
    kind: Kind
    email: str
    message_id: str
    detail: str


_ADDRESS = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_STATUS = re.compile(r"\b([245])\.\d{1,3}\.\d{1,3}\b")


def _field(block: Message, name: str) -> str:
    return str(block.get(name, "") or "").strip()


def _address(value: str) -> str:
    """'rfc822; Bob@Example.com' → 'bob@example.com'."""
    value = value.split(";", 1)[-1]
    match = _ADDRESS.search(value)
    return match.group(0).lower() if match else ""


def _original_message_id(report: Message) -> str:
    for part in report.walk():
        if part.get_content_type() in ("message/rfc822", "text/rfc822-headers"):
            payload = part.get_payload()
            if isinstance(payload, list) and payload:  # message/rfc822: the parsed message
                original = payload[0]
                if isinstance(original, Message) and original.get("Message-ID"):
                    return str(original["Message-ID"]).strip().strip("<>")
            text = part.get_payload(decode=True) or b""
            if isinstance(text, bytes):
                match = re.search(rb"^Message-ID:\s*<?([^>\r\n]+)>?", text, re.IGNORECASE | re.MULTILINE)
                if match:
                    return match.group(1).decode(errors="replace").strip()
    return ""


def _delivery_status(report: Message, message_id: str) -> list[Finding]:
    findings = []
    for part in report.walk():
        if part.get_content_type() != "message/delivery-status":
            continue
        # Python parses the status into a list of header blocks: one for the message,
        # then one per recipient.
        blocks = part.get_payload()
        if not isinstance(blocks, list):
            continue
        for block in blocks[1:]:
            if not isinstance(block, Message):
                continue
            action = _field(block, "Action").lower()
            status = _field(block, "Status")
            address = _address(_field(block, "Final-Recipient") or _field(block, "Original-Recipient"))
            if not address or action in ("delivered", "relayed", "expanded"):
                continue
            detail = _field(block, "Diagnostic-Code") or status
            if status.startswith("5") or (action == "failed" and not status.startswith("4")):
                findings.append(Finding("bounce", address, message_id, detail))
            elif status.startswith("4") or action == "delayed":
                findings.append(Finding("soft_bounce", address, message_id, detail))
    return findings


def _feedback(report: Message, message_id: str) -> list[Finding]:
    for part in report.walk():
        if part.get_content_type() != "message/feedback-report":
            continue
        payload = part.get_payload()
        fields = (
            payload[0] if isinstance(payload, list) and payload and isinstance(payload[0], Message) else part
        )
        address = _address(_field(fields, "Original-Rcpt-To"))
        if not address:  # fall back to the original message's To
            for inner in report.walk():
                original = inner.get_payload() if inner.get_content_type() == "message/rfc822" else None
                if isinstance(original, list) and original and isinstance(original[0], Message):
                    address = _address(str(original[0].get("To", "")))
        if address:
            kind = _field(fields, "Feedback-Type") or "abuse"
            return [Finding("complaint", address, message_id, f"Spam report ({kind})")]
    return []


def _plain_bounce(message: Message, message_id: str) -> list[Finding]:
    """Old-style bounces: the transcript names the failed address next to a 5xx reply."""
    subject = str(message.get("Subject", "")).lower()
    sender = str(message.get("From", "")).lower()
    if not (
        "mailer-daemon" in sender
        or "postmaster" in sender
        or "returned mail" in subject
        or "undeliver" in subject
        or "delivery status" in subject
        or "failure" in subject
    ):
        return []
    body = message.get_body(("plain",)) if isinstance(message, EmailMessage) else None
    text = body.get_content() if isinstance(body, EmailMessage) else ""
    found: dict[str, Finding] = {}
    for line in text.splitlines():
        if not re.search(r"\b5\d\d\b|\b5\.\d+\.\d+\b", line):
            continue
        for address in _ADDRESS.findall(line):
            address = address.lower()
            if "mailer-daemon" not in address and "postmaster" not in address:
                found.setdefault(address, Finding("bounce", address, message_id, line.strip()[:300]))
    return list(found.values()) if len(found) == 1 else []  # only when it's unambiguous


def parse(raw: bytes) -> list[Finding] | None:
    """What a mailbox message reports, or None when it isn't a bounce or spam report."""
    message = email.message_from_bytes(raw, policy=email.policy.default)
    message_id = _original_message_id(message)
    if message.get_content_type() == "multipart/report":
        report_type = str(message.get_param("report-type", "")).lower()
        findings = (
            _feedback(message, message_id)
            if report_type == "feedback-report"
            else _delivery_status(message, message_id)
        )
        return findings or None
    return _plain_bounce(message, message_id) or None


def apply(conn: sqlite3.Connection, findings: list[Finding]) -> None:
    for finding in findings:
        notifications._apply(
            conn, finding.kind, finding.email, finding.message_id, finding.detail, "the bounce mailbox"
        )


# ---------------------------------------------------------------------------
# The mailbox


def connect(config: RelaySettings) -> imaplib.IMAP4:
    imap: imaplib.IMAP4
    if config.imap_port == 143:
        imap = imaplib.IMAP4(config.imap_host, config.imap_port, timeout=TIMEOUT_SECONDS)
        imap.starttls(ssl.create_default_context())
    else:
        imap = imaplib.IMAP4_SSL(
            config.imap_host,
            config.imap_port,
            ssl_context=ssl.create_default_context(),
            timeout=TIMEOUT_SECONDS,
        )
    imap.login(config.imap_username, config.imap_password)
    return imap


def poll(conn: sqlite3.Connection, config: RelaySettings) -> int:
    """Check the mailbox once. Returns how many messages were understood."""
    imap = connect(config)
    understood = 0
    try:
        imap.select("INBOX")
        status, data = imap.search(None, "UNSEEN")
        ids = data[0].split()[:MAX_PER_POLL] if status == "OK" and data and data[0] else []
        for num in ids:
            status, parts = imap.fetch(num, "(BODY.PEEK[])")  # PEEK: stays unread unless understood
            raw = next((p[1] for p in parts if isinstance(p, tuple)), b"") if status == "OK" else b""
            findings = parse(raw) if raw else None
            if findings is None:
                continue
            with transaction(conn):
                apply(conn, findings)
            imap.store(num, "+FLAGS", "\\Seen")
            understood += 1
    finally:
        with contextlib.suppress(Exception):
            imap.logout()
    return understood


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def poll_if_due(conn: sqlite3.Connection, moment: datetime | None = None) -> None:
    """Called by the worker: checks the bounce mailbox every few minutes when campaigns
    go through an SMTP relay. Errors are recorded for Settings, never raised."""
    if settings.active_provider(conn) not in ("authsmtp", "smtp"):
        return
    config = settings.get_group(conn, "relay", RelaySettings)
    if not config.mailbox_configured:
        return
    moment = moment or datetime.now(UTC)
    state = settings.get_value(conn, STATUS_KEY) or {}
    if state.get("next_at", "") > _iso(moment):
        return
    checked, next_at = _iso(moment), _iso(moment + timedelta(minutes=POLL_MINUTES))
    try:
        count = poll(conn, config)
        state = {"checked_at": checked, "next_at": next_at, "error": "", "last_count": count}
        if count:
            log.info("Bounce mailbox: applied %d report(s)", count)
    except (OSError, imaplib.IMAP4.error, ssl.SSLError) as exc:
        state = {**state, "checked_at": checked, "next_at": next_at, "error": f"{type(exc).__name__}: {exc}"}
        log.warning("Couldn't check the bounce mailbox: %s", exc)
    with transaction(conn):
        settings.put_value(conn, STATUS_KEY, state)
