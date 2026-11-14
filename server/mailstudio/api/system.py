"""/api/v1/health and /api/v1/me"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from .. import __version__
from ..deps import Db, ReadKey

router = APIRouter(tags=["System"])


@router.get("/health", summary="Health check")
def health(conn: Db) -> dict[str, Any]:
    """Unauthenticated. Used by Docker and uptime monitors."""
    conn.execute("SELECT 1").fetchone()
    return {"status": "ok", "version": __version__}


@router.get("/me", summary="Describe the current API key")
def me(key: ReadKey) -> dict[str, Any]:
    return {"id": key.id, "name": key.name, "scopes": sorted(key.scopes)}
