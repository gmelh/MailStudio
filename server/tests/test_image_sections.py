"""Linked images, images pushed to the email's edges, the Hero image section and the Spacer."""

from __future__ import annotations

import re
from typing import Any

from fastapi.testclient import TestClient

Headers = dict[str, str]
PHOTO = "https://cdn.example.com/rye.jpg"


def render(client: TestClient, auth: Headers, *sections: dict[str, Any]) -> str:
    body = {
        "document": {"schema": 2, "sections": list(sections), "brand": {"padding": 30, "radius": 8}},
        "subject": "Hi",
        "preheader": "",
    }
    response = client.post("/api/v1/render", json=body, headers=auth)
    assert response.status_code == 200, response.text
    return str(response.json()["html"])


def section(html: str, section_id: str) -> str:
    start = html.index(f'class="ms-s-{section_id}')
    rest = html[start + 10 :]
    following = re.search(r'class="ms-s-(?!' + re.escape(section_id) + r")", rest)
    return rest[: following.start()] if following else rest


def padding(html: str) -> str:
    found = re.search(r'<td style="direction:ltr;font-size:0px;padding:([^;]+);', html)
    assert found, html
    return found.group(1)


def image_cell(html: str) -> str:
    """The style of the cell around the first image, and the image's width."""
    found = re.search(
        r'<td align="\w+" style="([^"]*)"><table[^>]*><tbody><tr>'
        r'<td(?: class="[^"]*")? style="width:(\d+)px;">',
        html,
    )
    assert found, html
    return f"{found.group(1)} width:{found.group(2)}"


def test_images_can_link(client: TestClient, auth: Headers) -> None:
    html = render(
        client,
        auth,
        {"id": "h", "type": "header", "settings": {"imageUrl": PHOTO, "imageLink": "https://bakery.example"}},
        {"id": "i", "type": "image", "settings": {"url": PHOTO, "link": "https://bakery.example/rye"}},
        {"id": "t", "type": "imageText", "settings": {"url": PHOTO, "link": "javascript:alert(1)"}},
    )
    assert re.search(r'<a href="https://bakery.example"[^>]*><img', section(html, "h"))
    assert re.search(r'<a href="https://bakery.example/rye"[^>]*><img', section(html, "i"))
    assert not re.search(r"<a [^>]*><img", section(html, "t"))  # unsafe links are dropped
    assert "javascript" not in html


def test_image_to_the_edges(client: TestClient, auth: Headers) -> None:
    html = render(
        client,
        auth,
        {"id": "a", "type": "image", "settings": {"url": PHOTO}},
        {"id": "b", "type": "image", "settings": {"url": PHOTO, "edge": "on"}},
    )
    inset, edge = section(html, "a"), section(html, "b")
    assert padding(inset) == "20px 30px" and "border-radius:5px" in inset
    assert padding(edge) == "0" and "border-radius" not in edge
    assert "padding:0;" in image_cell(edge) and "width:600" in image_cell(edge)  # the email's full width


def test_image_and_text_to_the_edges(client: TestClient, auth: Headers) -> None:
    for position, text_padding in (("left", "24px 30px 24px 24px"), ("right", "24px 24px 24px 30px")):
        html = section(
            render(
                client,
                auth,
                {
                    "id": "t",
                    "type": "imageText",
                    "settings": {"url": PHOTO, "edge": "on", "position": position},
                },
            ),
            "t",
        )
        assert padding(html) == "0", position
        assert "padding:0;" in image_cell(html) and "width:300" in image_cell(html), (
            position
        )  # its column, unstretched
        assert f"padding:{text_padding};" in html, position  # the words keep their spacing
        # The image is on the requested side.
        assert (html.index("<img") < html.index("ms-heading")) == (position == "left"), position


def test_edges_need_an_image(client: TestClient, auth: Headers) -> None:
    html = section(render(client, auth, {"id": "t", "type": "imageText", "settings": {"edge": "on"}}), "t")
    assert padding(html) != "0"  # no image: the usual layout


def test_hero_image(client: TestClient, auth: Headers) -> None:
    html = render(
        client,
        auth,
        {"id": "edge", "type": "heroImage", "settings": {"url": PHOTO, "title": "Fresh", "height": "tall"}},
        {
            "id": "inset",
            "type": "heroImage",
            "settings": {"url": PHOTO, "edge": "off", "tone": "dark", "align": "left"},
        },
        {"id": "plain", "type": "heroImage", "settings": {"buttonUrl": ""}},
    )
    edge, inset, plain = section(html, "edge"), section(html, "inset"), section(html, "plain")
    assert (PHOTO in edge and "v:image" in edge) or "v:fill" in edge or "v:rect" in edge  # Outlook gets VML
    assert "background-size:cover" in edge.replace(" ", "")
    assert "padding:136px 30px" in edge and "color:#ffffff" in edge
    assert "border-radius:8px" not in edge  # square corners (the button's own are 9px)
    assert "padding:16px 30px" in inset and "border-radius:8px" in inset and "text-align:left" in inset
    headline = re.search(r'<h1 style="[^"]*color:([^;"]+)', inset)
    assert headline and headline.group(1) != "#ffffff"  # dark text for light photos
    assert PHOTO not in plain and "ms-button" not in plain  # no image or button: just a coloured panel


def test_spacer(client: TestClient, auth: Headers) -> None:
    html = section(render(client, auth, {"id": "s", "type": "spacer", "settings": {"height": "48"}}), "s")
    assert "height:48px" in html
    tall = section(render(client, auth, {"id": "s", "type": "spacer", "settings": {"height": "999"}}), "s")
    assert "height:160px" in tall  # kept in range


def test_image_checks_include_hero_backgrounds(client: TestClient, make_key: Any) -> None:
    auth = make_key("read write send")
    document = {
        "schema": 2,
        "sections": [
            {"id": "hero", "type": "heroImage", "settings": {"url": "http://192.168.1.20/shop.jpg"}}
        ],
    }
    cid = client.post("/api/v1/campaigns", json={"name": "T", "document": document}, headers=auth).json()[
        "id"
    ]
    found = client.get(f"/api/v1/campaigns/{cid}/checks", headers=auth).json()
    # On this test install (itself on localhost) that's a warning; only one, despite the Outlook copy.
    shown = [w for w in found["warnings"] if "192.168.1.20" in w]
    assert len(shown) == 1 and "section 1 (Hero image)" in shown[0]
    assert not any("description" in w for w in found["warnings"])  # backgrounds have none
