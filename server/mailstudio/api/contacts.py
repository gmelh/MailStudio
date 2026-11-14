"""/api/v1/contacts"""

from __future__ import annotations

import hashlib
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse

from .. import importer
from ..deps import Db, IdempotencyKey, Paging, ReadKey, WriteKey, fingerprint, run_write
from ..errors import ProblemError, not_found
from ..repositories import contacts, lists
from ..schemas import (
    ConsentEvent,
    ConsentIn,
    Contact,
    ContactCreate,
    ContactPage,
    ContactUpdate,
    ImportResult,
    normalise_email,
    normalise_tag,
)

router = APIRouter(prefix="/contacts", tags=["Contacts"])


def _require_lists(conn: Db, list_ids: list[str]) -> None:
    missing = sorted(set(list_ids) - lists.existing_ids(conn, list_ids))
    if missing:
        raise ProblemError(
            422, "unknown-lists", "Unknown lists", "Some list IDs don't exist.", list_ids=missing
        )


@router.get("", response_model=ContactPage, summary="List contacts")
def list_contacts(
    conn: Db,
    _: ReadKey,
    page: Paging,
    list_id: str | None = None,
    status: Annotated[Literal["pending", "subscribed", "unsubscribed"] | None, Query()] = None,
    tag: str | None = None,
    email: str | None = None,
    q: Annotated[str | None, Query(max_length=200, description="Search email addresses and names")] = None,
) -> dict[str, Any]:
    if status and not list_id:
        raise ProblemError(
            422, "status-needs-list", "Missing list_id", "Filter by `status` together with `list_id`."
        )
    rows = contacts.page(
        conn,
        after=page.after,
        limit=page.limit,
        list_id=list_id,
        status=status,
        tag=normalise_tag(tag) if tag else None,
        email=normalise_email(email) if email else None,
        search=q.strip() or None if q else None,
    )
    return page.result(rows)


@router.post("", response_model=Contact, status_code=201, summary="Create a contact")
def create_contact(
    body: ContactCreate, request: Request, conn: Db, key: WriteKey, idempotency_key: IdempotencyKey = None
) -> JSONResponse:
    if body.lists and body.consent is None:
        raise ProblemError(
            422,
            "consent-required",
            "Consent required",
            "Adding a contact to a list needs a `consent.source` saying how permission was given.",
        )

    def action() -> tuple[int, Any]:
        _require_lists(conn, body.lists)
        contact_id = contacts.create(
            conn,
            email=normalise_email(body.email),
            first_name=body.first_name,
            last_name=body.last_name,
            fields=body.fields,
            tags=body.tags,
        )
        for list_id in body.lists:
            assert body.consent is not None
            lists.subscribe(conn, list_id, contact_id, body.consent)
        return 201, contacts.get(conn, contact_id)

    return run_write(conn, key, idempotency_key, fingerprint(request, body), action)


@router.get("/{contact_id}", response_model=Contact, summary="Get a contact")
def get_contact(contact_id: str, conn: Db, _: ReadKey) -> dict[str, Any]:
    contact = contacts.get(conn, contact_id)
    if contact is None:
        raise not_found("Contact", contact_id)
    return contact


@router.get("/{contact_id}/history", response_model=list[ConsentEvent], summary="Consent history")
def contact_history(contact_id: str, conn: Db, _: ReadKey) -> list[dict[str, Any]]:
    """Every subscribe, confirm and unsubscribe for this contact, newest first: the record
    that shows each person agreed to hear from you."""
    if contacts.get(conn, contact_id) is None:
        raise not_found("Contact", contact_id)
    return contacts.history(conn, contact_id)


@router.patch("/{contact_id}", response_model=Contact, summary="Update a contact")
def update_contact(
    contact_id: str, body: ContactUpdate, request: Request, conn: Db, key: WriteKey
) -> JSONResponse:
    changes = body.model_dump(exclude_unset=True)
    if changes.get("email") is None:
        changes.pop("email", None)
    else:
        changes["email"] = normalise_email(changes["email"])
    for name in ("first_name", "last_name", "fields", "tags"):
        if name in changes and changes[name] is None:
            del changes[name]

    def action() -> tuple[int, Any]:
        if contacts.get(conn, contact_id) is None:
            raise not_found("Contact", contact_id)
        contacts.update(conn, contact_id, changes)
        return 200, contacts.get(conn, contact_id)

    return run_write(conn, key, None, fingerprint(request, body), action)


@router.delete("/{contact_id}", status_code=204, summary="Erase a contact")
def delete_contact(contact_id: str, request: Request, conn: Db, key: WriteKey) -> Response:
    """Deletes the contact, their list memberships, tags and consent history.
    If the address is suppressed, the suppression stays."""

    def action() -> tuple[int, Any]:
        if not contacts.delete(conn, contact_id):
            raise not_found("Contact", contact_id)
        return 204, None

    run_write(conn, key, None, fingerprint(request, None), action)
    return Response(status_code=204)


@router.post("/import", response_model=ImportResult, summary="Import contacts from CSV")
def import_contacts(
    request: Request,
    conn: Db,
    key: WriteKey,
    file: Annotated[UploadFile, File(description="CSV with an `email` column; other columns become fields")],
    consent_source: Annotated[
        str, Form(min_length=1, max_length=200, description="How these people agreed to receive email")
    ],
    list_id: Annotated[str | None, Form()] = None,
    tags: Annotated[str, Form(description="Comma-separated tags to add to every imported contact")] = "",
    idempotency_key: IdempotencyKey = None,
) -> JSONResponse:
    """Upserts contacts by email in one transaction. People who unsubscribed from the
    list, or whose address is suppressed, are counted and skipped, never re-added."""
    data = file.file.read(importer.MAX_BYTES + 1)
    try:
        tag_list = sorted({normalise_tag(t) for t in tags.split(",") if t.strip()})
    except ValueError as exc:
        raise ProblemError(422, "validation", "Invalid tags", str(exc)) from None
    consent = ConsentIn(source=f"CSV import ({file.filename or 'upload'}): {consent_source.strip()}"[:200])

    def action() -> tuple[int, Any]:
        if list_id:
            _require_lists(conn, [list_id])
        stats = importer.run(conn, data, consent=consent, list_id=list_id, tags=tag_list)
        return 200, stats.__dict__

    request_print = fingerprint(
        request,
        {
            "file": hashlib.sha256(data).hexdigest(),
            "list": list_id,
            "tags": tag_list,
            "source": consent.source,
        },
    )
    return run_write(conn, key, idempotency_key, request_print, action)
