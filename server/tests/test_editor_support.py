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
    assert len(library["sections"]) == 19 and library["sections"]["header"]["label"] == "Header"
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


def test_plain_feature_sections_keep_their_own_colours(client: TestClient, auth: Headers) -> None:
    """The Minimal style switches feature backgrounds off without overwriting the feature
    colours, so switching to another style brings them back."""
    brand = {
        "light": {"background": "#ffffff", "featured": "#abcdef"},
        "dark": {"background": "#0a0b0c", "featured": "#111213"},
    }
    sections = [{"id": "h", "type": "hero", "settings": {}}]

    tinted = render(client, auth, sections, document={"brand": brand})
    assert "background-color:#abcdef" in tinted and "#111213 !important" in tinted

    flat = render(client, auth, sections, document={"brand": {**brand, "flatFeatured": True}})
    assert "#abcdef" not in flat and "#111213" not in flat
    assert ".ms-featured, .ms-featured > table{background:#0a0b0c !important" in flat.replace(" {", "{")

    dark_preview = render(
        client, auth, sections, document={"brand": {**brand, "flatFeatured": True}}, scheme="dark"
    )
    assert "#111213" not in dark_preview


def test_presets_say_whether_feature_sections_are_plain(client: TestClient, auth: Headers) -> None:
    presets = client.get("/api/v1/sections", headers=auth).json()["presets"]
    assert {k: p["brand"]["flatFeatured"] for k, p in presets.items()} == {
        "modern": False,
        "editorial": False,
        "minimal": True,
    }


def test_campaigns_from_the_old_minimal_style_read_as_plain(client: TestClient, auth: Headers) -> None:
    """The old Minimal copied the background over the feature colour; those documents now
    say flatFeatured, so the editor shows the truth and can bring feature colours back."""
    old_minimal = {
        "light": {"background": "#ffffff", "featured": "#FFFFFF"},
        "dark": {"background": "#0a0b0c", "featured": "#0a0b0c"},
    }
    created = client.post(
        "/api/v1/campaigns",
        json={"name": "Old", "document": {"schema": 2, "brand": old_minimal}},
        headers=auth,
    )
    assert created.status_code == 201, created.text
    assert created.json()["document"]["brand"]["flatFeatured"] is True

    ordinary = client.post("/api/v1/campaigns", json={"name": "New"}, headers=auth).json()
    assert ordinary["document"]["brand"]["flatFeatured"] is False
    only_light_same = {**old_minimal, "dark": {"background": "#0a0b0c", "featured": "#111213"}}
    response = client.post(
        "/api/v1/campaigns",
        json={"name": "Chosen", "document": {"schema": 2, "brand": only_light_same}},
        headers=auth,
    )
    assert response.json()["document"]["brand"]["flatFeatured"] is False


def test_every_section_can_have_a_feature_background(client: TestClient, auth: Headers) -> None:
    """Each section has a Feature background toggle. The sections that always had one
    default to on, so existing campaigns (which don't store it) look the same."""
    library = client.get("/api/v1/sections", headers=auth).json()["sections"]
    defaults_on = {"hero", "offer", "imageText", "footer", "event"}
    for type_, definition in library.items():
        assert any(f["key"] == "featured" and f["kind"] == "toggle" for f in definition["fields"]), type_
        assert definition["defaults"]["featured"] == ("on" if type_ in defaults_on else "off"), type_

    brand = {"brand": {"light": {"featured": "#abcdef"}}}

    def featured(section: dict[str, Any]) -> bool:
        html = render(client, auth, [{"id": "x", **section}], document=brand)
        tag = re.search(r'<div class="ms-s-x[^"]*"[^>]*>', html)
        assert tag, html
        return "ms-featured" in tag.group(0) and "#abcdef" in tag.group(0)

    assert featured({"type": "hero", "settings": {}})  # stored before the toggle existed
    assert not featured({"type": "hero", "settings": {"featured": "off"}})
    assert not featured({"type": "text", "settings": {}})
    assert featured({"type": "text", "settings": {"featured": "on"}})
    for position in ("above", "beside"):  # the header has a separate layout for each
        assert featured({"type": "header", "settings": {"featured": "on", "imagePosition": position}})


def test_feature_background_toggle_takes_on_or_off(client: TestClient, auth: Headers) -> None:
    body = {
        "document": {"schema": 2, "sections": [{"id": "x", "type": "text", "settings": {"featured": "yes"}}]}
    }
    response = client.post("/api/v1/render", json=body, headers=auth)
    assert response.status_code == 422
    assert response.json()["errors"][0]["location"] == "document.sections.0.settings.featured"


def test_header_image_can_stretch_to_the_edges(client: TestClient, auth: Headers) -> None:
    """Stretched, the header is just the image: full width, no padding, no name."""
    logo = "https://example.com/logo.png"

    def header(settings: dict[str, str]) -> str:
        return render(
            client,
            auth,
            [{"id": "h", "type": "header", "settings": settings}],
            document={"brand": {"padding": 30}},
        )

    def section_padding(html: str) -> str:
        found = re.search(
            r'<div class="ms-s-h[^"]*"[^>]*>.*?<td[^>]*style="[^"]*padding:([^;]+);', html, re.S
        )
        assert found, html
        return found.group(1)

    normal = header({"imageUrl": logo})
    assert section_padding(normal) == "25px 30px" and 'class="ms-title"' in normal and 'width="240"' in normal

    for position in ("above", "below", "beside"):
        stretched = header(
            {"imageUrl": logo, "imageStretch": "on", "imagePosition": position, "imageWidth": "100"}
        )
        assert section_padding(stretched) == "0", position
        assert 'width="600"' in stretched and 'width="100"' not in stretched, (
            position
        )  # the email's full width
        assert re.search(
            r'padding:0;[^"]*"><table[^>]*><tbody><tr><td style="width:600px;"><img', stretched
        ), position
        assert 'class="ms-title"' not in stretched, position  # no name, just the image
        assert 'alt="YOUR COMPANY"' in stretched, position  # which is described by the brand name

    # Without an image there's nothing to stretch, so the name shows as usual.
    no_image = header({"imageStretch": "on"})
    assert section_padding(no_image) == "25px 30px" and "YOUR COMPANY" in no_image
