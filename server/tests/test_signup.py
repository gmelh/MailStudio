"""Public signup forms and double opt-in."""

from __future__ import annotations

import re
from email.message import EmailMessage
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient

from mailstudio import signup, tokens
from mailstudio.config import Settings
from mailstudio.db import connect, transaction
from mailstudio.providers import Outcome, Result
from mailstudio.repositories import settings as settings_repo

Headers = dict[str, str]


class Outbox:
    sent: ClassVar[list[EmailMessage]] = []

    def send(self, message: EmailMessage) -> Result:
        Outbox.sent.append(message)
        return Result(Outcome.SENT, "msg")

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def outbox(monkeypatch: pytest.MonkeyPatch) -> type[Outbox]:
    Outbox.sent = []
    monkeypatch.setattr(signup, "configured_provider", lambda conn: Outbox())
    return Outbox


def make_list(client: TestClient, auth: Headers, *, double_opt_in: bool = True, **form: Any) -> str:
    client.patch(
        "/api/v1/settings",
        json={
            "sender": {"from_name": "Bakery", "from_email": "news@bakery.example"},
            "business": {"name": "Fremantle Bakery", "address": "12 High St"},
        },
        headers=auth,
    )
    list_id = str(
        client.post(
            "/api/v1/lists", json={"name": "News", "double_opt_in": double_opt_in}, headers=auth
        ).json()["id"]
    )
    response = client.patch(
        f"/api/v1/lists/{list_id}", json={"signup": {"enabled": True, **form}}, headers=auth
    )
    assert response.status_code == 200, response.text
    return list_id


def join(client: TestClient, list_id: str, email: str, **extra: str) -> Any:
    return client.post(f"/f/{list_id}", data={"email": email, **extra}, follow_redirects=False)


def member_status(client: TestClient, auth: Headers, list_id: str, email: str) -> str | None:
    for status in ("pending", "subscribed", "unsubscribed"):
        page = client.get(f"/api/v1/lists/{list_id}/members", params={"status": status}, headers=auth).json()
        if any(m["email"] == email for m in page["data"]):
            return status
    return None


def confirm_url(message: EmailMessage) -> str:
    body = message.get_body(("plain",))
    assert body is not None
    found = re.search(r"http://\S+/c/(\S+)", body.get_content())
    assert found
    return f"/c/{found.group(1)}"


def test_form_is_hidden_until_enabled(client: TestClient, auth: Headers) -> None:
    list_id = client.post("/api/v1/lists", json={"name": "News"}, headers=auth).json()["id"]
    assert client.get(f"/f/{list_id}").status_code == 404
    assert join(client, list_id, "a@example.com").status_code == 404
    client.patch(
        f"/api/v1/lists/{list_id}", json={"signup": {"enabled": True, "title": "Bread news"}}, headers=auth
    )
    page = client.get(f"/f/{list_id}")
    assert page.status_code == 200 and "Bread news" in page.text and 'name="email"' in page.text
    assert client.get("/f/lst_01J00000000000000000000000").status_code == 404


def test_double_opt_in_waits_for_the_button_not_the_link(
    client: TestClient, auth: Headers, outbox: type[Outbox]
) -> None:
    list_id = make_list(client, auth)
    response = join(client, list_id, " Ada@Example.com ", first_name="Ada")
    assert response.status_code == 200 and "confirm" in response.text
    assert member_status(client, auth, list_id, "ada@example.com") == "pending"

    assert len(outbox.sent) == 1
    message = outbox.sent[0]
    assert message["To"] == "ada@example.com" and "News" in message["Subject"]
    assert "List-Unsubscribe" not in message  # not a campaign
    url = confirm_url(message)

    assert "Yes, subscribe me" in client.get(url).text  # opening the link (or a mail scanner) ...
    assert member_status(client, auth, list_id, "ada@example.com") == "pending"  # ... changes nothing

    assert "Thanks for confirming" in client.post(url).text
    assert member_status(client, auth, list_id, "ada@example.com") == "subscribed"
    assert client.post(url).status_code == 200  # a second click is harmless

    contact = client.get("/api/v1/contacts", params={"email": "ada@example.com"}, headers=auth).json()[
        "data"
    ][0]
    assert contact["first_name"] == "Ada"
    history = client.get(f"/api/v1/contacts/{contact['id']}/history", headers=auth).json()
    assert [e["action"] for e in history] == ["confirm", "consent"]
    assert history[1]["source"] == "Signup form (News)"


