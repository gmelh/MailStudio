"""Campaigns: stored design document, sender details and audience."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..db import now
from ..ids import new_id

EDITABLE = ("draft",)


def _marks(values: list[Any]) -> str:
    return ",".join("?" * len(values))


def audience_sql(list_ids: list[str], tags: list[str]) -> tuple[str, list[Any]]:
    """SELECT of the contacts a campaign would reach: subscribed to any of its
    lists, not suppressed, and (if tags are set) carrying at least one tag.
    Contacts are unique by address, so nobody appears twice."""
    sql = (
        "SELECT c.id, c.email, c.first_name, c.last_name, c.fields FROM contacts c"
        f" WHERE EXISTS (SELECT 1 FROM list_members m WHERE m.contact_id = c.id"
        f"   AND m.status = 'subscribed' AND m.list_id IN ({_marks(list_ids)}))"
        " AND NOT EXISTS (SELECT 1 FROM suppressions s WHERE s.email = c.email)"
    )
    params: list[Any] = list(list_ids)
    if tags:
        sql += (
            " AND EXISTS (SELECT 1 FROM contact_tags t"
            f" WHERE t.contact_id = c.id AND t.tag IN ({_marks(tags)}))"
        )
        params += tags
    return sql, params


def audience_count(conn: sqlite3.Connection, list_ids: list[str], tags: list[str]) -> int:
    if not list_ids:
        return 0
    sql, params = audience_sql(list_ids, tags)
    count: int = conn.execute(f"SELECT COUNT(*) FROM ({sql})", params).fetchone()[0]
    return count


def _targets(
    conn: sqlite3.Connection, campaign_ids: list[str]
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    lists: dict[str, list[str]] = {i: [] for i in campaign_ids}
    tags: dict[str, list[str]] = {i: [] for i in campaign_ids}
    marks = _marks(campaign_ids)
    for r in conn.execute(
        f"SELECT campaign_id, list_id FROM campaign_lists WHERE campaign_id IN ({marks}) ORDER BY list_id",
        campaign_ids,
    ):
        lists[r["campaign_id"]].append(r["list_id"])
    for r in conn.execute(
        f"SELECT campaign_id, tag FROM campaign_tags WHERE campaign_id IN ({marks}) ORDER BY tag",
        campaign_ids,
    ):
        tags[r["campaign_id"]].append(r["tag"])
    return lists, tags


def _hydrate(
    conn: sqlite3.Connection, rows: list[sqlite3.Row], *, with_document: bool
) -> list[dict[str, Any]]:
    if not rows:
        return []
    lists, tags = _targets(conn, [r["id"] for r in rows])
    out = []
    for r in rows:
        item = dict(r)
        document = item.pop("document")
        item.pop("snapshot", None)
        item["lists"] = lists[r["id"]]
        item["tags"] = tags[r["id"]]
        item["audience"] = audience_count(conn, item["lists"], item["tags"])
        if with_document:
            item["document"] = json.loads(document)
        out.append(item)
    return out


def get(conn: sqlite3.Connection, campaign_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
    return _hydrate(conn, [row], with_document=True)[0] if row else None


def page(
    conn: sqlite3.Connection, *, after: str | None, limit: int, status: str | None
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM campaigns WHERE id > ?"
    params: list[Any] = [after or ""]
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY id LIMIT ?"
    params.append(limit + 1)
    return _hydrate(conn, conn.execute(sql, params).fetchall(), with_document=False)


def _set_targets(
    conn: sqlite3.Connection, campaign_id: str, list_ids: list[str] | None, tags: list[str] | None
) -> None:
    if list_ids is not None:
        conn.execute("DELETE FROM campaign_lists WHERE campaign_id = ?", (campaign_id,))
        conn.executemany(
            "INSERT INTO campaign_lists (campaign_id, list_id) VALUES (?, ?)",
            [(campaign_id, i) for i in dict.fromkeys(list_ids)],
        )
    if tags is not None:
        conn.execute("DELETE FROM campaign_tags WHERE campaign_id = ?", (campaign_id,))
        conn.executemany(
            "INSERT INTO campaign_tags (campaign_id, tag) VALUES (?, ?)", [(campaign_id, t) for t in tags]
        )


_COLUMNS = ("name", "subject", "preheader", "from_name", "from_email", "reply_to")


def create(conn: sqlite3.Connection, values: dict[str, Any], document: dict[str, Any]) -> str:
    campaign_id = new_id("campaign")
    stamp = now()
    conn.execute(
        "INSERT INTO campaigns (id, name, subject, preheader, from_name, from_email, reply_to, document,"
        " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (campaign_id, *(values.get(c, "") or "" for c in _COLUMNS), json.dumps(document), stamp, stamp),
    )
    _set_targets(conn, campaign_id, values.get("lists", []), values.get("tags", []))
    return campaign_id


def update(
    conn: sqlite3.Connection, campaign_id: str, values: dict[str, Any], document: dict[str, Any] | None
) -> None:
    columns = {c: values[c] or "" for c in _COLUMNS if c in values}
    if document is not None:
        columns["document"] = json.dumps(document)
    columns["updated_at"] = now()
    assignments = ", ".join(f"{c} = ?" for c in columns)  # names come from the fixed set above
    conn.execute(f"UPDATE campaigns SET {assignments} WHERE id = ?", (*columns.values(), campaign_id))
    _set_targets(conn, campaign_id, values.get("lists"), values.get("tags"))


def delete(conn: sqlite3.Connection, campaign_id: str) -> None:
    conn.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))


def target_lists(conn: sqlite3.Connection, campaign_id: str) -> list[str]:
    return [
        r["list_id"]
        for r in conn.execute("SELECT list_id FROM campaign_lists WHERE campaign_id = ?", (campaign_id,))
    ]
