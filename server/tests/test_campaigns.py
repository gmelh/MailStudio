from __future__ import annotations

import re
import smtplib
from collections.abc import Callable
from email.message import EmailMessage
from pathlib import Path
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient

from mailstudio import tokens
from mailstudio.cli import campaign_schema
from mailstudio.config import Settings
from mailstudio.db import connect
from mailstudio.repositories import settings as settings_repo
from mailstudio.sections import load

from .conftest import CONSENT

Headers = dict[str, str]
REPO = Path(__file__).resolve().parents[2]


def setup_business(client: TestClient, auth: Headers) -> None:
    response = client.patch(
        "/api/v1/settings",
        headers=auth,
        json={
            "business": {"name": "Acme Bakery", "address": "1 Hay St, Perth WA 6000"},
            "sender": {
                "from_name": "Acme",
                "from_email": "news@acme.example",
                "reply_to": "hello@acme.example",
            },
        },
    )
    assert response.status_code == 200, response.text


def make_list(client: TestClient, auth: Headers, name: str) -> str:
    return str(client.post("/api/v1/lists", json={"name": name}, headers=auth).json()["id"])


def make_contact(client: TestClient, auth: Headers, email: str, lists: list[str], **extra: Any) -> str:
    body = {"email": email, "lists": lists, "consent": CONSENT, **extra}
    response = client.post("/api/v1/contacts", json=body, headers=auth)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def make_campaign(client: TestClient, auth: Headers, **body: Any) -> dict[str, Any]:
    response = client.post(
        "/api/v1/campaigns", json={"name": "November", "subject": "News", **body}, headers=auth
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


# ---------------------------------------------------------------------------
# Settings


def test_settings_groups_and_write_only_password(client: TestClient, auth: Headers) -> None:
    defaults = client.get("/api/v1/settings", headers=auth).json()
    assert defaults["smtp"]["password_set"] is False and defaults["brand"]["width"] == 600

    client.patch("/api/v1/settings", headers=auth, json={"smtp": {"host": "mail.example", "password": "pw"}})
    updated = client.patch(
        "/api/v1/settings", headers=auth, json={"smtp": {"host": "smtp.example", "port": 2525}}
    )
    smtp = updated.json()["smtp"]
    assert smtp == {
        "host": "smtp.example",
        "port": 2525,
        "username": "",
        "security": "starttls",
        "password_set": True,
    }
    assert '"password":' not in updated.text and "pw" not in updated.text


# ---------------------------------------------------------------------------
# Creating and editing campaigns


def test_new_campaign_starts_from_business_details(client: TestClient, auth: Headers) -> None:
    setup_business(client, auth)
    campaign = make_campaign(client, auth)
    assert campaign["id"].startswith("cmp_") and campaign["status"] == "draft"
    assert (campaign["from_name"], campaign["from_email"], campaign["reply_to"]) == (
        "Acme",
        "news@acme.example",
        "hello@acme.example",
    )
    sections = {s["type"]: s["settings"] for s in campaign["document"]["sections"]}
    assert sections["header"]["brand"] == "ACME BAKERY"
    assert sections["footer"]["address"] == "1 Hay St, Perth WA 6000"


def test_audience_counts_each_reachable_address_once(client: TestClient, auth: Headers) -> None:
    news, offers = make_list(client, auth, "News"), make_list(client, auth, "Offers")
    make_contact(client, auth, "both@example.com", [news, offers], tags=["vip"])
    make_contact(client, auth, "news@example.com", [news])
    gone = make_contact(client, auth, "gone@example.com", [news], tags=["vip"])
    make_contact(client, auth, "blocked@example.com", [offers], tags=["vip"])
    client.post(f"/api/v1/lists/{news}/members/{gone}/unsubscribe", headers=auth)
    client.post("/api/v1/suppressions", json={"email": "blocked@example.com"}, headers=auth)

    assert make_campaign(client, auth, lists=[news, offers])["audience"] == 2
    assert make_campaign(client, auth, lists=[news, offers], tags=["vip"])["audience"] == 1
    assert make_campaign(client, auth)["audience"] == 0


def test_editor_export_is_accepted_as_the_document(client: TestClient, auth: Headers) -> None:
    exported = {  # shape written by the editor's Export JSON
        "schema": 2,
        "id": "abc",
        "name": "From the editor",
        "subject": "ignored",
        "brand": {
            "width": 640,
            "fonts": {
                "heading": "Georgia, serif",
                "body": "Arial, Helvetica, sans-serif",
                "small": "Arial, Helvetica, sans-serif",
            },
        },
        "sections": [{"id": "1", "type": "text", "settings": {"heading": "Hi", "body": "Hello"}}],
    }
    campaign = make_campaign(client, auth, document=exported)
    assert campaign["document"]["brand"]["width"] == 640
    assert "name" not in campaign["document"]


def test_invalid_documents_list_every_problem(client: TestClient, auth: Headers) -> None:
    document = {
        "schema": 2,
        "sections": [
            {"id": "a", "type": "carousel", "settings": {}},
            {"id": "b", "type": "text", "settings": {"colour": "red"}},
            {"id": "b", "type": "image", "settings": {"url": "data:image/png;base64,AAAA"}},
        ],
    }
    response = client.post("/api/v1/campaigns", json={"name": "x", "document": document}, headers=auth)
    assert response.status_code == 422
    messages = {e["location"]: e["message"] for e in response.json()["errors"]}
    assert "Unknown section type" in messages["document.sections.0"]
    assert "Unknown field" in messages["document.sections.1.settings.colour"]
    assert "Duplicate" in messages["document.sections.2"]
    assert "hosted image" in messages["document.sections.2.settings.url"]


def test_unknown_target_list_is_rejected(client: TestClient, auth: Headers) -> None:
    response = client.post("/api/v1/campaigns", json={"name": "x", "lists": ["lst_nope"]}, headers=auth)
    assert response.status_code == 422 and response.json()["list_ids"] == ["lst_nope"]


def test_only_drafts_can_change(settings: Settings, client: TestClient, auth: Headers) -> None:
    campaign = make_campaign(client, auth)
    url = f"/api/v1/campaigns/{campaign['id']}"
    assert (
        client.patch(url, json={"subject": "Better subject"}, headers=auth).json()["subject"]
        == "Better subject"
    )
    conn = connect(settings.database_path)
    conn.execute("UPDATE campaigns SET status = 'sent' WHERE id = ?", (campaign["id"],))
    conn.close()
    assert client.patch(url, json={"subject": "Too late"}, headers=auth).status_code == 409
    assert client.delete(url, headers=auth).status_code == 409


# ---------------------------------------------------------------------------
# Rendering


def test_preview_personalises_and_escapes(client: TestClient, auth: Headers) -> None:
    news = make_list(client, auth, "News")
    contact = make_contact(client, auth, "ada@example.com", [news], first_name="Ada <b>")
    document = {
        "schema": 2,
        "sections": [
            {
                "id": "t",
                "type": "text",
                "settings": {"heading": "Hi {{first_name|there}}", "body": "<script>x</script>"},
            },
        ],
    }
    campaign = make_campaign(client, auth, subject="For {{first_name|you}}", document=document)
    url = f"/api/v1/campaigns/{campaign['id']}/preview"

    sample = client.post(url, headers=auth).json()
    assert sample["subject"] == "For you" and "Hi there" in sample["html"]

    personal = client.post(url, json={"contact_id": contact}, headers=auth).json()
    assert personal["subject"] == "For Ada <b>"
    assert "Hi Ada &lt;b&gt;" in personal["html"] and "<script>" not in personal["html"]
    assert "Hi Ada <b>" in personal["text"]
    assert "/u/preview" in personal["html"]


def test_compliance_footer_added_only_when_missing(client: TestClient, auth: Headers) -> None:
    setup_business(client, auth)

    def unsubscribe_links(sections: list[dict[str, Any]]) -> int:
        body = {"document": {"schema": 2, "sections": sections}}
        html = client.post("/api/v1/render", json=body, headers=auth).json()["html"]
        return len(re.findall(r">Unsubscribe</a>", html))

    text = {"id": "t", "type": "text", "settings": {}}
    assert unsubscribe_links([text]) == 1
    assert unsubscribe_links([text, {"id": "f", "type": "footer", "settings": {}}]) == 1


def test_every_section_type_renders(settings: Settings, client: TestClient, auth: Headers) -> None:
    library = load(settings.sections_dir)
    sections = [{"id": t, "type": t, "settings": {}} for t in library.sections]
    response = client.post(
        "/api/v1/render", json={"document": {"schema": 2, "sections": sections}}, headers=auth
    )
    assert response.status_code == 200, response.text
    html = response.json()["html"]
    assert html.startswith("<!doctype html>") and "Something new is here." in html


# ---------------------------------------------------------------------------
# Test sends


class FakeSmtp:
    sent: ClassVar[list[EmailMessage]] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host = host

    def __enter__(self) -> FakeSmtp:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def starttls(self, **_: object) -> None:
        pass

    def login(self, user: str, password: str) -> None:
        pass

    def send_message(self, message: EmailMessage) -> None:
        FakeSmtp.sent.append(message)


def test_test_send_needs_send_scope_and_smtp(
    client: TestClient, make_key: Callable[[str], Headers], monkeypatch: pytest.MonkeyPatch
) -> None:
    writer, sender = make_key("read write"), make_key("read write send")
    setup_business(client, writer)
    campaign = make_campaign(client, writer, subject="Hello {{first_name|friend}}")
    url = f"/api/v1/campaigns/{campaign['id']}/test"

    assert client.post(url, json={"to": ["me@example.com"]}, headers=writer).status_code == 403
    no_smtp = client.post(url, json={"to": ["me@example.com"]}, headers=sender)
    assert no_smtp.status_code == 502 and "Settings" in no_smtp.json()["detail"]

    client.patch("/api/v1/settings", headers=writer, json={"smtp": {"host": "smtp.example", "port": 1025}})
    monkeypatch.setattr(smtplib, "SMTP", FakeSmtp)
    FakeSmtp.sent = []
    response = client.post(url, json={"to": ["me@example.com", "you@example.com"]}, headers=sender)
    assert response.status_code == 202
    assert len(FakeSmtp.sent) == 2
    message = FakeSmtp.sent[0]
    assert message["Subject"] == "[Test] Hello friend"
    assert message["From"] == "Acme <news@acme.example>"
    assert message["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert message.get_body(("plain",)) is not None and message.get_body(("html",)) is not None


# ---------------------------------------------------------------------------
# Unsubscribe page


def test_unsubscribe_link_flow(settings: Settings, client: TestClient, auth: Headers) -> None:
    news, other = make_list(client, auth, "News"), make_list(client, auth, "Other")
    contact = make_contact(client, auth, "ada@example.com", [news, other])
    campaign = make_campaign(client, auth, lists=[news])
    conn = connect(settings.database_path)
    token = tokens.make(settings_repo.secret_key(conn), contact, campaign["id"])
    conn.close()

    page = client.get(f"/u/{token}")
    assert page.status_code == 200 and "ada@example.com" in page.text and "<form" in page.text
    memberships = client.get(f"/api/v1/contacts/{contact}", headers=auth).json()["lists"]
    assert all(m["status"] == "subscribed" for m in memberships)  # GET changes nothing

    assert "unsubscribed" in client.post(f"/u/{token}").text
    statuses = {
        m["list_id"]: m["status"]
        for m in client.get(f"/api/v1/contacts/{contact}", headers=auth).json()["lists"]
    }
    assert statuses == {news: "unsubscribed", other: "subscribed"}  # only the campaign's list


def test_one_click_and_forged_tokens(settings: Settings, client: TestClient, auth: Headers) -> None:
    news = make_list(client, auth, "News")
    contact = make_contact(client, auth, "ada@example.com", [news])
    campaign = make_campaign(client, auth, lists=[news])
    conn = connect(settings.database_path)
    token = tokens.make(settings_repo.secret_key(conn), contact, campaign["id"])

    forged = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    assert client.post(f"/u/{forged}").status_code == 400
    assert client.get("/u/preview").status_code == 200

    response = client.post(f"/u/{token}", data={"List-Unsubscribe": "One-Click"})
    assert response.status_code == 200
    source = conn.execute(
        "SELECT source FROM consent_events WHERE contact_id = ? AND action = 'unsubscribe'", (contact,)
    ).fetchone()[0]
    conn.close()
    assert source.startswith("One-click unsubscribe")


# ---------------------------------------------------------------------------
# Shared files stay in step


def test_committed_json_schema_is_current() -> None:
    committed = (REPO / "schema" / "campaign.schema.json").read_text(encoding="utf-8")
    assert committed == campaign_schema(), "Run `mailstudio export-schema` and commit the result"
