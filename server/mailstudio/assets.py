"""Image uploads, prepared for email.

Mail apps download every image on open, often over mobile data, and some cap
message size. So uploads are resized to at most 1200 px wide (sharp on high-density
screens at the usual 600 px email width) and recompressed: JPEG for photos, PNG
when there's transparency. Animated GIFs are kept as they are if they're small.
WebP and other formats are converted, since many mail apps can't show them.
"""

from __future__ import annotations

import io
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from .db import now
from .errors import ProblemError
from .ids import new_id

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_WIDTH = 1200
MAX_PIXELS = 40_000_000  # refuse decompression bombs
MAX_ANIMATED_GIF = 2 * 1024 * 1024
JPEG_QUALITY = 82
_ACCEPTED = {"JPEG", "PNG", "GIF", "WEBP", "BMP", "TIFF"}


@dataclass(frozen=True, slots=True)
class Prepared:
    data: bytes
    extension: str
    content_type: str
    width: int
    height: int


def _bad(detail: str) -> ProblemError:
    return ProblemError(422, "bad-image", "That image can't be used", detail)


def prepare(data: bytes) -> Prepared:
    if len(data) > MAX_UPLOAD_BYTES:
        raise ProblemError(413, "image-too-large", "Image too large", "Upload images up to 15 MB.")
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        opened = Image.open(io.BytesIO(data))
        opened.load()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise _bad("Upload a JPEG, PNG, GIF or WebP image.") from exc
    if opened.format not in _ACCEPTED:
        raise _bad("Upload a JPEG, PNG, GIF or WebP image.")

    if opened.format == "GIF" and getattr(opened, "is_animated", False):
        if len(data) > MAX_ANIMATED_GIF:
            raise _bad("Animated GIFs must be under 2 MB, because many people read email on mobile data.")
        return Prepared(data, "gif", "image/gif", opened.width, opened.height)

    image: Image.Image = ImageOps.exif_transpose(opened) or opened  # respect phone camera rotation
    if image.width > MAX_WIDTH:
        height = round(image.height * MAX_WIDTH / image.width)
        image = image.resize((MAX_WIDTH, max(1, height)), Image.Resampling.LANCZOS)

    has_alpha = image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info)
    out = io.BytesIO()
    if has_alpha:
        image.convert("RGBA").save(out, "PNG", optimize=True)
        return Prepared(out.getvalue(), "png", "image/png", image.width, image.height)
    image.convert("RGB").save(out, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    return Prepared(out.getvalue(), "jpg", "image/jpeg", image.width, image.height)


def store(conn: sqlite3.Connection, folder: Path, prepared: Prepared, original_name: str) -> dict[str, Any]:
    asset_id = new_id("asset")
    file_name = f"{asset_id}.{prepared.extension}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / file_name).write_bytes(prepared.data)
    conn.execute(
        "INSERT INTO assets (id, file_name, original_name, content_type, bytes, width, height, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            asset_id,
            file_name,
            original_name[:200],
            prepared.content_type,
            len(prepared.data),
            prepared.width,
            prepared.height,
            now(),
        ),
    )
    return get(conn, asset_id) or {}


def get(conn: sqlite3.Connection, asset_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    return dict(row) if row else None
