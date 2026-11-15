"""Product cards, Video, Event, and the coupon code on Offer."""

from __future__ import annotations

import re
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mailstudio.render import youtube_id

Headers = dict[str, str]
PHOTO = "https://cdn.example.com/rye.jpg"


def render(client: TestClient, auth: Headers, *sections: dict[str, Any]) -> str:
    body = {"document": {"schema": 2, "sections": list(sections)}, "subject": "Hi", "preheader": ""}
    response = client.post("/api/v1/render", json=body, headers=auth)
    assert response.status_code == 200, response.text
    return str(response.json()["html"])


def section(html: str, section_id: str) -> str:
    start = html.index(f'class="ms-s-{section_id}')
    rest = html[start + 10 :]
    following = re.search(r'class="ms-s-(?!' + re.escape(section_id) + r")|ms-compliance", rest)
    return rest[: following.start()] if following else rest


def test_product_cards(client: TestClient, auth: Headers) -> None:
    three = section(
        render(
            client,
            auth,
            {
                "id": "p",
                "type": "products",
                "settings": {"image1": PHOTO, "alt1": "Rye loaf", "price2": "", "link3": ""},
            },
        ),
        "p",
    )
    assert three.count("mj-column-per-33") == 3
    assert "Seeded rye" in three and "Spelt sourdough" in three and "Fruit loaf" in three
    assert 'alt="Rye loaf"' in three and re.search(
        r'<a href="https://example.com"[^>]*><img', three
    )  # linked
    assert "$9.50" in three and "$11.00" not in three  # empty price: none shown
    assert three.count(">Order now<") == 2  # the card without a link has no button
    assert three.count('class="mj-full-width-mobile"') >= 1  # photos fill the width on phones

    two = section(render(client, auth, {"id": "p", "type": "products", "settings": {"count": "2"}}), "p")
    assert two.count("mj-column-per-50") == 2 and "Fruit loaf" not in two


@pytest.mark.parametrize(
    ("url", "video"),
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtube.com/watch?feature=share&v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ?t=42", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://vimeo.com/76979871", None),
        ("https://www.youtube.com/channel/UC123", None),
    ],
)
def test_youtube_addresses(url: str, video: str | None) -> None:
    assert youtube_id(url) == video


def test_video(client: TestClient, auth: Headers) -> None:
    html = render(
        client,
        auth,
        {
            "id": "yt",
            "type": "video",
            "settings": {"url": "https://youtu.be/dQw4w9WgXcQ", "caption": "Our bakers at work"},
        },
        {
            "id": "vm",
            "type": "video",
            "settings": {"url": "https://vimeo.com/76979871", "thumbnail": PHOTO, "edge": "on"},
        },
    )
    youtube, vimeo = section(html, "yt"), section(html, "vm")
    assert "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg" in youtube  # its own thumbnail
    assert 'href="https://youtu.be/dQw4w9WgXcQ"' in youtube and "Watch the video" in youtube
    assert "Our bakers at work" in youtube and "border-radius:" in youtube
    assert PHOTO in vimeo and "padding:145px 20px" in vimeo  # edge to edge
    assert "v:image" in vimeo or "v:fill" in vimeo or "v:rect" in vimeo  # Outlook


def test_event(client: TestClient, auth: Headers) -> None:
    html = section(
        render(
            client,
            auth,
            {
                "id": "e",
                "type": "event",
                "settings": {"placeLink": "https://maps.example.com/bakery", "time": ""},
            },
        ),
        "e",
    )
    assert "Sourdough workshop" in html and "Saturday 22 November" in html and "&middot;" not in html
    assert 'href="https://maps.example.com/bakery"' in html and "Our bakery, 12 Main Street" in html
    assert 'href="https://example.com"' in html and "Book a place" in html


def test_offer_coupon_code(client: TestClient, auth: Headers) -> None:
    html = render(
        client,
        auth,
        {"id": "a", "type": "offer", "settings": {"code": "SPRING20"}},
        {"id": "b", "type": "offer", "settings": {}},
    )
    coupon = re.search(
        r'<span class="ms-heading" style="[^"]*dashed[^"]*">SPRING20</span>', section(html, "a")
    )
    assert coupon  # ms-heading keeps it readable in dark mode
    assert "dashed" not in section(html, "b")
