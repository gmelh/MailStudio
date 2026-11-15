"""Templates: bundled ones, previews, starting campaigns from them, export, the gallery."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mailstudio import gallery
from mailstudio.config import Settings
from mailstudio.sections import load

SECTIONS = Path(__file__).resolve().parents[2] / "sections"


def _template(client: TestClient, auth: dict[str, str], template_id: str = "harbour") -> dict[str, Any]:
    templates = client.get("/api/v1/templates", headers=auth).json()["templates"]
    return next(t for t in templates if t["id"] == template_id)


def test_bundled_templates_are_valid_and_render() -> None:
    from mailstudio import render
    from mailstudio.document import Brand, check_sections

    library = load(SECTIONS)
    templates = gallery.bundled(SECTIONS)
    assert len(templates) >= 16
    assert len({t.id for t in templates}) == len(templates)
    for template in templates:
        assert template.description and template.subject
        document = gallery.apply(template, Brand(), "Acme", "1 Road", "template")
        check_sections(document, library)
        for scheme in ("light", "dark"):
            html = render.render(
                document,
                library,
                subject=template.subject,
                preheader=template.preheader,
                business=render.Business(name="Acme", address="1 Road"),
                scheme=scheme,
            ).html
            assert "Acme" in html


def test_bundled_templates_have_distinct_colours() -> None:
    accents = {t.document.brand.light.accent for t in gallery.bundled(SECTIONS)}
    assert len(accents) == len(gallery.bundled(SECTIONS))


def test_list_templates(client: TestClient, auth: dict[str, str]) -> None:
    body = client.get("/api/v1/templates", headers=auth).json()
    assert {"harbour", "ember", "meadow", "ink", "postcard", "lantern", "haven", "beacon"} <= {
        t["id"] for t in body["templates"]
    }
    assert body["gallery"] is True
    assert "newsletter" in body["categories"]


def test_preview_in_template_or_brand_colours(client: TestClient, auth: dict[str, str]) -> None:
    client.patch(
        "/api/v1/settings", headers=auth, json={"business": {"name": "Blue Fern", "address": "9 Leaf Lane"}}
    )
    template = _template(client, auth, "ember")
    own = client.post("/api/v1/templates/preview", headers=auth, json={"template": template}).json()
    assert template["document"]["brand"]["light"]["accent"] in own["html"]
    assert "Blue Fern" in own["html"] and "9 Leaf Lane" in own["html"]
    mine = client.post(
        "/api/v1/templates/preview", headers=auth, json={"template": template, "colours": "brand"}
    ).json()
    assert template["document"]["brand"]["light"]["accent"] not in mine["html"]


def test_create_campaign_from_template(client: TestClient, auth: dict[str, str]) -> None:
    client.patch(
        "/api/v1/settings", headers=auth, json={"business": {"name": "Blue Fern", "address": "9 Leaf Lane"}}
    )
    template = _template(client, auth, "meadow")
    reply = client.post(
        "/api/v1/campaigns",
        headers=auth,
        json={"name": "Open day", "template": template, "colours": "template"},
    )
    assert reply.status_code == 201, reply.text
    campaign = reply.json()
    assert campaign["subject"] == template["subject"]
    assert campaign["preheader"] == template["preheader"]
    document = campaign["document"]
    assert document["brand"]["light"] == template["document"]["brand"]["light"]
    assert [s["type"] for s in document["sections"]] == [s["type"] for s in template["document"]["sections"]]
    header = next(s for s in document["sections"] if s["type"] == "header")
    footer = next(s for s in document["sections"] if s["type"] == "footer")
    assert header["settings"]["brand"] == "BLUE FERN"
    assert footer["settings"]["company"] == "Blue Fern"
    assert footer["settings"]["address"] == "9 Leaf Lane"


def test_create_from_template_in_brand_colours(client: TestClient, auth: dict[str, str]) -> None:
    brand = client.get("/api/v1/settings", headers=auth).json()["brand"]
    reply = client.post(
        "/api/v1/campaigns", headers=auth, json={"name": "Sale", "template_id": "ember", "colours": "brand"}
    )
    assert reply.status_code == 201, reply.text
    document = reply.json()["document"]
    assert document["brand"]["light"] == brand["light"]
    assert document["brand"]["radius"] == 10  # the template's shape stays


def test_explicit_subject_wins_over_template(client: TestClient, auth: dict[str, str]) -> None:
    reply = client.post(
        "/api/v1/campaigns",
        headers=auth,
        json={"name": "X", "template_id": "ink", "subject": "My own subject"},
    )
    assert reply.json()["subject"] == "My own subject"


def test_unknown_bundled_template(client: TestClient, auth: dict[str, str]) -> None:
    reply = client.post("/api/v1/campaigns", headers=auth, json={"name": "X", "template_id": "nope"})
    assert reply.status_code == 404


def test_check_rejects_bad_templates(client: TestClient, auth: dict[str, str]) -> None:
    template = _template(client, auth)
    assert client.post("/api/v1/templates/check", headers=auth, json=template).status_code == 200

    wrong = client.post(
        "/api/v1/templates/check", headers=auth, json={**template, "format": "something-else"}
    )
    assert wrong.status_code == 422 and wrong.json()["type"].endswith("not-a-template")

    broken = json.loads(json.dumps(template))
    broken["document"]["brand"]["light"]["accent"] = "red"
    reply = client.post("/api/v1/templates/check", headers=auth, json=broken)
    assert reply.status_code == 422
    assert any("accent" in e["location"] for e in reply.json()["errors"])

    unknown = json.loads(json.dumps(template))
    unknown["document"]["sections"].append({"id": "x", "type": "carousel", "settings": {}})
    assert client.post("/api/v1/templates/check", headers=auth, json=unknown).status_code == 422


def test_export_campaign_as_template(client: TestClient, auth: dict[str, str]) -> None:
    client.patch(
        "/api/v1/settings", headers=auth, json={"business": {"name": "Blue Fern", "address": "9 Leaf Lane"}}
    )
    created = client.post(
        "/api/v1/campaigns", headers=auth, json={"name": "Spring Sale 2027!", "template_id": "ember"}
    ).json()
    reply = client.get(f"/api/v1/campaigns/{created['id']}/template", headers=auth)
    assert reply.status_code == 200
    assert "spring-sale-2027.mstemplate.json" in reply.headers["content-disposition"]
    template = reply.json()
    assert template["format"] == "mailstudio-template" and template["id"] == "spring-sale-2027"
    assert "Blue Fern" not in json.dumps(template)  # the business details stay behind
    assert template["document"]["brand"]["logo"] == ""
    # ...and it goes straight back in.
    assert client.post("/api/v1/templates/check", headers=auth, json=template).status_code == 200


# The online gallery -------------------------------------------------------------------


@pytest.fixture
def fake_gallery(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    template = json.loads(next((SECTIONS / "gallery").glob("*harbour.json")).read_text())
    files: dict[str, Any] = {
        "https://gallery.test/gallery/index.json": {
            "format": "mailstudio-gallery",
            "version": 1,
            "templates": [
                {
                    "id": "harbour",
                    "name": "Harbour",
                    "category": "newsletter",
                    "file": "t/harbour.json",
                    "preview": "t/harbour.png",
                },
            ],
        },
        "https://gallery.test/gallery/t/harbour.json": template,
    }
    fetched: list[str] = []

    def fetch(url: str, limit: int) -> Any:
        fetched.append(url)
        if url not in files:
            raise gallery.ProblemError(502, "gallery-unreachable", "The gallery isn't available")
        return files[url]

    monkeypatch.setattr(gallery, "_fetch", fetch)
    return {"files": files, "fetched": fetched}


@pytest.fixture
def gallery_client(tmp_path: Path) -> Any:
    from mailstudio.app import create_app

    settings = Settings(data_dir=tmp_path / "data", gallery_url="https://gallery.test/gallery/index.json")
    with TestClient(create_app(settings)) as test_client:
        yield test_client, settings


def _key(settings: Settings) -> dict[str, str]:
    from mailstudio.auth import create_key, parse_scopes
    from mailstudio.db import connect, transaction

    conn = connect(settings.database_path)
    with transaction(conn):
        key = create_key(conn, "test", parse_scopes("read write"))
    conn.close()
    return {"Authorization": f"Bearer {key.token}"}


def test_gallery_list_and_fetch(gallery_client: Any, fake_gallery: dict[str, Any]) -> None:
    client, settings = gallery_client
    auth = _key(settings)
    listed = client.get("/api/v1/templates/gallery", headers=auth).json()
    assert listed["templates"][0]["preview"] == "https://gallery.test/gallery/t/harbour.png"
    assert "file" not in listed["templates"][0]
    one = client.get("/api/v1/templates/gallery/harbour", headers=auth)
    assert one.status_code == 200 and one.json()["name"] == "Harbour"
    assert client.get("/api/v1/templates/gallery/other", headers=auth).status_code == 404


def test_gallery_unreachable(gallery_client: Any, fake_gallery: dict[str, Any]) -> None:
    client, settings = gallery_client
    fake_gallery["files"].clear()
    reply = client.get("/api/v1/templates/gallery", headers=_key(settings))
    assert reply.status_code == 502 and reply.json()["type"].endswith("gallery-unreachable")


def test_gallery_rejects_non_gallery_json(gallery_client: Any, fake_gallery: dict[str, Any]) -> None:
    client, settings = gallery_client
    fake_gallery["files"]["https://gallery.test/gallery/index.json"] = {"hello": "world"}
    reply = client.get("/api/v1/templates/gallery", headers=_key(settings))
    assert reply.status_code == 502 and reply.json()["type"].endswith("gallery-invalid")


def test_gallery_index_cannot_point_outside_itself() -> None:
    with pytest.raises(ValueError):
        gallery.GalleryEntry.model_validate({"id": "x", "name": "X", "file": "https://evil.test/x.json"})
    with pytest.raises(ValueError):
        gallery.GalleryEntry.model_validate({"id": "x", "name": "X", "file": "../x.json"})


def test_gallery_turned_off(tmp_path: Path) -> None:
    from mailstudio.app import create_app

    settings = Settings(data_dir=tmp_path / "data", gallery_url="")
    with TestClient(create_app(settings)) as client:
        auth = _key(settings)
        assert client.get("/api/v1/templates", headers=auth).json()["gallery"] is False
        assert client.get("/api/v1/templates/gallery", headers=auth).status_code == 404


def test_template_schema_is_committed() -> None:
    from mailstudio.cli import template_schema

    committed = (SECTIONS.parent / "schema" / "template.schema.json").read_text(encoding="utf-8")
    assert committed == template_schema(), "Run `mailstudio export-schema` and commit the result"


def _contrast(a: str, b: str) -> float:
    def luminance(colour: str) -> float:
        channels = [int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize("template", gallery.bundled(SECTIONS), ids=lambda t: t.id)
def test_bundled_templates_are_readable(template: gallery.Template) -> None:
    """Text, links and button labels meet WCAG AA contrast, light and dark."""
    for scheme in ("light", "dark"):
        p = getattr(template.document.brand, scheme)
        for ground in (p.background, p.featured):
            assert _contrast(p.text, ground) >= 4.5, (scheme, "text")
            assert _contrast(p.link, ground) >= 4.5, (scheme, "link")
            assert _contrast(p.heading, ground) >= 3, (scheme, "heading")
        assert _contrast(p.buttonText, p.accent) >= 4.5, (scheme, "button")
