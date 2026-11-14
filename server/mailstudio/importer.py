"""CSV contact import.

Columns: `email` (required), `first_name`, `last_name`, plus any others, which
become custom fields. Headers are matched case-insensitively and spaces become
underscores, so "First Name" works. Existing contacts are updated, never
duplicated. People who unsubscribed from the target list are never re-added.

The whole import runs in the caller's transaction: it applies completely or not at all.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from pydantic import EmailStr, TypeAdapter, ValidationError

from .errors import ProblemError
from .repositories import contacts, lists, suppressions
from .schemas import RESERVED_FIELDS, ConsentIn, ImportProblem, normalise_email, validate_fields

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 100_000
MAX_REPORTED_PROBLEMS = 100
_EMAIL = TypeAdapter(EmailStr)
_HEADER_ALIASES = {
    "e-mail": "email",
    "email_address": "email",
    "firstname": "first_name",
    "lastname": "last_name",
}


@dataclass
class ImportStats:
    rows: int = 0
    created: int = 0
    updated: int = 0
    added_to_list: int = 0
    skipped_unsubscribed: int = 0
    skipped_suppressed: int = 0
    invalid: int = 0
    problems: list[ImportProblem] = field(default_factory=list)

    def problem(self, line: int, error: str) -> None:
        self.invalid += 1
        if len(self.problems) < MAX_REPORTED_PROBLEMS:
            self.problems.append(ImportProblem(row=line, error=error))


def _header_key(raw: str) -> str:
    key = "_".join(raw.strip().lower().split())
    return _HEADER_ALIASES.get(key, key)


def _decode(data: bytes) -> str:
    if len(data) > MAX_BYTES:
        raise ProblemError(413, "file-too-large", "File too large", "CSV files can be at most 10 MB.")
    for encoding in ("utf-8-sig", "cp1252"):  # Excel on Windows still saves cp1252 by default
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ProblemError(422, "bad-encoding", "Unreadable file", "Save the CSV as UTF-8 and try again.")


def _reader(text: str) -> csv.DictReader[str]:
    try:
        dialect: type[csv.Dialect] = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise ProblemError(422, "empty-file", "Empty file", "The CSV has no header row.")
    reader.fieldnames = [_header_key(name) for name in reader.fieldnames]
    if "email" not in reader.fieldnames:
        raise ProblemError(422, "no-email-column", "No email column", "The CSV needs a column named `email`.")
    for name in reader.fieldnames:
        if name not in ("email", "first_name", "last_name") and name in RESERVED_FIELDS:
            raise ProblemError(422, "reserved-column", "Reserved column name", f"`{name}` can't be imported.")
    return reader


def run(
    conn: sqlite3.Connection,
    data: bytes,
    *,
    consent: ConsentIn,
    list_id: str | None,
    tags: list[str],
) -> ImportStats:
    reader = _reader(_decode(data))
    stats = ImportStats()

    for record in reader:
        line = reader.line_num
        stats.rows += 1
        if stats.rows > MAX_ROWS:
            raise ProblemError(
                413, "too-many-rows", "Too many rows", f"Import at most {MAX_ROWS:,} rows at a time."
            )
        try:
            email = normalise_email(str(_EMAIL.validate_python((record.get("email") or "").strip())))
        except ValidationError:
            stats.problem(line, f"Invalid email address: {record.get('email')!r}")
            continue

        values: dict[str, Any] = {
            key: (value or "").strip()
            for key, value in record.items()
            if key and key not in ("email", "first_name", "last_name") and (value or "").strip()
        }
        try:
            fields = validate_fields(values)
        except ValueError as exc:
            stats.problem(line, str(exc))
            continue

        first_name = (record.get("first_name") or "").strip()[:200]
        last_name = (record.get("last_name") or "").strip()[:200]
        contact_id = contacts.id_for_email(conn, email)
        if contact_id is None:
            contact_id = contacts.create(
                conn, email=email, first_name=first_name, last_name=last_name, fields=fields
            )
            stats.created += 1
        elif contacts.merge(conn, contact_id, first_name=first_name, last_name=last_name, fields=fields):
            stats.updated += 1
        if tags:
            contacts.add_tags(conn, contact_id, tags)

        if list_id:
            if suppressions.is_suppressed(conn, email):
                stats.skipped_suppressed += 1
                continue
            outcome = lists.subscribe(conn, list_id, contact_id, consent)
            if outcome == "added":
                stats.added_to_list += 1
            elif outcome == "skipped_unsubscribed":
                stats.skipped_unsubscribed += 1

    return stats
