"""/api/v1/lists"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse

from ..deps import Db, IdempotencyKey, Paging, ReadKey, WriteKey, fingerprint, run_write
from ..errors import not_found
from ..repositories import contacts, lists
from ..schemas import (
    ListCreate,
    ListPage,
    ListUpdate,
    MailingList,
    MemberPage,
    MembersAdd,
    MembersAddResult,
    Unsubscribe,
)

router = APIRouter(prefix="/lists", tags=["Lists"])


def _require_list(conn: Db, list_id: str) -> None:
    if lists.get(conn, list_id) is None:
        raise not_found("List", list_id)


@router.get("", response_model=ListPage, summary="List mailing lists")
def list_lists(conn: Db, _: ReadKey, page: Paging) -> dict[str, Any]:
    return page.result(lists.page(conn, after=page.after, limit=page.limit))


@router.post("", response_model=MailingList, status_code=201, summary="Create a list")
def create_list(
    body: ListCreate, request: Request, conn: Db, key: WriteKey, idempotency_key: IdempotencyKey = None
) -> JSONResponse:
    def action() -> tuple[int, Any]:
        list_id = lists.create(
            conn, name=body.name, description=body.description, double_opt_in=body.double_opt_in
        )
        return 201, lists.get(conn, list_id)

    return run_write(conn, key, idempotency_key, fingerprint(request, body), action)


@router.get("/{list_id}", response_model=MailingList, summary="Get a list")
def get_list(list_id: str, conn: Db, _: ReadKey) -> dict[str, Any]:
    found = lists.get(conn, list_id)
    if found is None:
        raise not_found("List", list_id)
    return found


@router.patch("/{list_id}", response_model=MailingList, summary="Update a list")
def update_list(list_id: str, body: ListUpdate, request: Request, conn: Db, key: WriteKey) -> JSONResponse:
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}

    def action() -> tuple[int, Any]:
        _require_list(conn, list_id)
        lists.update(conn, list_id, changes)
        return 200, lists.get(conn, list_id)

    return run_write(conn, key, None, fingerprint(request, body), action)


@router.delete("/{list_id}", status_code=204, summary="Delete a list")
def delete_list(list_id: str, request: Request, conn: Db, key: WriteKey) -> Response:
    """Deletes the list and its memberships. Contacts themselves are kept."""

    def action() -> tuple[int, Any]:
        if not lists.delete(conn, list_id):
            raise not_found("List", list_id)
        return 204, None

    run_write(conn, key, None, fingerprint(request, None), action)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Members


@router.get("/{list_id}/members", response_model=MemberPage, summary="List members")
def list_members(
    list_id: str,
    conn: Db,
    _: ReadKey,
    page: Paging,
    status: Annotated[Literal["pending", "subscribed", "unsubscribed"] | None, Query()] = None,
) -> dict[str, Any]:
    _require_list(conn, list_id)
    return page.result(lists.members_page(conn, list_id, status=status, after=page.after, limit=page.limit))


@router.post("/{list_id}/members", response_model=MembersAddResult, summary="Subscribe contacts")
def add_members(
    list_id: str,
    body: MembersAdd,
    request: Request,
    conn: Db,
    key: WriteKey,
    idempotency_key: IdempotencyKey = None,
) -> JSONResponse:
    """Subscribes existing contacts, recording `consent` for each. Contacts who
    unsubscribed from this list are skipped unless `resubscribe` is true."""

    def action() -> tuple[int, Any]:
        _require_list(conn, list_id)
        result: dict[str, list[str]] = {
            "added": [],
            "already_subscribed": [],
            "skipped_unsubscribed": [],
            "not_found": [],
        }
        for contact_id in dict.fromkeys(body.contact_ids):  # de-duplicate, keep order
            if contacts.get(conn, contact_id) is None:
                result["not_found"].append(contact_id)
                continue
            outcome = lists.subscribe(conn, list_id, contact_id, body.consent, resubscribe=body.resubscribe)
            result[outcome].append(contact_id)
        return 200, result

    return run_write(conn, key, idempotency_key, fingerprint(request, body), action)


@router.post("/{list_id}/members/{contact_id}/unsubscribe", status_code=204, summary="Unsubscribe a contact")
def unsubscribe_member(
    list_id: str, contact_id: str, request: Request, conn: Db, key: WriteKey, body: Unsubscribe | None = None
) -> Response:
    """Marks the member unsubscribed and records it in their consent history.
    Unsubscribing someone who already unsubscribed succeeds without change."""
    source = body.source if body else "API"

    def action() -> tuple[int, Any]:
        _require_list(conn, list_id)
        if not lists.unsubscribe(conn, list_id, contact_id, source):
            raise not_found("List member", contact_id)
        return 204, None

    run_write(conn, key, None, fingerprint(request, body), action)
    return Response(status_code=204)
