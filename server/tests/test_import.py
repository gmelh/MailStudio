from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from .conftest import CONSENT

Headers = dict[str, str]


def upload(client: TestClient, auth: Headers, csv_text: str | bytes, **form: str) -> Any:
    data = csv_text.encode() if isinstance(csv_text, str) else csv_text
    form.setdefault("consent_source", "Existing customers who opted in at checkout")
    return client.post(
        "/api/v1/contacts/import",
        files={"file": ("customers.csv", data, "text/csv")},
        data=form,
        headers=auth,
    )


def test_import_creates_updates_and_reports_bad_rows(client: TestClient, auth: Headers) -> None:
    lst = client.post("/api/v1/lists", json={"name": "Customers"}, headers=auth).json()
    client.post("/api/v1/contacts", json={"email": "old@example.com", "first_name": "Old"}, headers=auth)

    csv_text = (
        "Email,First Name,Last Name,City\n"
        "new@example.com,Ada,Lovelace,Perth\n"
        "OLD@example.com,,Timer,Fremantle\n"
        "not-an-email,X,Y,Z\n"
    )
    response = upload(client, auth, csv_text, list_id=lst["id"], tags="imported, Customer")
    assert response.status_code == 200, response.text
    result = response.json()
    assert (
        result["rows"],
        result["created"],
        result["updated"],
        result["added_to_list"],
        result["invalid"],
    ) == (3, 1, 1, 2, 1)
    assert result["problems"] == [{"row": 4, "error": "Invalid email address: 'not-an-email'"}]

    old = client.get("/api/v1/contacts", params={"email": "old@example.com"}, headers=auth).json()["data"][0]
    assert old["first_name"] == "Old" and old["last_name"] == "Timer"  # blanks don't overwrite
    assert old["fields"] == {"city": "Fremantle"}
    assert old["tags"] == ["customer", "imported"]


def test_import_never_re_adds_unsubscribed_or_suppressed(client: TestClient, auth: Headers) -> None:
    lst = client.post("/api/v1/lists", json={"name": "News"}, headers=auth).json()
    left = client.post(
        "/api/v1/contacts",
        json={"email": "left@example.com", "lists": [lst["id"]], "consent": CONSENT},
        headers=auth,
    ).json()
    client.post(f"/api/v1/lists/{lst['id']}/members/{left['id']}/unsubscribe", headers=auth)
    client.post("/api/v1/suppressions", json={"email": "blocked@example.com"}, headers=auth)

    result = upload(client, auth, "email\nleft@example.com\nblocked@example.com\n", list_id=lst["id"]).json()
    assert (
        result["skipped_unsubscribed"] == 1
        and result["skipped_suppressed"] == 1
        and result["added_to_list"] == 0
    )


def test_import_accepts_semicolons_bom_and_cp1252(client: TestClient, auth: Headers) -> None:
    result = upload(client, auth, "﻿email;first_name\nzoe@example.com;Zoë\n").json()
    assert result["created"] == 1
    result = upload(client, auth, "email,first_name\nrene@example.com,René\n".encode("cp1252")).json()
    assert result["created"] == 1
    rene = client.get("/api/v1/contacts", params={"email": "rene@example.com"}, headers=auth).json()["data"][
        0
    ]
    assert rene["first_name"] == "René"


def test_import_requires_email_column_and_known_list(client: TestClient, auth: Headers) -> None:
    assert upload(client, auth, "name\nAda\n").json()["type"].endswith("no-email-column")
    response = upload(client, auth, "email\na@example.com\n", list_id="lst_missing")
    assert response.status_code == 422
    assert client.get("/api/v1/contacts", headers=auth).json()["data"] == []  # rolled back
