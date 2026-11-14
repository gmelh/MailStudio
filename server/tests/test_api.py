from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from mailstudio.config import Settings
from mailstudio.db import connect, migrate

from .conftest import CONSENT

Headers = dict[str, str]


def new_list(client: TestClient, auth: Headers, name: str = "Newsletter") -> dict[str, Any]:
    response = client.post("/api/v1/lists", json={"name": name}, headers=auth)
    assert response.status_code == 201, response.text
    return response.json()


def new_contact(client: TestClient, auth: Headers, email: str, **extra: Any) -> dict[str, Any]:
    response = client.post("/api/v1/contacts", json={"email": email, **extra}, headers=auth)
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# System, auth and errors


def test_health_needs_no_key(client: TestClient) -> None:
    assert client.get("/api/v1/health").json()["status"] == "ok"


def test_missing_and_bad_keys_are_problem_details(client: TestClient) -> None:
    for headers in ({}, {"Authorization": "Bearer nope"}):
        response = client.get("/api/v1/contacts", headers=headers)
        assert response.status_code == 401
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["type"] == "https://mailstudio.email/problems/unauthorized"


def test_scopes_are_enforced(client: TestClient, make_key: Callable[[str], Headers]) -> None:
    read_only = make_key("read")
    assert client.get("/api/v1/me", headers=read_only).json()["scopes"] == ["read"]
    response = client.post("/api/v1/lists", json={"name": "x"}, headers=read_only)
    assert response.status_code == 403
    assert "write" in response.json()["detail"]


def test_validation_errors_list_each_problem(client: TestClient, auth: Headers) -> None:
    response = client.post(
        "/api/v1/contacts", json={"email": "not-an-email", "tags": ["!!"], "colour": "red"}, headers=auth
    )
    assert response.status_code == 422
    locations = {e["location"] for e in response.json()["errors"]}
    assert {"body.email", "body.tags", "body.colour"} <= locations


def test_migrations_are_idempotent(settings: Settings, client: TestClient) -> None:
    assert migrate(settings) == []  # already applied at startup
    conn = connect(settings.database_path)
    from mailstudio.db import _migrations

    assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == len(_migrations())
    conn.close()


# ---------------------------------------------------------------------------
# Contacts


def test_create_contact_normalises_and_subscribes(client: TestClient, auth: Headers) -> None:
    lst = new_list(client, auth)
    contact = new_contact(
        client,
        auth,
        " Ada@Example.COM ",
        first_name="Ada",
        fields={"city": "Perth", "vip": True},
        tags=["Customer", " customer "],
        lists=[lst["id"]],
        consent=CONSENT,
    )
    assert contact["id"].startswith("ctc_")
    assert contact["email"] == "ada@example.com"
    assert contact["tags"] == ["customer"]
    assert contact["fields"] == {"city": "Perth", "vip": True}
    assert contact["lists"] == [{"list_id": lst["id"], "status": "subscribed"}]
    assert client.get(f"/api/v1/lists/{lst['id']}", headers=auth).json()["counts"]["subscribed"] == 1


def test_list_subscription_requires_consent(client: TestClient, auth: Headers) -> None:
    lst = new_list(client, auth)
    response = client.post(
        "/api/v1/contacts", json={"email": "a@example.com", "lists": [lst["id"]]}, headers=auth
    )
    assert response.status_code == 422
    assert response.json()["type"].endswith("consent-required")


def test_unknown_list_rolls_back_the_whole_request(client: TestClient, auth: Headers) -> None:
    response = client.post(
        "/api/v1/contacts",
        json={"email": "a@example.com", "lists": ["lst_missing"], "consent": CONSENT},
        headers=auth,
    )
    assert response.status_code == 422
    assert (
        client.get("/api/v1/contacts", params={"email": "a@example.com"}, headers=auth).json()["data"] == []
    )


def test_duplicate_email_conflicts_with_existing_id(client: TestClient, auth: Headers) -> None:
    first = new_contact(client, auth, "a@example.com")
    response = client.post("/api/v1/contacts", json={"email": "A@example.com"}, headers=auth)
    assert response.status_code == 409
    assert response.json()["contact_id"] == first["id"]


