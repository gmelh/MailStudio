"""Mailing lists, memberships and the consent trail that goes with them."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from ..db import now
from ..errors import ProblemError
from ..ids import new_id
from ..schemas import ConsentIn

SubscribeOutcome = Literal["added", "already_subscribed", "skipped_unsubscribed"]


def record_consent(
    conn: sqlite3.Connection,
    contact_id: str,
    list_id: str | None,
    action: Literal["consent", "confirm", "unsubscribe"],
    source: str,
    ip: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO consent_events (id, contact_id, list_id, action, source, ip, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (new_id("consent"), contact_id, list_id, action, source, ip, now()),
    )


def _counts(conn: sqlite3.Connection, list_ids: list[str]) -> dict[str, dict[str, int]]:
    counts = {i: {"subscribed": 0, "pending": 0, "unsubscribed": 0} for i in list_ids}
    if list_ids:
        marks = ",".join("?" * len(list_ids))
        for r in conn.execute(
            f"SELECT list_id, status, COUNT(*) AS n FROM list_members WHERE list_id IN ({marks})"
            " GROUP BY list_id, status",
            list_ids,
        ):
            counts[r["list_id"]][r["status"]] = r["n"]
    return counts


def _hydrate(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    counts = _counts(conn, [r["id"] for r in rows])
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "description": r["description"],
            "double_opt_in": bool(r["double_opt_in"]),
            "signup": json.loads(r["signup"] or "{}"),
            "counts": counts[r["id"]],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]


def _name_taken(name: str) -> ProblemError:
    return ProblemError(
        409, "list-name-exists", "List name already in use", f"A list named {name!r} already exists."
    )


def get(conn: sqlite3.Connection, list_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM lists WHERE id = ?", (list_id,)).fetchone()
    return _hydrate(conn, [row])[0] if row else None


def existing_ids(conn: sqlite3.Connection, list_ids: list[str]) -> set[str]:
    if not list_ids:
        return set()
    marks = ",".join("?" * len(list_ids))
    return {r["id"] for r in conn.execute(f"SELECT id FROM lists WHERE id IN ({marks})", list_ids)}


def create(conn: sqlite3.Connection, *, name: str, description: str, double_opt_in: bool) -> str:
    list_id = new_id("list")
    stamp = now()
    try:
        conn.execute(
            "INSERT INTO lists (id, name, description, double_opt_in, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (list_id, name, description, int(double_opt_in), stamp, stamp),
        )
    except sqlite3.IntegrityError:
        raise _name_taken(name) from None
    return list_id


def update(conn: sqlite3.Connection, list_id: str, changes: dict[str, Any]) -> None:
    columns: dict[str, Any] = {k: changes[k] for k in ("name", "description") if k in changes}
    if "double_opt_in" in changes:
        columns["double_opt_in"] = int(changes["double_opt_in"])
    if "signup" in changes:
        columns["signup"] = json.dumps(changes["signup"])
    columns["updated_at"] = now()
    assignments = ", ".join(f"{k} = ?" for k in columns)  # keys come from the fixed set above
    try:
        conn.execute(f"UPDATE lists SET {assignments} WHERE id = ?", (*columns.values(), list_id))
    except sqlite3.IntegrityError:
        raise _name_taken(changes["name"]) from None


def delete(conn: sqlite3.Connection, list_id: str) -> bool:
    return conn.execute("DELETE FROM lists WHERE id = ?", (list_id,)).rowcount > 0


def page(conn: sqlite3.Connection, *, after: str | None, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM lists WHERE id > ? ORDER BY id LIMIT ?", (after or "", limit + 1)
    ).fetchall()
    return _hydrate(conn, rows)


def subscribe(
    conn: sqlite3.Connection,
    list_id: str,
    contact_id: str,
    consent: ConsentIn,
    *,
    resubscribe: bool = False,
) -> SubscribeOutcome:
    """Subscribe a contact, recording consent. Someone who unsubscribed is left
    alone unless `resubscribe` is set, so an import can never quietly re-add them."""
    row = conn.execute(
        "SELECT status FROM list_members WHERE list_id = ? AND contact_id = ?", (list_id, contact_id)
    ).fetchone()
    if row and row["status"] == "subscribed":
        return "already_subscribed"
    if row and row["status"] == "unsubscribed" and not resubscribe:
        return "skipped_unsubscribed"

    stamp = now()
    if row:
        conn.execute(
            "UPDATE list_members SET status = 'subscribed', subscribed_at = ?, unsubscribed_at = NULL,"
            " updated_at = ? WHERE list_id = ? AND contact_id = ?",
            (stamp, stamp, list_id, contact_id),
        )
    else:
        conn.execute(
            "INSERT INTO list_members (list_id, contact_id, status, subscribed_at, created_at, updated_at)"
            " VALUES (?, ?, 'subscribed', ?, ?, ?)",
            (list_id, contact_id, stamp, stamp, stamp),
        )
    record_consent(
        conn, contact_id, list_id, "consent", consent.source, str(consent.ip) if consent.ip else None
    )
    return "added"


CONFIRM_WITHIN = timedelta(days=14)
ConfirmOutcome = Literal["confirmed", "already_subscribed", "expired"]


def request_confirmation(
    conn: sqlite3.Connection, list_id: str, contact_id: str, source: str, ip: str | None
) -> Literal["pending", "already_subscribed"]:
    """Someone asked to join a double opt-in list: they wait as `pending` until they
    click the link in the confirmation email. Signing up again (even after
    unsubscribing) is their own fresh request, so it's allowed."""
    row = conn.execute(
        "SELECT status FROM list_members WHERE list_id = ? AND contact_id = ?", (list_id, contact_id)
    ).fetchone()
    if row and row["status"] == "subscribed":
        return "already_subscribed"
    stamp = now()
    conn.execute(
        "INSERT INTO list_members (list_id, contact_id, status, created_at, updated_at)"
        " VALUES (?, ?, 'pending', ?, ?)"
        " ON CONFLICT (list_id, contact_id)"
        " DO UPDATE SET status = 'pending', updated_at = excluded.updated_at",
        (list_id, contact_id, stamp, stamp),
    )
    record_consent(conn, contact_id, list_id, "consent", source, ip)
    return "pending"


