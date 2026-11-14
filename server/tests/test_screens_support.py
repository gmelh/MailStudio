"""API additions for the everyday screens: search, consent history, copying
campaigns, unsubscribe counts, and test sends through SES."""

from __future__ import annotations

from email.message import EmailMessage
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient

from mailstudio import ses
from mailstudio.config import Settings
from mailstudio.db import connect, transaction
from mailstudio.providers import Outcome, Result
from mailstudio.repositories import lists

from .conftest import CONSENT

Headers = dict[str, str]


def add_contact(client: TestClient, auth: Headers, email: str, **extra: Any) -> str:
    response = client.post("/api/v1/contacts", json={"email": email, **extra}, headers=auth)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def test_search_matches_email_and_name_and_escapes_wildcards(client: TestClient, auth: Headers) -> None:
    add_contact(client, auth, "ada@example.com", first_name="Ada", last_name="Lovelace")
    add_contact(client, auth, "grace@navy.example", first_name="Grace", last_name="Hopper")
    add_contact(client, auth, "per_cent@example.com")

    def search(q: str) -> list[str]:
        response = client.get("/api/v1/contacts", params={"q": q}, headers=auth)
        assert response.status_code == 200, response.text
        return sorted(c["email"] for c in response.json()["data"])

    assert search("LOVE") == ["ada@example.com"]
    assert search("grace hop") == ["grace@navy.example"]
    assert search("navy") == ["grace@navy.example"]
    assert search("_") == ["per_cent@example.com"]  # underscore is literal, not "any character"
    assert search("%") == []
    assert len(search("example")) == 3


def test_history_shows_consent_and_unsubscribes(client: TestClient, auth: Headers) -> None:
    list_id = client.post("/api/v1/lists", json={"name": "News"}, headers=auth).json()["id"]
    contact = add_contact(client, auth, "ada@example.com", lists=[list_id], consent=CONSENT)
    unsubscribed = client.post(f"/api/v1/lists/{list_id}/members/{contact}/unsubscribe", headers=auth)
    assert unsubscribed.status_code == 204

    history = client.get(f"/api/v1/contacts/{contact}/history", headers=auth).json()
    assert [e["action"] for e in history] == ["unsubscribe", "consent"]
    assert history[1]["source"] == CONSENT["source"] and history[1]["list_name"] == "News"
    missing = client.get("/api/v1/contacts/ctc_01J00000000000000000000000/history", headers=auth)
    assert missing.status_code == 404


def test_duplicate_copies_design_and_audience_into_a_new_draft(client: TestClient, auth: Headers) -> None:
    list_id = client.post("/api/v1/lists", json={"name": "News"}, headers=auth).json()["id"]
    original = client.post(
        "/api/v1/campaigns",
        json={"name": "November", "subject": "Hello", "lists": [list_id], "from_email": "shop@example.com"},
        headers=auth,
    ).json()

    copy = client.post(f"/api/v1/campaigns/{original['id']}/duplicate", headers=auth)
    assert copy.status_code == 201, copy.text
    body = copy.json()
    assert body["id"] != original["id"] and body["status"] == "draft"
    assert body["name"] == "Copy of November" and body["subject"] == "Hello"
    assert body["lists"] == [list_id] and body["from_email"] == "shop@example.com"
    assert body["document"] == original["document"]


def test_stats_count_unsubscribes_from_that_campaign(
    client: TestClient, auth: Headers, settings: Settings
) -> None:
    list_id = client.post("/api/v1/lists", json={"name": "News"}, headers=auth).json()["id"]
    a = add_contact(client, auth, "a@example.com", lists=[list_id], consent=CONSENT)
    b = add_contact(client, auth, "b@example.com", lists=[list_id], consent=CONSENT)
    campaign = client.post("/api/v1/campaigns", json={"name": "N"}, headers=auth).json()["id"]
    conn = connect(settings.database_path)
    with transaction(conn):
        lists.unsubscribe(conn, list_id, a, f"Unsubscribe link ({campaign})")
        lists.unsubscribe(conn, list_id, b, "Unsubscribe link (cmp_SOMETHINGELSE)")
    conn.close()
    assert client.get(f"/api/v1/campaigns/{campaign}/stats", headers=auth).json()["unsubscribed"] == 1


class FakeSes:
    sent: ClassVar[list[EmailMessage]] = []

    def __init__(self, _config: Any) -> None:
        pass

    def send(self, message: EmailMessage) -> Result:
        FakeSes.sent.append(message)
        return Result(Outcome.SENT, "msg-1")

    def close(self) -> None:
        pass


def test_test_send_uses_ses_when_no_smtp_is_set(
    client: TestClient, make_key: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = make_key("read write send")
    monkeypatch.setattr(ses, "SesProvider", FakeSes)
    FakeSes.sent = []
    client.patch(
        "/api/v1/settings",
        json={"ses": {"region": "ap-southeast-2", "access_key_id": "AKIA", "secret_access_key": "s"}},
        headers=key,
    )
    campaign = client.post(
        "/api/v1/campaigns",
        json={"name": "N", "subject": "Hi", "from_email": "shop@example.com"},
        headers=key,
    ).json()["id"]
    response = client.post(f"/api/v1/campaigns/{campaign}/test", json={"to": ["me@example.com"]}, headers=key)
    assert response.status_code == 202, response.text
    assert [m["To"] for m in FakeSes.sent] == ["me@example.com"]
    assert FakeSes.sent[0]["Subject"] == "[Test] Hi"
