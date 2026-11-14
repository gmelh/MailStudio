"""Request-level plumbing shared by every endpoint: database connection,
authentication, write transactions with idempotency, and cursor pagination."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import sqlite3
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import Cookie, Depends, Header, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from .accounts import SESSION_COOKIE, load_session
from .auth import ALL_SCOPES, ApiKey, Principal, authenticate, check_scope
from .config import Settings
from .db import connect, now, transaction
from .errors import ProblemError

IDEMPOTENCY_TTL = timedelta(hours=24)


def get_db(request: Request) -> Iterator[sqlite3.Connection]:
    settings: Settings = request.app.state.settings
    conn = connect(settings.database_path)
    try:
        yield conn
    finally:
        conn.close()


Db = Annotated[sqlite3.Connection, Depends(get_db)]


_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def session_principal(
    request: Request, conn: sqlite3.Connection, token: str | None, csrf: str | None
) -> Principal | None:
    """The logged-in person, or None. Changes made with a session cookie must also
    carry the session's CSRF token, so other websites can't act on someone's behalf."""
    if not token:
        return None
    session = load_session(conn, token)
    if session is None:
        return None
    if request.method not in _SAFE_METHODS and not (csrf and hmac.compare_digest(csrf, session.csrf_token)):
        raise ProblemError(403, "csrf", "Missing security token", "Reload the page and try again.")
    return Principal(session.user_id, session.name or session.email, ALL_SCOPES, "user", session.csrf_token)


def require(scope: str) -> Callable[..., ApiKey]:
    def dependency(
        request: Request,
        conn: Db,
        authorization: Annotated[str | None, Header()] = None,
        ms_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> ApiKey:
        if not authorization:
            person = session_principal(request, conn, ms_session, x_csrf_token)
            if person is not None:
                return person
        key = authenticate(conn, authorization)
        check_scope(key, scope)
        return key

    return dependency


def require_user(
    request: Request,
    conn: Db,
    ms_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> Principal:
    """For things only a logged-in person may do, such as managing logins."""
    person = session_principal(request, conn, ms_session, x_csrf_token)
    if person is None:
        raise ProblemError(
            401, "unauthorized", "Please log in", "This needs a logged-in user, not an API key."
        )
    return person


UserPrincipal = Annotated[Principal, Depends(require_user)]


ReadKey = Annotated[ApiKey, Depends(require("read"))]
WriteKey = Annotated[ApiKey, Depends(require("write"))]
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key", max_length=200)]


# ---------------------------------------------------------------------------
# Writes


def fingerprint(request: Request, payload: Any) -> str:
    raw = json.dumps([request.method, request.url.path, jsonable_encoder(payload)], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def run_write(
    conn: sqlite3.Connection,
    key: ApiKey,
    idempotency_key: str | None,
    request_fingerprint: str,
    action: Callable[[], tuple[int, Any]],
) -> JSONResponse:
    """Run `action` in one transaction and return its (status, body) as JSON.

    With an Idempotency-Key, a repeat of the same request within 24 hours returns
    the stored response instead of running again. Reusing the key for a different
    request is an error. Failed requests aren't stored, so they can be retried.
    """
    with transaction(conn):
        if idempotency_key:
            stored = conn.execute(
                "SELECT fingerprint, status_code, body FROM idempotency_keys"
                " WHERE api_key_id = ? AND key = ?",
                (key.id, idempotency_key),
            ).fetchone()
            if stored is not None:
                if stored["fingerprint"] != request_fingerprint:
                    raise ProblemError(
                        422,
                        "idempotency-key-reused",
                        "Idempotency key reused",
                        "This Idempotency-Key was already used for a different request.",
                    )
                response = JSONResponse(json.loads(stored["body"]), status_code=stored["status_code"])
                response.headers["Idempotent-Replayed"] = "true"
                return response

        status, body = action()
        encoded = jsonable_encoder(body)

        if idempotency_key:
            cutoff = (datetime.now(UTC) - IDEMPOTENCY_TTL).isoformat().replace("+00:00", "Z")
            conn.execute("DELETE FROM idempotency_keys WHERE created_at < ?", (cutoff,))
            conn.execute(
                "INSERT INTO idempotency_keys (api_key_id, key, fingerprint, status_code, body, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (key.id, idempotency_key, request_fingerprint, status, json.dumps(encoded), now()),
            )
    return JSONResponse(encoded, status_code=status)


# ---------------------------------------------------------------------------
# Pagination


class Page:
    """Cursor pagination over ID order. IDs are time-sortable, so pages are
    stable while new rows are being added."""

    def __init__(
        self,
        cursor: Annotated[str | None, Query(description="`next_cursor` from the previous page")] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> None:
        self.after = decode_cursor(cursor) if cursor else None
        self.limit = limit

    def result(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Build the response from up to limit + 1 fetched rows."""
        has_more = len(rows) > self.limit
        data = rows[: self.limit]
        return {"data": data, "next_cursor": encode_cursor(data[-1]["id"]) if has_more and data else None}


Paging = Annotated[Page, Depends()]


def encode_cursor(last_id: str) -> str:
    return base64.urlsafe_b64encode(last_id.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> str:
    try:
        return base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
    except (binascii.Error, UnicodeDecodeError):
        raise ProblemError(
            400, "bad-cursor", "Invalid cursor", "Use the `next_cursor` value as given."
        ) from None
