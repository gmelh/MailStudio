"""Image checks before sending: images recipients can't load stop a campaign; weaker
practices (descriptions, sharpness, weight, image-only emails) are warnings."""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from mailstudio import images
from mailstudio.app import create_app
from mailstudio.config import Settings

Headers = dict[str, str]
PUBLIC = "https://mail.bakery.example"
WORDS = " ".join(["Fresh sourdough every morning."] * 20)  # enough text not to be image-only


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", base_url=PUBLIC)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def upload(client: TestClient, auth: Headers, size: tuple[int, int]) -> dict[str, Any]:
    out = io.BytesIO()
    Image.new("RGB", size, (200, 80, 40)).save(out, "JPEG")
    response = client.post(
        "/api/v1/assets", files={"file": ("photo.jpg", out.getvalue(), "image/jpeg")}, headers=auth
    )
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def campaign(client: TestClient, auth: Headers, sections: list[dict[str, Any]], text: bool = True) -> str:
    body = [{"id": "words", "type": "text", "settings": {"heading": "News", "body": WORDS}}] if text else []
    document = {"schema": 2, "sections": [*sections, *body, {"id": "foot", "type": "footer", "settings": {}}]}
    created = client.post(
        "/api/v1/campaigns", json={"name": "T", "subject": "Hi", "document": document}, headers=auth
    )
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


def checks(client: TestClient, auth: Headers, campaign_id: str) -> dict[str, list[str]]:
    return client.get(f"/api/v1/campaigns/{campaign_id}/checks", headers=auth).json()  # type: ignore[no-any-return]


@pytest.mark.parametrize(
    ("url", "reachable"),
    [
        ("https://mail.bakery.example/assets/x.jpg", True),
        ("https://cdn.example.com/logo.png", True),
        ("https://93.184.215.14/logo.png", True),
        ("http://localhost:8080/assets/x.jpg", False),
        ("http://127.0.0.1/x.jpg", False),
        ("http://192.168.1.20/x.jpg", False),
        ("http://10.0.0.5/x.jpg", False),
        ("http://[::1]/x.jpg", False),
        ("http://nas.local/x.jpg", False),
        ("http://intranet/x.jpg", False),
    ],
)
def test_addresses_recipients_cannot_reach(url: str, reachable: bool) -> None:
    assert (images.unreachable(url) is None) is reachable


def test_own_images_follow_the_install_address() -> None:
    html = '<img src="http://localhost:8080/assets/ast_01JZ0000000000000000000000.jpg"><img src="https://x.com/a.jpg">'
    assert images.absolute_assets(html, PUBLIC + "/") == (
        f'<img src="{PUBLIC}/assets/ast_01JZ0000000000000000000000.jpg"><img src="https://x.com/a.jpg">'
    )


def test_unreachable_and_missing_images_stop_sending(client: TestClient, make_key: Any) -> None:
    auth = make_key("read write send")
    client.patch(
        "/api/v1/settings", json={"business": {"name": "Bakery", "address": "1 Main St"}}, headers=auth
    )
    lst = client.post("/api/v1/lists", json={"name": "News"}, headers=auth).json()
    client.post(f"/api/v1/lists/{lst['id']}/contacts", json={"email": "ann@example.com"}, headers=auth)
    cid = campaign(
        client,
        auth,
        [
            {"id": "logo", "type": "header", "settings": {"imageUrl": "http://192.168.1.20/logo.png"}},
            {
                "id": "pic",
                "type": "image",
                "settings": {"url": f"{PUBLIC}/assets/ast_01JZ0000000000000000000000.jpg", "alt": "Loaves"},
            },
        ],
    )
    found = checks(client, auth, cid)
    assert found["problems"] == [
        "The image in section 1 (Header) points at 192.168.1.20, a private address recipients can't reach. "
        "Upload it here, on your MailStudio's public address.",
        "The image in section 2 (Image) isn't in this MailStudio: it was deleted, or uploaded to another "
        "install. Upload it again.",
    ]
    client.patch(
        f"/api/v1/campaigns/{cid}",
        json={"lists": [lst["id"]], "from_email": "news@bakery.example", "from_name": "Bakery"},
        headers=auth,
    )
    refused = client.post(f"/api/v1/campaigns/{cid}/schedule", headers=auth)
    assert refused.status_code == 422
    assert set(found["problems"]) <= set(refused.json()["problems"])


def test_good_images_pass(client: TestClient, auth: Headers) -> None:
    banner = upload(client, auth, (1200, 400))
    cid = campaign(
        client,
        auth,
        [
            {"id": "logo", "type": "header", "settings": {"imageUrl": banner["url"], "imageStretch": "on"}},
            {"id": "pic", "type": "image", "settings": {"url": banner["url"], "alt": "Our new rye loaf"}},
        ],
    )
    assert checks(client, auth, cid) == {"problems": [], "warnings": []}  # the header uses the brand name


def test_warnings(client: TestClient, auth: Headers) -> None:
    small = upload(client, auth, (300, 200))
    cid = campaign(
        client,
        auth,
        [
            {"id": "pic", "type": "image", "settings": {"url": small["url"], "alt": ""}},
            {
                "id": "side",
                "type": "imageText",
                "settings": {"url": "http://cdn.example.com/a.jpg", "alt": "IMG_2041.jpg"},
            },
        ],
    )
    warnings = checks(client, auth, cid)["warnings"]
    assert len(warnings) == 4, warnings
    assert "section 1 (Image) has no description" in warnings[0]
    assert "300px wide but shown at" in warnings[1] and "blurry" in warnings[1]
    assert "section 2 (Image + Text) uses plain http" in warnings[2]
    assert "described as “IMG_2041.jpg”" in warnings[3]


def test_heavy_and_image_only_emails(
    client: TestClient, auth: Headers, monkeypatch: pytest.MonkeyPatch
) -> None:
    banner = upload(client, auth, (1200, 400))
    monkeypatch.setattr(images, "HEAVY_IMAGE_BYTES", 10)
    monkeypatch.setattr(images, "HEAVY_EMAIL_BYTES", 10)
    cid = campaign(
        client,
        auth,
        [{"id": "pic", "type": "image", "settings": {"url": banner["url"], "alt": "Rye"}}],
        text=False,
    )
    warnings = checks(client, auth, cid)["warnings"]
    assert any("KB, slow on mobile data" in w for w in warnings)
    assert any("The images add up to" in w for w in warnings)
    assert any("mostly images" in w for w in warnings)


def test_local_installs_warn_instead_of_blocking(tmp_path: Path) -> None:
    local = Settings(data_dir=tmp_path / "local")  # http://localhost:8080
    with TestClient(create_app(local)) as client:
        setup = client.post(
            "/api/v1/auth/setup",
            json={"email": "g@example.com", "password": "correct-horse-battery", "name": "G"},
        ).json()
        auth = {"X-CSRF-Token": setup["csrf_token"]}
        logo = upload(client, auth, (1200, 400))
        cid = campaign(
            client, auth, [{"id": "pic", "type": "image", "settings": {"url": logo["url"], "alt": "Rye"}}]
        )
        found = checks(client, auth, cid)
        assert found["problems"] == []
        assert "only you will see it" in found["warnings"][0]
