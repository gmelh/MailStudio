"""Social Links with real icons, coloured to suit the email and served by the install."""

from __future__ import annotations

import io
import re
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from mailstudio import social

Headers = dict[str, str]


def render(
    client: TestClient, auth: Headers, settings: dict[str, Any], brand: dict[str, Any] | None = None
) -> str:
    document = {
        "schema": 2,
        "sections": [{"id": "s", "type": "social", "settings": settings}],
        "brand": brand or {},
    }
    response = client.post(
        "/api/v1/render", json={"document": document, "subject": "Hi", "preheader": ""}, headers=auth
    )
    assert response.status_code == 200, response.text
    return str(response.json()["html"])


def icons(html: str) -> list[str]:
    return re.findall(r'src="http://localhost:8080/social/([^"]+)\.png"', html)


def test_only_filled_networks_show_as_icons(client: TestClient, auth: Headers) -> None:
    html = render(
        client, auth, {"instagram": "https://instagram.com/bakery", "tiktok": "https://tiktok.com/@bakery"}
    )
    assert icons(html) == ["instagram-circle-4768da", "tiktok-circle-4768da"]  # the brand accent by default
    assert re.search(r'<a href="https://instagram.com/bakery"[^>]*>\s*<img[^>]*alt="Instagram"', html)
    assert 'alt="TikTok"' in html and "Facebook" not in html


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({"colour": "network"}, ["instagram-circle-e4405f", "website-circle-4768da"]),
        ({"colour": "heading", "style": "plain"}, ["instagram-plain-273449", "website-plain-273449"]),
        (
            {"colour": "custom", "customColour": "#C0FFEE"},
            ["instagram-circle-c0ffee", "website-circle-c0ffee"],
        ),
    ],
)
def test_icon_colours(
    client: TestClient, auth: Headers, options: dict[str, str], expected: list[str]
) -> None:
    links = {"website": "https://bakery.example", "instagram": "https://instagram.com/bakery"}
    assert sorted(icons(render(client, auth, {**links, **options}))) == expected


def test_icon_size(client: TestClient, auth: Headers) -> None:
    html = render(client, auth, {"x": "https://x.com/bakery", "size": "40"})
    assert re.search(r'<img[^>]*src="[^"]*/social/x-[^"]*"[^>]*width="40"', html)


def test_colours_must_be_colours(client: TestClient, auth: Headers) -> None:
    body = {
        "document": {
            "schema": 2,
            "sections": [{"id": "s", "type": "social", "settings": {"customColour": "red"}}],
        },
        "subject": "Hi",
    }
    response = client.post("/api/v1/render", json=body, headers=auth)
    assert response.status_code == 422
    assert response.json()["errors"][0]["location"] == "document.sections.0.settings.customColour"


def test_icons_are_served_and_kept(client: TestClient, settings: Any) -> None:
    response = client.get("/social/linkedin-circle-0a66c2.png")
    assert response.status_code == 200 and response.headers["content-type"] == "image/png"
    assert "immutable" in response.headers["cache-control"]
    image = Image.open(io.BytesIO(response.content))
    assert image.size == (social.SIZE, social.SIZE) and image.mode == "RGBA"
    assert image.getpixel((social.SIZE // 2, 4))[:3] == (0x0A, 0x66, 0xC2)  # the circle, in that colour
    assert image.getpixel((0, 0))[3] == 0  # transparent corners
    assert (settings.data_dir / "social" / "linkedin-circle-0a66c2.png").is_file()  # made once, kept


@pytest.mark.parametrize(
    "name",
    [
        "linkedin-circle-0A66C2",
        "myspace-circle-000000",
        "x-square-000000",
        "x-circle-zzzzzz",
        "x-circle-000000-extra",
    ],
)
def test_unknown_icons_are_not_found(client: TestClient, name: str) -> None:
    assert client.get(f"/social/{name}.png").status_code == 404


def test_logo_contrast_on_light_colours() -> None:
    shape = Image.new("RGBA", (256, 256), (255, 255, 255, 0))
    shape.paste((255, 255, 255, 255), (64, 64, 192, 192))  # a white square as the "logo"
    dark_logo = social.draw(shape, "circle", "fff5cc")
    light_logo = social.draw(shape, "circle", "1877f2")
    middle = (social.SIZE // 2, social.SIZE // 2)
    assert sum(dark_logo.getpixel(middle)[:3]) < 200  # dark logo on a pale circle
    assert light_logo.getpixel(middle)[:3] == (255, 255, 255)  # white logo on a strong colour
