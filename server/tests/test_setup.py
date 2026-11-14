"""The setup checklist, automatic notification setup in AWS, and backups."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import boto3
import pytest
from botocore.stub import Stubber
from fastapi.testclient import TestClient

from mailstudio import aws_setup, cli
from mailstudio.app import create_app
from mailstudio.config import Settings

Headers = dict[str, str]
TOPIC = "arn:aws:sns:ap-southeast-2:123456789012:mailstudio-notifications"
ENDPOINT = "https://mail.bakery.example/webhooks/ses"


class StubbedClients(aws_setup.Clients):
    """Real boto3 clients whose calls are answered by botocore's Stubber."""

    def __init__(self) -> None:
        options: dict[str, Any] = {
            "region_name": "ap-southeast-2",
            "aws_access_key_id": "AKIA",
            "aws_secret_access_key": "secret",
        }
        self.sns = boto3.client("sns", **options)
        self.sts = boto3.client("sts", **options)
        self.ses = boto3.client("sesv2", **options)
        self.stubs = [Stubber(self.sns), Stubber(self.sts), Stubber(self.ses)]
        for stub in self.stubs:
            stub.activate()

    def done(self) -> None:
        for stub in self.stubs:
            stub.assert_no_pending_responses()


def expect_prepare(clients: StubbedClients, *, already_exists: bool) -> None:
    sns, sts, ses = clients.stubs
    sts.add_response(
        "get_caller_identity",
        {
            "Account": "123456789012",
            "Arn": "arn:aws:iam::123456789012:user/mailstudio",
            "UserId": "AIDAEXAMPLEUSERID1",
        },
    )
    sns.add_response("create_topic", {"TopicArn": TOPIC}, {"Name": "mailstudio-notifications"})
    sns.add_response("set_topic_attributes", {})
    if already_exists:
        ses.add_client_error("create_configuration_set", "AlreadyExistsException")
        ses.add_client_error("create_configuration_set_event_destination", "AlreadyExistsException")
        ses.add_response("update_configuration_set_event_destination", {})
    else:
        ses.add_response("create_configuration_set", {})
        ses.add_response("create_configuration_set_event_destination", {})


@pytest.mark.parametrize("already_exists", [False, True])
def test_prepare_creates_or_reuses_everything(already_exists: bool) -> None:
    clients = StubbedClients()
    expect_prepare(clients, already_exists=already_exists)
    assert aws_setup.prepare(clients) == TOPIC
    clients.done()


def test_subscription_status() -> None:
    clients = StubbedClients()
    sns = clients.stubs[0]
    for arn, expected in (("PendingConfirmation", "pending"), (f"{TOPIC}:abc", "confirmed")):
        sns.add_response(
            "list_subscriptions_by_topic",
            {
                "Subscriptions": [
                    {"Endpoint": "https://other.example/x", "SubscriptionArn": "x"},
                    {"Endpoint": ENDPOINT, "SubscriptionArn": arn},
                ]
            },
        )
        assert aws_setup.subscription_status(clients, TOPIC, ENDPOINT) == expected
    sns.add_response("list_subscriptions_by_topic", {"Subscriptions": []})
    assert aws_setup.subscription_status(clients, TOPIC, ENDPOINT) == "none"


@pytest.fixture
def https_client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(Settings(data_dir=tmp_path / "data", base_url="https://mail.bakery.example"))
    with TestClient(app, base_url="https://mail.bakery.example") as client:
        yield client


