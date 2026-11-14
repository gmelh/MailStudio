"""API keys.

A key looks like `ms_<43 url-safe characters>`. Only its SHA-256 hash is stored,
so a leaked database doesn't leak working keys. Each key carries scopes:

    read   view contacts, lists, campaigns and reports
    write  create and change them
    send   schedule and send campaigns (kept separate so CI can draft but not send)
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from .db import now
from .errors import ProblemError
from .ids import new_id

SCOPES: Final = ("read", "write", "send")
_PREFIX_LENGTH: Final = 10
_LAST_USED_RESOLUTION: Final = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is making a request: an API key, or a person logged in to the web app."""

    id: str
    name: str
    scopes: frozenset[str]
    kind: str = "key"  # "key" or "user"
    csrf_token: str | None = None


ApiKey = Principal
ALL_SCOPES = frozenset(SCOPES)


@dataclass(frozen=True, slots=True)
class NewKey:
    id: str
    token: str
    """The full key. Shown once at creation and never retrievable again."""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def parse_scopes(value: str) -> frozenset[str]:
    scopes = frozenset(s for s in value.replace(",", " ").split() if s)
    unknown = scopes - set(SCOPES)
    if unknown:
        raise ValueError(f"Unknown scope(s): {', '.join(sorted(unknown))}. Use: {', '.join(SCOPES)}")
    if not scopes:
        raise ValueError("At least one scope is required")
    return scopes


def create_key(conn: sqlite3.Connection, name: str, scopes: frozenset[str]) -> NewKey:
    token = "ms_" + secrets.token_urlsafe(32)
    key_id = new_id("api_key")
    conn.execute(
        "INSERT INTO api_keys (id, name, prefix, key_hash, scopes, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (key_id, name, token[:_PREFIX_LENGTH], _hash(token), " ".join(sorted(scopes)), now()),
    )
    return NewKey(key_id, token)


def authenticate(conn: sqlite3.Connection, authorization: str | None) -> ApiKey:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ProblemError(
            401,
            "unauthorized",
            "Authentication required",
            "Send an API key as `Authorization: Bearer <key>`.",
        )
    row = conn.execute(
        "SELECT id, name, scopes, last_used_at FROM api_keys WHERE key_hash = ? AND revoked_at IS NULL",
        (_hash(token.strip()),),
    ).fetchone()
    if row is None:
        raise ProblemError(401, "unauthorized", "Invalid API key", "The key is unknown or has been revoked.")

    # Record usage at coarse resolution so reads don't each cost a write.
    last_used = row["last_used_at"]
    stale = datetime.now(UTC) - _LAST_USED_RESOLUTION
    if last_used is None or datetime.fromisoformat(last_used) < stale:
        conn.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (now(), row["id"]))

    return ApiKey(row["id"], row["name"], frozenset(row["scopes"].split()))


def check_scope(key: ApiKey, scope: str) -> None:
    if scope not in key.scopes:
        raise ProblemError(
            403,
            "forbidden",
            "Missing scope",
            f"This API key needs the `{scope}` scope for this request.",
        )