def test_reserved_and_bad_field_names_rejected(client: TestClient, auth: Headers) -> None:
    for fields in ({"email": "x"}, {"Bad Name": "x"}):
        response = client.post(
            "/api/v1/contacts", json={"email": "a@example.com", "fields": fields}, headers=auth
        )
        assert response.status_code == 422


def test_update_replaces_tags_and_fields_only_when_given(client: TestClient, auth: Headers) -> None:
    contact = new_contact(client, auth, "a@example.com", fields={"city": "Perth"}, tags=["one"])
    response = client.patch(f"/api/v1/contacts/{contact['id']}", json={"first_name": "Ada"}, headers=auth)
    assert response.json()["fields"] == {"city": "Perth"} and response.json()["tags"] == ["one"]
    response = client.patch(
        f"/api/v1/contacts/{contact['id']}", json={"tags": ["two"], "fields": {}}, headers=auth
    )
    assert response.json()["tags"] == ["two"] and response.json()["fields"] == {}


def test_delete_contact_keeps_suppression(client: TestClient, auth: Headers) -> None:
    contact = new_contact(client, auth, "gone@example.com")
    client.post("/api/v1/suppressions", json={"email": "gone@example.com"}, headers=auth)
    assert client.delete(f"/api/v1/contacts/{contact['id']}", headers=auth).status_code == 204
    assert client.get(f"/api/v1/contacts/{contact['id']}", headers=auth).status_code == 404
    assert client.get("/api/v1/suppressions/gone@example.com", headers=auth).status_code == 200


def test_pagination_walks_every_contact_once(client: TestClient, auth: Headers) -> None:
    created = {new_contact(client, auth, f"p{i}@example.com")["id"] for i in range(7)}
    seen: list[str] = []
    params: dict[str, Any] = {"limit": 3}
    while True:
        page = client.get("/api/v1/contacts", params=params, headers=auth).json()
        seen += [c["id"] for c in page["data"]]
        if not page["next_cursor"]:
            break
        params["cursor"] = page["next_cursor"]
    assert len(seen) == 7 and set(seen) == created


def test_filters_by_tag_and_list_status(client: TestClient, auth: Headers) -> None:
    lst = new_list(client, auth)
    a = new_contact(client, auth, "a@example.com", tags=["vip"], lists=[lst["id"]], consent=CONSENT)
    new_contact(client, auth, "b@example.com", lists=[lst["id"]], consent=CONSENT)
    client.post(f"/api/v1/lists/{lst['id']}/members/{a['id']}/unsubscribe", headers=auth)

    by_tag = client.get("/api/v1/contacts", params={"tag": "VIP"}, headers=auth).json()["data"]
    assert [c["email"] for c in by_tag] == ["a@example.com"]
    subscribed = client.get(
        "/api/v1/contacts", params={"list_id": lst["id"], "status": "subscribed"}, headers=auth
    ).json()["data"]
    assert [c["email"] for c in subscribed] == ["b@example.com"]


# ---------------------------------------------------------------------------
# Lists and members


def test_list_names_are_unique_case_insensitively(client: TestClient, auth: Headers) -> None:
    new_list(client, auth, "News")
    assert client.post("/api/v1/lists", json={"name": "news"}, headers=auth).status_code == 409


def test_unsubscribed_contacts_are_not_re_added_without_resubscribe(
    client: TestClient, auth: Headers
) -> None:
    lst = new_list(client, auth)
    contact = new_contact(client, auth, "a@example.com", lists=[lst["id"]], consent=CONSENT)
    members = f"/api/v1/lists/{lst['id']}/members"
    assert client.post(f"{members}/{contact['id']}/unsubscribe", headers=auth).status_code == 204
    assert (
        client.post(f"{members}/{contact['id']}/unsubscribe", headers=auth).status_code == 204
    )  # idempotent

    body = {"contact_ids": [contact["id"], "ctc_missing"], "consent": CONSENT}
    result = client.post(members, json=body, headers=auth).json()
    assert result["skipped_unsubscribed"] == [contact["id"]] and result["not_found"] == ["ctc_missing"]

    result = client.post(members, json={**body, "resubscribe": True}, headers=auth).json()
    assert result["added"] == [contact["id"]]


