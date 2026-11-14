from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mailstudio.app import create_app
from mailstudio.cli import main as cli
from mailstudio.config import Settings

Headers = dict[str, str]
OWNER = {"name": "Gavin", "email": "Owner@Acme.example", "password": "correct horse battery"}


def set_up(client: TestClient) -> str:
    response = client.post("/api/v1/auth/setup", json=OWNER)
    assert response.status_code == 200, response.text
    return str(response.json()["csrf_token"])


def test_first_run_setup_happens_once(client: TestClient) -> None:
    assert client.get("/api/v1/auth/status").json() == {"setup_required": True, "user": None}
    weak = client.post("/api/v1/auth/setup", json={**OWNER, "password": "short"})
    assert weak.status_code == 422 and weak.json()["type"].endswith("weak-password")

    set_up(client)
    status = client.get("/api/v1/auth/status").json()
    assert status["setup_required"] is False and status["user"]["user"]["email"] == "owner@acme.example"
    assert client.post("/api/v1/auth/setup", json={**OWNER, "email": "x@acme.example"}).status_code == 409


def test_session_uses_the_api_with_csrf_on_changes(client: TestClient) -> None:
    csrf = set_up(client)
    assert client.get("/api/v1/contacts").status_code == 200
    blocked = client.post("/api/v1/lists", json={"name": "News"})
    assert blocked.status_code == 403 and blocked.json()["type"].endswith("csrf")
    assert (
        client.post("/api/v1/lists", json={"name": "News"}, headers={"X-CSRF-Token": "wrong"}).status_code
        == 403
    )
    assert (
        client.post("/api/v1/lists", json={"name": "News"}, headers={"X-CSRF-Token": csrf}).status_code == 201
    )


def test_cookie_flags(client: TestClient) -> None:
    response = client.post("/api/v1/auth/setup", json=OWNER)
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    assert "secure" not in cookie  # plain http in tests; https installs get Secure


def test_login_logout_and_throttling(client: TestClient) -> None:
    csrf = set_up(client)
    client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert client.get("/api/v1/contacts").status_code == 401

    wrong = client.post("/api/v1/auth/login", json={"email": OWNER["email"], "password": "nope nope nope"})
    assert wrong.status_code == 401
    assert client.post(
        "/api/v1/auth/login", json={"email": OWNER["email"], "password": OWNER["password"]}
    ).is_success
    assert client.get("/api/v1/auth/me").json()["user"]["name"] == "Gavin"

    for _ in range(10):
        client.post("/api/v1/auth/login", json={"email": OWNER["email"], "password": "guessing guess"})
    locked = client.post("/api/v1/auth/login", json={"email": OWNER["email"], "password": OWNER["password"]})
    assert locked.status_code == 429


def test_password_change_logs_out_other_sessions(settings: Settings) -> None:
    app = create_app(settings)
    with TestClient(app) as laptop, TestClient(app) as phone:
        csrf = set_up(laptop)
        phone.post("/api/v1/auth/login", json={"email": OWNER["email"], "password": OWNER["password"]})
        assert phone.get("/api/v1/auth/me").status_code == 200
        body = {"current_password": OWNER["password"], "new_password": "an even better passphrase"}
        assert (
            laptop.post("/api/v1/auth/password", json=body, headers={"X-CSRF-Token": csrf}).status_code == 204
        )
        assert phone.get("/api/v1/auth/me").status_code == 401
        assert laptop.get("/api/v1/auth/me").status_code == 200


def test_users_are_managed_by_logged_in_people_only(
    client: TestClient, make_key: Callable[[str], Headers]
) -> None:
    key = make_key("read write send")
    assert client.get("/api/v1/users", headers=key).status_code == 401  # API keys can't manage logins

    csrf = {"X-CSRF-Token": set_up(client)}
    staff = {"name": "Sam", "email": "sam@acme.example", "password": "a good long password"}
    added = client.post("/api/v1/users", json=staff, headers=csrf)
    assert added.status_code == 201 and "password" not in added.text
    assert client.post("/api/v1/users", json=staff, headers=csrf).status_code == 409
    users = client.get("/api/v1/users").json()["data"]
    me = next(u for u in users if u["email"] == "owner@acme.example")
    assert client.delete(f"/api/v1/users/{me['id']}", headers=csrf).status_code == 409
    assert client.delete(f"/api/v1/users/{added.json()['id']}", headers=csrf).status_code == 204


def test_reset_password_command(
    settings: Settings,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    set_up(client)
    monkeypatch.setenv("MAILSTUDIO_DATA_DIR", str(settings.data_dir))
    assert cli(["reset-password", "--email", "owner@acme.example"]) == 0
    new_password = capsys.readouterr().out.split("\n\n")[1].strip()
    assert client.get("/api/v1/auth/me").status_code == 401  # logged out everywhere
    login = {"email": "owner@acme.example", "password": new_password}
    assert client.post("/api/v1/auth/login", json=login).status_code == 200


def test_web_app_is_served_with_route_fallback(tmp_path: Path) -> None:
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html><title>app</title>")
    (web / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("nope")
    settings = Settings(data_dir=tmp_path / "data", web_dir=web)
    with TestClient(create_app(settings)) as client:
        assert "<title>app</title>" in client.get("/").text
        assert "<title>app</title>" in client.get("/campaigns/cmp_123").text
        assert client.get("/app.js").text == "console.log(1)"
        assert "nope" not in client.get("/../secret.txt").text
        missing = client.get("/api/v1/does-not-exist")
        assert missing.status_code == 404 and missing.headers["content-type"] == "application/problem+json"
