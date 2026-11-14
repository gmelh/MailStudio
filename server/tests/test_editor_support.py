from __future__ import annotations

import io
import re
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image

from mailstudio.config import Settings

Headers = dict[str, str]


def render(client: TestClient, auth: Headers, sections: list[dict[str, Any]], **extra: Any) -> str:
    body = {"document": {"schema": 2, "sections": sections, **extra.pop("document", {})}, **extra}
    response = client.post("/api/v1/render", json=body, headers=auth)
    assert response.status_code == 200, response.text
    return str(response.json()["html"])


def image_bytes(size: tuple[int, int], mode: str = "RGB", fmt: str = "JPEG") -> bytes:
    out = io.BytesIO()
    Image.new(mode, size, (200, 80, 40, 128) if mode == "RGBA" else (200, 80, 40)).save(out, fmt)
    return out.getvalue()


def test_sections_and_presets_are_served(client: TestClient, auth: Headers) -> None:
    library = client.get("/api/v1/sections", headers=auth).json()
    assert len(library["sections"]) == 14 and library["sections"]["header"]["label"] == "Header"
    assert set(library["presets"]) == {"modern", "editorial", "minimal"}


def test_text_formatting(client: TestClient, auth: Headers) -> None:
    body = "Hi **there**, *really*.\n\n- one\n- [two](https://example.com/x)\n\n[nope](javascript:alert(1))"
    html = render(client, auth, [{"id": "t", "type": "text", "settings": {"body": body}}])
    assert "<strong>there</strong>" in html and "<em>really</em>" in html
    assert re.search(r'<li><a class="ms-link" href="https://example.com/x"[^>]*>two</a></li>', html)
    assert "javascript:" not in html.replace(
        "[nope](javascript:alert(1", ""
    )  # left as plain text, not a link
    assert 'href="javascript' not in html


def test_dark_mode_styles_and_dark_preview(client: TestClient, auth: Headers) -> None:
    brand = {
        "dark": {
            "outer": "#010203",
            "background": "#0a0b0c",
            "featured": "#111213",
            "text": "#dddddd",
            "heading": "#eeeeee",
            "link": "#99bbff",
            "accent": "#7788ff",
            "buttonText": "#000000",
        }
    }
    sections = [{"id": "h", "type": "hero", "settings": {}}]
    light = render(client, auth, sections, document={"brand": brand})
    assert "@media (prefers-color-scheme: dark)" in light and "#0a0b0c !important" in light
    assert "[data-ogsc] .ms-text div" in light  # Outlook's dark mode
    assert '<meta name="color-scheme" content="light dark">' in light

    dark = render(client, auth, sections, document={"brand": brand}, scheme="dark")
    assert "prefers-color-scheme: dark)" not in dark and "background-color:#111213" in dark


def test_sections_are_marked_for_click_to_select(client: TestClient, auth: Headers) -> None:
    html = render(
        client,
        auth,
        [{"id": "s1a", "type": "text", "settings": {}}, {"id": "bad id!", "type": "hero", "settings": {}}],
    )
    assert "ms-s-s1a" in html and "ms-s-bad-id-" in html


def test_button_corners_follow_the_brand(client: TestClient, auth: Headers) -> None:
    html = render(
        client, auth, [{"id": "b", "type": "button", "settings": {}}], document={"brand": {"radius": 0}}
    )
    assert "border-radius:1px" in html


def test_image_upload_is_resized_compressed_and_public(
    settings: Settings, client: TestClient, auth: Headers
) -> None:
    big = client.post(
        "/api/v1/assets", files={"file": ("photo.jpg", image_bytes((2400, 1600)), "image/jpeg")}, headers=auth
    )
    assert big.status_code == 201, big.text
    photo = big.json()
    assert (photo["width"], photo["height"], photo["content_type"]) == (1200, 800, "image/jpeg")
    assert photo["url"].startswith(settings.base_url + "/assets/ast_") and photo["url"].endswith(".jpg")

    logo = client.post(
        "/api/v1/assets",
        files={"file": ("logo.png", image_bytes((300, 100), "RGBA", "PNG"), "image/png")},
        headers=auth,
    ).json()
    assert logo["content_type"] == "image/png"  # transparency kept

    served = client.get(photo["url"].replace(settings.base_url, ""))  # no login needed
    assert served.status_code == 200 and "immutable" in served.headers["cache-control"]
    assert Image.open(io.BytesIO(served.content)).size == (1200, 800)

    listed = client.get("/api/v1/assets", headers=auth).json()["data"]
    assert [a["id"] for a in listed] == [logo["id"], photo["id"]]  # newest first

    assert client.delete(f"/api/v1/assets/{photo['id']}", headers=auth).status_code == 204
    assert client.get(photo["url"].replace(settings.base_url, "")).status_code == 404


def test_non_images_are_refused(client: TestClient, auth: Headers) -> None:
    response = client.post(
        "/api/v1/assets", files={"file": ("x.png", b"<svg onload=alert(1)>", "image/png")}, headers=auth
    )
    assert response.status_code == 422
    assert client.get("/assets/..%2F..%2Fdata%2Fmailstudio.db").status_code == 404
    assert client.get("/assets/ast_00000000000000000000000000.jpg").status_code == 404