def test_without_double_opt_in_people_join_straight_away(
    client: TestClient, auth: Headers, outbox: type[Outbox]
) -> None:
    list_id = make_list(client, auth, double_opt_in=False, thanks="Welcome aboard!")
    response = join(client, list_id, "grace@example.com")
    assert "Welcome aboard!" in response.text
    assert member_status(client, auth, list_id, "grace@example.com") == "subscribed"
    assert outbox.sent == []


def test_signing_up_again_after_unsubscribing_is_allowed(client: TestClient, auth: Headers) -> None:
    list_id = make_list(client, auth, double_opt_in=False)
    join(client, list_id, "grace@example.com")
    contact = client.get("/api/v1/contacts", params={"email": "grace@example.com"}, headers=auth).json()[
        "data"
    ][0]
    client.post(f"/api/v1/lists/{list_id}/members/{contact['id']}/unsubscribe", headers=auth)
    join(client, list_id, "grace@example.com")
    assert member_status(client, auth, list_id, "grace@example.com") == "subscribed"


def test_known_and_blocked_addresses_get_the_same_answer_and_no_email(
    client: TestClient, auth: Headers, outbox: type[Outbox]
) -> None:
    list_id = make_list(client, auth)
    client.post("/api/v1/suppressions", json={"email": "blocked@example.com"}, headers=auth)
    first = join(client, list_id, "ada@example.com")
    client.post(confirm_url(outbox.sent[0]))
    outbox.sent.clear()

    for email in ("blocked@example.com", "ada@example.com"):
        assert join(client, list_id, email).text == first.text
    assert outbox.sent == []
    assert member_status(client, auth, list_id, "blocked@example.com") is None


def test_repeat_signups_send_one_email(client: TestClient, auth: Headers, outbox: type[Outbox]) -> None:
    list_id = make_list(client, auth)
    for _ in range(3):
        join(client, list_id, "ada@example.com")
    assert len(outbox.sent) == 1


def test_bots_bad_addresses_and_floods(client: TestClient, auth: Headers, outbox: type[Outbox]) -> None:
    list_id = make_list(client, auth)
    assert join(client, list_id, "bot@example.com", website="http://spam.example").status_code == 200
    assert member_status(client, auth, list_id, "bot@example.com") is None and outbox.sent == []

    bad = join(client, list_id, "not an email")
    assert (
        bad.status_code == 400
        and "look like an email address" in bad.text
        and 'value="not an email"' in bad.text
    )

    client.app.state.signup_guard = signup.SignupGuard(per_ip=2)  # type: ignore[attr-defined]
    assert join(client, list_id, "one@example.com").status_code == 200
    assert join(client, list_id, "two@example.com").status_code == 200
    assert join(client, list_id, "three@example.com").status_code == 429


def test_redirects_after_signup_and_rejects_unsafe_addresses(client: TestClient, auth: Headers) -> None:
    list_id = make_list(client, auth, redirect_url="https://bakery.example/thanks")
    response = join(client, list_id, "ada@example.com")
    assert response.status_code == 303 and response.headers["location"] == "https://bakery.example/thanks"
    bad = client.patch(
        f"/api/v1/lists/{list_id}",
        json={"signup": {"enabled": True, "redirect_url": "javascript:alert(1)"}},
        headers=auth,
    )
    assert bad.status_code == 422


def test_forged_swapped_and_expired_links(
    client: TestClient, auth: Headers, settings: Settings, outbox: type[Outbox]
) -> None:
    list_id = make_list(client, auth)
    join(client, list_id, "ada@example.com")
    url = confirm_url(outbox.sent[0])
    contact_id, _, _ = url.removeprefix("/c/").split(".")

    assert client.post(url[:-2] + "xx").status_code == 400
    conn = connect(settings.database_path)
    with transaction(conn):
        secret = settings_repo.secret_key(conn)
    unsubscribe_token = tokens.make(secret, contact_id, list_id)  # right IDs, wrong purpose
    assert client.post(f"/c/{unsubscribe_token}").status_code == 400

    with transaction(conn):
        conn.execute("UPDATE list_members SET updated_at = '2020-01-01T00:00:00.000Z'")
    conn.close()
    assert client.post(url).status_code == 410
    assert member_status(client, auth, list_id, "ada@example.com") == "pending"


def test_confirmation_email_is_escaped(client: TestClient, auth: Headers, outbox: type[Outbox]) -> None:
    list_id = make_list(client, auth)
    client.patch(f"/api/v1/lists/{list_id}", json={"name": "<b>News</b>"}, headers=auth)
    join(client, list_id, "ada@example.com")
    body = outbox.sent[0].get_body(("html",))
    assert body is not None
    assert "&lt;b&gt;News&lt;/b&gt;" in body.get_content() and "<b>News</b>" not in body.get_content()
