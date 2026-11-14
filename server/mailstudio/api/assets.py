"""/api/v1/assets: uploaded images."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, File, Request, Response, UploadFile

from .. import assets
from ..config import Settings
from ..db import transaction
from ..deps import Db, Paging, ReadKey, WriteKey
from ..errors import not_found

router = APIRouter(prefix="/assets", tags=["Images"])


def _public(item: dict[str, Any], request: Request) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    return {
        "id": item["id"],
        "url": f"{settings.base_url}/assets/{item['file_name']}",
        "original_name": item["original_name"],
        "content_type": item["content_type"],
        "bytes": item["bytes"],
        "width": item["width"],
        "height": item["height"],
        "created_at": item["created_at"],
    }


@router.post("", status_code=201, summary="Upload an image")
def upload(
    request: Request,
    conn: Db,
    _: WriteKey,
    file: Annotated[UploadFile, File(description="JPEG, PNG, GIF or WebP, up to 15 MB")],
) -> dict[str, Any]:
    """Resized to at most 1200 px wide and compressed for email. Returns the public URL to use in emails."""
    prepared = assets.prepare(file.file.read(assets.MAX_UPLOAD_BYTES + 1))
    settings: Settings = request.app.state.settings
    with transaction(conn):
        item = assets.store(conn, settings.assets_dir, prepared, file.filename or "")
    return _public(item, request)


@router.get("", summary="List uploaded images")
def list_assets(request: Request, conn: Db, _: ReadKey, page: Paging) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT * FROM assets WHERE id < ? ORDER BY id DESC LIMIT ?",
        (page.after or "~", page.limit + 1),
    ).fetchall()
    return page.result([_public(dict(r), request) for r in rows])


@router.delete("/{asset_id}", status_code=204, summary="Delete an image")
def delete_asset(asset_id: str, request: Request, conn: Db, _: WriteKey) -> Response:
    """Emails already sent that show this image will show a broken image instead."""
    settings: Settings = request.app.state.settings
    with transaction(conn):
        item = assets.get(conn, asset_id)
        if item is None:
            raise not_found("Image", asset_id)
        conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
    (settings.assets_dir / item["file_name"]).unlink(missing_ok=True)
    return Response(status_code=204)