def first_user(client: TestClient) -> Headers:
    response = client.post(
        "/api/v1/auth/setup",
        json={"name": "Gavin", "email": "gavin@bakery.example", "password": "correct horse battery staple"},
    )
    assert response.status_code in (200, 201), response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def test_notification_setup_saves_then_subscribes(
    https_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    csrf = first_user(https_client)
    https_client.patch(
        "/api/v1/settings",
        json={"ses": {"region": "ap-southeast-2", "access_key_id": "AKIA", "secret_access_key": "s"}},
        headers=csrf,
    )
    clients = StubbedClients()
    expect_prepare(clients, already_exists=False)
    clients.stubs[0].add_response(
        "subscribe",
        {"SubscriptionArn": "pending confirmation"},
        {"TopicArn": TOPIC, "Protocol": "https", "Endpoint": ENDPOINT},
    )
    clients.stubs[0].add_response(
        "list_subscriptions_by_topic",
        {"Subscriptions": [{"Endpoint": ENDPOINT, "SubscriptionArn": "PendingConfirmation"}]},
    )
    monkeypatch.setattr(aws_setup, "Clients", lambda _config: clients)

    response = https_client.post("/api/v1/sending/notifications", headers=csrf)
    assert response.status_code == 200, response.text
    assert response.json() == {"topic_arn": TOPIC, "configuration_set": "mailstudio", "status": "pending"}
    clients.done()
    ses = https_client.get("/api/v1/settings").json()["ses"]
    assert ses["notification_topic_arn"] == TOPIC and ses["configuration_set"] == "mailstudio"
    assert ses["secret_set"]  # the secret survived the update


def test_notification_setup_needs_https_and_ses(client: TestClient, auth: Headers) -> None:
    assert client.post("/api/v1/sending/notifications", headers=auth).status_code == 409
    client.patch(
        "/api/v1/settings",
        json={"ses": {"region": "ap-southeast-2", "access_key_id": "AKIA", "secret_access_key": "s"}},
        headers=auth,
    )
    response = client.post("/api/v1/sending/notifications", headers=auth)
    assert response.status_code == 422 and response.json()["type"].endswith("needs-https")


def test_access_denied_explains_the_permissions(
    https_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    csrf = first_user(https_client)
    https_client.patch(
        "/api/v1/settings",
        json={"ses": {"region": "ap-southeast-2", "access_key_id": "AKIA", "secret_access_key": "s"}},
        headers=csrf,
    )
    clients = StubbedClients()
    clients.stubs[1].add_client_error("get_caller_identity", "AccessDenied", "not allowed")
    monkeypatch.setattr(aws_setup, "Clients", lambda _config: clients)
    response = https_client.post("/api/v1/sending/notifications", headers=csrf)
    assert (
        response.status_code == 502 and "permissions listed in the setup guide" in response.json()["detail"]
    )
    policy = https_client.get("/api/v1/sending/iam-policy").json()
    assert "sns:Subscribe" in policy["Statement"][0]["Action"]


def test_checklist_without_aws(client: TestClient, auth: Headers) -> None:
    def steps() -> dict[str, bool]:
        body = client.get("/api/v1/setup", headers=auth).json()
        return {s["key"]: s["done"] for s in body["steps"]}

    assert not any(steps().values())
    client.patch(
        "/api/v1/settings",
        json={
            "business": {"name": "Bakery", "address": "12 High St"},
            "sender": {"from_name": "Bakery", "from_email": "news@bakery.example"},
        },
        headers=auth,
    )
    list_id = client.post("/api/v1/lists", json={"name": "News"}, headers=auth).json()["id"]
    client.post(
        "/api/v1/contacts",
        json={"email": "a@example.com", "lists": [list_id], "consent": {"source": "Asked in person"}},
        headers=auth,
    )
    done = steps()
    assert done["business"] and done["sender"] and done["audience"]
    assert not done["ses"] and not done["domain"]
    assert client.get("/api/v1/setup", headers=auth).json()["domain"] == "bakery.example"


def test_backup_is_a_complete_standalone_copy(
    client: TestClient, auth: Headers, settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.post("/api/v1/lists", json={"name": "News"}, headers=auth)
    monkeypatch.setenv("MAILSTUDIO_DATA_DIR", str(settings.data_dir))
    target = tmp_path / "copy.db"
    assert cli.main(["backup", str(target)]) == 0
    assert not Path(f"{target}-wal").exists()
    copy = sqlite3.connect(target)
    assert copy.execute("SELECT name FROM lists").fetchall() == [("News",)]
    copy.close()
