"""Signed link tokens: `<contact_id>.<other_id>.<signature>`.

Unsubscribe links carry a campaign ID; confirmation links carry a list ID and are
signed with a different purpose, so neither kind can be passed off as the other.
The "View in browser" link uses the same token as unsubscribing (both are the reader's
own). Share links (`<campaign_id>.<signature>`) show a generic copy of a sent campaign.

The signature (HMAC-SHA256 with the install's secret, truncated to 128 bits)
stops anyone unsubscribing other people by guessing IDs.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re

_ID = re.compile(r"^[a-z]{3}_[0-9A-Z]{26}$")


def _signature(secret: bytes, message: str) -> str:
    digest = hmac.new(secret, message.encode(), hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def make(secret: bytes, contact_id: str, campaign_id: str) -> str:
    message = f"{contact_id}.{campaign_id}"
    return f"{message}.{_signature(secret, message)}"


def read(secret: bytes, token: str) -> tuple[str, str] | None:
    """Returns (contact_id, campaign_id), or None if the token is malformed or forged."""
    parts = token.split(".")
    if len(parts) != 3 or not all(_ID.match(p) for p in parts[:2]):
        return None
    contact_id, campaign_id, signature = parts
    expected = _signature(secret, f"{contact_id}.{campaign_id}")
    return (contact_id, campaign_id) if hmac.compare_digest(signature, expected) else None


def make_confirm(secret: bytes, contact_id: str, list_id: str) -> str:
    message = f"{contact_id}.{list_id}"
    return f"{message}.{_signature(secret, f'confirm:{message}')}"


def read_confirm(secret: bytes, token: str) -> tuple[str, str] | None:
    """Returns (contact_id, list_id), or None if the token is malformed or forged."""
    parts = token.split(".")
    if len(parts) != 3 or not all(_ID.match(p) for p in parts[:2]):
        return None
    contact_id, list_id, signature = parts
    expected = _signature(secret, f"confirm:{contact_id}.{list_id}")
    return (contact_id, list_id) if hmac.compare_digest(signature, expected) else None


def make_share(secret: bytes, campaign_id: str) -> str:
    return f"{campaign_id}.{_signature(secret, f'share:{campaign_id}')}"


def read_share(secret: bytes, token: str) -> str | None:
    """Returns the campaign ID, or None if the token is malformed or forged."""
    parts = token.split(".")
    if len(parts) != 2 or not _ID.match(parts[0]):
        return None
    expected = _signature(secret, f"share:{parts[0]}")
    return parts[0] if hmac.compare_digest(parts[1], expected) else None
