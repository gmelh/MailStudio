"""The suppression list: addresses that must never receive marketing email."""

from __future__ import annotations

import sqlite3
from typing import Any, Literal

from ..db import now

Reason = Literal["bounce", "complaint", "manual"]


def _as_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["email"], **dict(row)}


def get(conn: sqlite3.Connection, email: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM suppressions WHERE email = ?", (email,)).fetchone()
    return _as_dict(row) if row else None


def is_suppressed(conn: sqlite3.Connection, email: str) -> bool:
    return conn.execute("SELECT 1 FROM suppressions WHERE email = ?", (email,)).fetchone() is not None


def add(conn: sqlite3.Connection, email: str, reason: Reason, detail: str = "") -> bool:
    """Suppress an address. Returns False if it was already suppressed (the
    original reason is kept, since it records why sending first stopped)."""
    return (
        conn.execute(
            "INSERT OR IGNORE INTO suppressions (email, reason, detail, created_at) VALUES (?, ?, ?, ?)",
            (email, reason, detail, now()),
        ).rowcount
        > 0
    )


def remove(conn: sqlite3.Connection, email: str) -> bool:
    return conn.execute("DELETE FROM suppressions WHERE email = ?", (email,)).rowcount > 0


def page(
    conn: sqlite3.Connection, *, after: str | None, limit: int, reason: Reason | None = None
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM suppressions WHERE email > ?"
    params: list[Any] = [after or ""]
    if reason:
        sql += " AND reason = ?"
        params.append(reason)
    sql += " ORDER BY email LIMIT ?"
    params.append(limit + 1)
    return [_as_dict(r) for r in conn.execute(sql, params)]