def confirm(conn: sqlite3.Connection, list_id: str, contact_id: str, ip: str | None) -> ConfirmOutcome:
    """The person clicked the confirmation link. Links work for CONFIRM_WITHIN of the signup."""
    row = conn.execute(
        "SELECT status, updated_at FROM list_members WHERE list_id = ? AND contact_id = ?",
        (list_id, contact_id),
    ).fetchone()
    if row and row["status"] == "subscribed":
        return "already_subscribed"
    if row is None or row["status"] != "pending":
        return "expired"
    asked = datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00"))
    if datetime.now(UTC) - asked > CONFIRM_WITHIN:
        return "expired"
    stamp = now()
    conn.execute(
        "UPDATE list_members SET status = 'subscribed', subscribed_at = ?, unsubscribed_at = NULL,"
        " updated_at = ? WHERE list_id = ? AND contact_id = ?",
        (stamp, stamp, list_id, contact_id),
    )
    record_consent(conn, contact_id, list_id, "confirm", "Confirmation link in email", ip)
    return "confirmed"


def unsubscribe(conn: sqlite3.Connection, list_id: str, contact_id: str, source: str) -> bool:
    """Mark a member unsubscribed. Returns False if they weren't on the list."""
    stamp = now()
    changed = conn.execute(
        "UPDATE list_members SET status = 'unsubscribed', unsubscribed_at = ?, updated_at = ?"
        " WHERE list_id = ? AND contact_id = ? AND status != 'unsubscribed'",
        (stamp, stamp, list_id, contact_id),
    ).rowcount
    if changed:
        record_consent(conn, contact_id, list_id, "unsubscribe", source)
        return True
    return (
        conn.execute(
            "SELECT 1 FROM list_members WHERE list_id = ? AND contact_id = ?", (list_id, contact_id)
        ).fetchone()
        is not None
    )


def members_page(
    conn: sqlite3.Connection, list_id: str, *, status: str | None, after: str | None, limit: int
) -> list[dict[str, Any]]:
    params: list[Any] = [list_id, after or ""]
    sql = (
        "SELECT c.id, c.email, c.first_name, c.last_name, m.status, m.subscribed_at, m.unsubscribed_at"
        " FROM list_members m JOIN contacts c ON c.id = m.contact_id"
        " WHERE m.list_id = ? AND c.id > ?"
    )
    if status:
        sql += " AND m.status = ?"
        params.append(status)
    sql += " ORDER BY c.id LIMIT ?"
    params.append(limit + 1)
    return [dict(r) for r in conn.execute(sql, params)]
