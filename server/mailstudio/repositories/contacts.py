"""Contacts, their tags, and the batch loading that turns rows into API objects."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..db import now
from ..errors import ProblemError
from ..ids import new_id
from ..schemas import FieldValue


def _placeholders(values: list[Any]) -> str:
    return ",".join("?" * len(values))


def hydrate(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    """Build full contact objects for a batch of rows with three extra queries,
    not three per contact."""
    if not rows:
        return []
    ids = [row["id"] for row in rows]
    emails = [row["email"] for row in rows]
    marks = _placeholders(ids)

    tags: dict[str, list[str]] = {}
    for r in conn.execute(
        f"SELECT contact_id, tag FROM contact_tags WHERE contact_id IN ({marks}) ORDER BY tag", ids
    ):
        tags.setdefault(r["contact_id"], []).append(r["tag"])

    lists: dict[str, list[dict[str, str]]] = {}
    for r in conn.execute(
        f"SELECT contact_id, list_id, status FROM list_members"
        f" WHERE contact_id IN ({marks}) ORDER BY list_id",
        ids,
    ):
        lists.setdefault(r["contact_id"], []).append({"list_id": r["list_id"], "status": r["status"]})

    suppressed = {
        r["email"]: r["reason"]
        for r in conn.execute(
            f"SELECT email, reason FROM suppressions WHERE email IN ({_placeholders(emails)})", emails
        )
    }

    return [
        {
            "id": row["id"],
            "email": row["email"],
            "first_name": row["first_name"],
            "last_name": row["last_name"],
            "fields": json.loads(row["fields"]),
            "tags": tags.get(row["id"], []),
            "lists": lists.get(row["id"], []),
            "suppressed": suppressed.get(row["email"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


def get(conn: sqlite3.Connection, contact_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    return hydrate(conn, [row])[0] if row else None


def id_for_email(conn: sqlite3.Connection, email: str) -> str | None:
    row = conn.execute("SELECT id FROM contacts WHERE email = ?", (email,)).fetchone()
    return row["id"] if row else None


def _email_taken(email: str, existing_id: str) -> ProblemError:
    return ProblemError(
        409,
        "email-exists",
        "Email already in use",
        f"A contact with {email} already exists.",
        contact_id=existing_id,
    )


def create(
    conn: sqlite3.Connection,
    *,
    email: str,
    first_name: str = "",
    last_name: str = "",
    fields: dict[str, FieldValue] | None = None,
    tags: list[str] | None = None,
) -> str:
    existing = id_for_email(conn, email)
    if existing:
        raise _email_taken(email, existing)
    contact_id = new_id("contact")
    stamp = now()
    conn.execute(
        "INSERT INTO contacts (id, email, first_name, last_name, fields, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (contact_id, email, first_name, last_name, json.dumps(fields or {}), stamp, stamp),
    )
    if tags:
        add_tags(conn, contact_id, tags)
    return contact_id


def update(conn: sqlite3.Connection, contact_id: str, changes: dict[str, Any]) -> None:
    """Apply changes to email, first_name, last_name, fields (replaced) and tags (replaced)."""
    columns: dict[str, Any] = {}
    if "email" in changes:
        existing = id_for_email(conn, changes["email"])
        if existing and existing != contact_id:
            raise _email_taken(changes["email"], existing)
        columns["email"] = changes["email"]
    for name in ("first_name", "last_name"):
        if name in changes:
            columns[name] = changes[name]
    if "fields" in changes:
        columns["fields"] = json.dumps(changes["fields"])
    columns["updated_at"] = now()

    assignments = ", ".join(f"{name} = ?" for name in columns)  # names come from the fixed set above
    conn.execute(f"UPDATE contacts SET {assignments} WHERE id = ?", (*columns.values(), contact_id))

    if "tags" in changes:
        conn.execute("DELETE FROM contact_tags WHERE contact_id = ?", (contact_id,))
        add_tags(conn, contact_id, changes["tags"])


def merge(
    conn: sqlite3.Connection,
    contact_id: str,
    *,
    first_name: str,
    last_name: str,
    fields: dict[str, FieldValue],
) -> bool:
    """Import-style update: fill in non-empty values, merging custom fields.
    Returns True if anything changed."""
    row = conn.execute(
        "SELECT first_name, last_name, fields FROM contacts WHERE id = ?", (contact_id,)
    ).fetchone()
    merged = {**json.loads(row["fields"]), **fields}
    new = (first_name or row["first_name"], last_name or row["last_name"], json.dumps(merged))
    if new == (row["first_name"], row["last_name"], json.dumps(json.loads(row["fields"]))):
        return False
    conn.execute(
        "UPDATE contacts SET first_name = ?, last_name = ?, fields = ?, updated_at = ? WHERE id = ?",
        (*new, now(), contact_id),
    )
    return True


def add_tags(conn: sqlite3.Connection, contact_id: str, tags: list[str]) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO contact_tags (contact_id, tag) VALUES (?, ?)", [(contact_id, t) for t in tags]
    )


def delete(conn: sqlite3.Connection, contact_id: str) -> bool:
    """Erase a contact with their memberships, tags and consent history.
    Any suppression of their address is kept, so they're never emailed again."""
    return conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,)).rowcount > 0


def page(
    conn: sqlite3.Connection,
    *,
    after: str | None,
    limit: int,
    list_id: str | None = None,
    status: str | None = None,
    tag: str | None = None,
    email: str | None = None,
    search: str | None = None,
) -> list[dict[str, Any]]:
    """One page of contacts in ID order, fetching limit + 1 rows to detect a next page."""
    where: list[str] = []
    params: list[Any] = []
    if search:
        like = "%" + search.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append(
            "(c.email LIKE ? ESCAPE '\\' OR lower(c.first_name || ' ' || c.last_name) LIKE ? ESCAPE '\\')"
        )
        params += [like, like]
    if after:
        where.append("c.id > ?")
        params.append(after)
    if email:
        where.append("c.email = ?")
        params.append(email)
    if tag:
        where.append("EXISTS (SELECT 1 FROM contact_tags t WHERE t.contact_id = c.id AND t.tag = ?)")
        params.append(tag)
    if list_id:
        member = "SELECT 1 FROM list_members m WHERE m.contact_id = c.id AND m.list_id = ?"
        params.append(list_id)
        if status:
            member += " AND m.status = ?"
            params.append(status)
        where.append(f"EXISTS ({member})")

    sql = "SELECT c.* FROM contacts c"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY c.id LIMIT ?"
    params.append(limit + 1)
    return hydrate(conn, conn.execute(sql, params).fetchall())


def history(conn: sqlite3.Connection, contact_id: str) -> list[dict[str, Any]]:
    """Consent events, newest first, with the list's name where it still exists."""
    rows = conn.execute(
        "SELECT e.action, e.source, e.ip, e.created_at, e.list_id, l.name AS list_name"
        " FROM consent_events e LEFT JOIN lists l ON l.id = e.list_id"
        " WHERE e.contact_id = ? ORDER BY e.created_at DESC, e.id DESC LIMIT 500",
        (contact_id,),
    ).fetchall()
    return [dict(r) for r in rows]