def test_consent_history_is_recorded(settings: Settings, client: TestClient, auth: Headers) -> None:
    lst = new_list(client, auth)
    contact = new_contact(
        client, auth, "a@example.com", lists=[lst["id"]], consent={**CONSENT, "ip": "203.0.113.9"}
    )
    client.post(
        f"/api/v1/lists/{lst['id']}/members/{contact['id']}/unsubscribe",
        json={"source": "Email link"},
        headers=auth,
    )
    conn = connect(settings.database_path)
    events = conn.execute(
        "SELECT action, source, ip FROM consent_events WHERE contact_id = ? ORDER BY created_at",
        (contact["id"],),
    ).fetchall()
    conn.close()
    assert [tuple(e) for e in events] == [
        ("consent", "Signed up on the website", "203.0.113.9"),
        ("unsubscribe", "Email link", None),
    ]


def test_deleting_a_list_keeps_contacts(client: TestClient, auth: Headers) -> None:
    lst = new_list(client, auth)
    contact = new_contact(client, auth, "a@example.com", lists=[lst["id"]], consent=CONSENT)
    assert client.delete(f"/api/v1/lists/{lst['id']}", headers=auth).status_code == 204
    assert client.get(f"/api/v1/contacts/{contact['id']}", headers=auth).json()["lists"] == []


# ---------------------------------------------------------------------------
# Idempotency


def test_idempotency_key_replays_without_rerunning(client: TestClient, auth: Headers) -> None:
    headers = {**auth, "Idempotency-Key": "abc-123"}
    first = client.post("/api/v1/lists", json={"name": "Once"}, headers=headers)
    again = client.post("/api/v1/lists", json={"name": "Once"}, headers=headers)
    assert first.status_code == again.status_code == 201
    assert again.json()["id"] == first.json()["id"]
    assert again.headers["Idempotent-Replayed"] == "true"
    assert len(client.get("/api/v1/lists", headers=auth).json()["data"]) == 1

    different = client.post("/api/v1/lists", json={"name": "Other"}, headers=headers)
    assert different.status_code == 422
    assert different.json()["type"].endswith("idempotency-key-reused")


def test_failed_requests_are_not_stored_under_their_key(client: TestClient, auth: Headers) -> None:
    new_list(client, auth, "Taken")
    headers = {**auth, "Idempotency-Key": "retry-me"}
    assert client.post("/api/v1/lists", json={"name": "Taken"}, headers=headers).status_code == 409
    client.delete(
        f"/api/v1/lists/{client.get('/api/v1/lists', headers=auth).json()['data'][0]['id']}", headers=auth
    )
    assert client.post("/api/v1/lists", json={"name": "Taken"}, headers=headers).status_code == 201


# ---------------------------------------------------------------------------
# Suppressions


def test_only_manual_suppressions_can_be_lifted(
    settings: Settings, client: TestClient, auth: Headers
) -> None:
    created = client.post(
        "/api/v1/suppressions", json={"email": "Me@Example.com", "detail": "asked"}, headers=auth
    )
    assert created.status_code == 201 and created.json()["email"] == "me@example.com"
    assert (
        client.post("/api/v1/suppressions", json={"email": "me@example.com"}, headers=auth).status_code == 200
    )

    conn = connect(settings.database_path)
    conn.execute(
        "INSERT INTO suppressions (email, reason, created_at)"
        " VALUES ('bounced@example.com', 'bounce', '2026-01-01')"
    )
    conn.close()
    assert client.delete("/api/v1/suppressions/bounced@example.com", headers=auth).status_code == 409
    assert client.delete("/api/v1/suppressions/me@example.com", headers=auth).status_code == 204


def test_new_migration_backs_up_first(settings: Settings, client: TestClient, monkeypatch: Any) -> None:
    from mailstudio import db

    original = db._migrations()
    version = original[-1][0] + 1
    name = f"{version:03d}_add_note.sql"
    extra = (version, name, "ALTER TABLE lists ADD COLUMN note TEXT NOT NULL DEFAULT '';")
    monkeypatch.setattr(db, "_migrations", lambda: [*original, extra])
    assert migrate(settings) == [name]
    backups = list(settings.backup_dir.glob(f"before-migration-{version:03d}-*.db"))
    assert len(backups) == 1
    old = connect(backups[0])
    assert "note" not in {r[1] for r in old.execute("PRAGMA table_info(lists)")}
    old.close()
