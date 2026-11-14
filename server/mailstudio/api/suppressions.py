"""/api/v1/suppressions

Bounces and complaints are added automatically by the sending pipeline. This API
lets owners add addresses by hand (for example "never email me again" requests)
and inspect the list. Only manual suppressions can be removed here: lifting a
bounce or complaint suppression risks the sender's reputation.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse

from ..deps import Db, IdempotencyKey, Paging, ReadKey, WriteKey, fingerprint, run_write
from ..errors import ProblemError, not_found
from ..repositories import suppressions
from ..schemas import Suppression, SuppressionCreate, SuppressionPage, normalise_email

router = APIRouter(prefix="/suppressions", tags=["Suppressions"])


@router.get("", response_model=SuppressionPage, summary="List suppressed addresses")
def list_suppressions(
    conn: Db,
    _: ReadKey,
    page: Paging,
    reason: Annotated[Literal["bounce", "complaint", "manual"] | None, Query()] = None,
) -> dict[str, Any]:
    return page.result(suppressions.page(conn, after=page.after, limit=page.limit, reason=reason))


@router.post("", response_model=Suppression, status_code=201, summary="Suppress an address")
def create_suppression(
    body: SuppressionCreate, request: Request, conn: Db, key: WriteKey, idempotency_key: IdempotencyKey = None
) -> JSONResponse:
    email = normalise_email(body.email)

    def action() -> tuple[int, Any]:
        created = suppressions.add(conn, email, "manual", body.detail)
        return (201 if created else 200), suppressions.get(conn, email)

    return run_write(conn, key, idempotency_key, fingerprint(request, body), action)


@router.get("/{email}", response_model=Suppression, summary="Check an address")
def get_suppression(email: str, conn: Db, _: ReadKey) -> dict[str, Any]:
    found = suppressions.get(conn, normalise_email(email))
    if found is None:
        raise not_found("Suppression", email)
    return found


@router.delete("/{email}", status_code=204, summary="Lift a manual suppression")
def delete_suppression(email: str, request: Request, conn: Db, key: WriteKey) -> Response:
    address = normalise_email(email)

    def action() -> tuple[int, Any]:
        found = suppressions.get(conn, address)
        if found is None:
            raise not_found("Suppression", email)
        if found["reason"] != "manual":
            raise ProblemError(
                409,
                "suppression-locked",
                "Can't lift this suppression",
                f"{address} is suppressed because of a {found['reason']}. "
                "Only manual suppressions can be lifted.",
            )
        suppressions.remove(conn, address)
        return 204, None

    run_write(conn, key, None, fingerprint(request, None), action)
    return Response(status_code=204)
