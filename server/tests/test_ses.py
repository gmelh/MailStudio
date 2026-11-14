from __future__ import annotations

import base64
import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any

import boto3
import pytest
from botocore.exceptions import ReadTimeoutError
from botocore.stub import ANY, Stubber
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from mailstudio import domain, notifications, ses
from mailstudio.config import Settings
from mailstudio.db import connect
from mailstudio.providers import Outcome, ProviderUnavailable, configured_provider
from mailstudio.repositories.settings import SesSettings
from mailstudio.worker import Worker

from .conftest import CONSENT

Headers = dict[str, str]
TOPIC = "arn:aws:sns:ap-southeast-2:123456789012:mailstudio-ses"
CERT_URL = "https://sns.ap-southeast-2.amazonaws.com/SimpleNotificationService-test.pem"
SES_CONFIG = SesSettings(
    region="ap-southeast-2",
    access_key_id="AKIATEST",
    secret_access_key="secret",
    notification_topic_arn=TOPIC,
)


def stubbed() -> tuple[Any, Stubber]:
    client = boto3.client(
        "sesv2", region_name="ap-southeast-2", aws_access_key_id="x", aws_secret_access_key="y"
    )
    return client, Stubber(client)


def message() -> EmailMessage:
    email = EmailMessage()
    email["From"], email["To"], email["Subject"] = "a@acme.example", "b@example.com", "Hi"
    email.set_content("Hello")
    return email


# ---------------------------------------------------------------------------
# Sending through SES


def test_ses_outcomes() -> None:
    client, stub = stubbed()
    provider = ses.SesProvider(SES_CONFIG, client)
    stub.add_response("send_email", {"MessageId": "0100-abc"}, {"Content": {"Raw": {"Data": ANY}}})
    stub.add_client_error("send_email", "TooManyRequestsException", "Maximum sending rate exceeded.")
    stub.add_client_error("send_email", "MessageRejected", "Address blacklisted.")
    with stub:
        assert provider.send(message()) == ses.Result(Outcome.SENT, message_id="0100-abc")
        assert provider.send(message()).outcome is Outcome.RETRY
        assert provider.send(message()).outcome is Outcome.FAILED


@pytest.mark.parametrize(
    ("code", "text"),
    [
        ("AccountSuspendedException", "Suspended"),
        ("SendingPausedException", "Paused"),
        ("MessageRejected", "Email address is not verified. The following identities failed the check"),
    ],
)
def test_account_level_problems_stop_sending_instead_of_failing_each_email(code: str, text: str) -> None:
    client, stub = stubbed()
    stub.add_client_error("send_email", code, text)
    with stub, pytest.raises(ProviderUnavailable):
        ses.SesProvider(SES_CONFIG, client).send(message())


def test_timeout_after_sending_is_never_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = stubbed()

    def timeout(**_: Any) -> None:
        raise ReadTimeoutError(endpoint_url="https://email.ap-southeast-2.amazonaws.com")

    monkeypatch.setattr(client, "send_email", timeout)
    result = ses.SesProvider(SES_CONFIG, client).send(message())
    assert result.outcome is Outcome.FAILED and "delivery unknown" in (result.error or "")


def test_ses_is_used_once_connected(settings: Settings, client: TestClient, auth: Headers) -> None:
    conn = connect(settings.database_path)
    with pytest.raises(ProviderUnavailable, match="Amazon SES"):
        configured_provider(conn)
    client.patch(
        "/api/v1/settings",
        headers=auth,
        json={
            "ses": {"region": "ap-southeast-2", "access_key_id": "AKIATEST", "secret_access_key": "s3cret"}
        },
    )
    assert isinstance(configured_provider(conn), ses.SesProvider)
    conn.close()

    shown = client.get("/api/v1/settings", headers=auth).json()["ses"]
    assert shown["secret_set"] is True and "s3cret" not in json.dumps(shown)
    client.patch(
        "/api/v1/settings", headers=auth, json={"ses": {"region": "eu-west-1", "access_key_id": "AKIA2"}}
    )
    assert client.get("/api/v1/settings", headers=auth).json()["ses"]["secret_set"] is True  # kept


# ---------------------------------------------------------------------------
# SNS notifications


@pytest.fixture
def signer(monkeypatch: pytest.MonkeyPatch) -> Callable[..., dict[str, Any]]:
    """Signs SNS messages with a throwaway certificate served as Amazon's."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "sns.amazonaws.com")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM)
    monkeypatch.setattr(notifications, "fetch_certificate", lambda url: pem)

    def sign(fields: dict[str, Any], *, version: str = "2", cert_url: str = CERT_URL) -> dict[str, Any]:
        msg = {
            "TopicArn": TOPIC,
            "MessageId": "sns-1",
            "Timestamp": "2026-11-13T00:00:00.000Z",
            "SignatureVersion": version,
            "SigningCertURL": cert_url,
            **fields,
        }
        signed = "".join(f"{f}\n{msg[f]}\n" for f in notifications._SIGNED_FIELDS[msg["Type"]] if f in msg)
        algorithm = hashes.SHA1() if version == "1" else hashes.SHA256()
        msg["Signature"] = base64.b64encode(key.sign(signed.encode(), padding.PKCS1v15(), algorithm)).decode()
        return msg

    return sign


def ses_event(kind: str, message_id: str, **details: Any) -> str:
    return json.dumps({"notificationType": kind, "mail": {"messageId": message_id}, **details})


def bounce(email: str, message_id: str = "msg-1", bounce_type: str = "Permanent") -> str:
    return ses_event(
        "Bounce",
        message_id,
        bounce={
            "bounceType": bounce_type,
            "bounceSubType": "General",
            "bouncedRecipients": [{"emailAddress": email, "diagnosticCode": "smtp; 550 5.1.1 user unknown"}],
        },
    )


@pytest.fixture
def connected(client: TestClient, auth: Headers) -> None:
    client.patch("/api/v1/settings", headers=auth, json={"ses": SES_CONFIG.model_dump()})


def post(client: TestClient, body: dict[str, Any]) -> Any:
    return client.post("/webhooks/ses", content=json.dumps(body), headers={"content-type": "text/plain"})


def test_permanent_bounces_and_complaints_suppress(
    client: TestClient, auth: Headers, connected: None, signer: Callable[..., dict[str, Any]]
) -> None:
    assert (
        post(client, signer({"Type": "Notification", "Message": bounce("Gone@Example.com")})).status_code
        == 200
    )
    complaint = ses_event(
        "Complaint",
        "msg-2",
        complaint={
            "complainedRecipients": [{"emailAddress": "angry@example.com"}],
            "complaintFeedbackType": "abuse",
        },
    )
    assert (
        post(client, signer({"Type": "Notification", "Message": complaint}, version="1")).status_code == 200
    )
    assert (
        post(
            client,
            signer({"Type": "Notification", "Message": bounce("busy@example.com", "msg-3", "Transient")}),
        ).status_code
        == 200
    )

    reasons = {
        s["email"]: s["reason"] for s in client.get("/api/v1/suppressions", headers=auth).json()["data"]
    }
    assert reasons == {"gone@example.com": "bounce", "angry@example.com": "complaint"}


def test_untrusted_notifications_are_refused(
    client: TestClient, connected: None, signer: Callable[..., dict[str, Any]], auth: Headers
) -> None:
    good = signer({"Type": "Notification", "Message": bounce("x@example.com")})
    assert post(client, {**good, "Message": bounce("victim@example.com")}).status_code == 400  # tampered
    other_topic = signer(
        {
            "Type": "Notification",
            "Message": bounce("x@example.com"),
            "TopicArn": "arn:aws:sns:us-east-1:999999999999:attacker",
        }
    )
    assert post(client, other_topic).status_code == 403
    fake_cert = signer(
        {"Type": "Notification", "Message": bounce("x@example.com")}, cert_url="https://evil.example/cert.pem"
    )
    assert post(client, fake_cert).status_code == 400
    assert client.post("/webhooks/ses", content="not json").status_code == 400
    assert client.get("/api/v1/suppressions", headers=auth).json()["data"] == []


def test_subscription_is_confirmed_only_for_our_topic(
    client: TestClient,
    connected: None,
    signer: Callable[..., dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    visited: list[str] = []
    monkeypatch.setattr(notifications, "confirm_subscription", visited.append)
    url = "https://sns.ap-southeast-2.amazonaws.com/?Action=ConfirmSubscription&Token=t"
    confirm = {"Type": "SubscriptionConfirmation", "Message": "Confirm", "Token": "t", "SubscribeURL": url}
    assert post(client, signer(confirm)).status_code == 200
    assert post(client, signer({**confirm, "TopicArn": "arn:aws:sns:us-east-1:9:other"})).status_code == 403
    assert visited == [url]


def test_notifications_are_refused_until_a_topic_is_configured(
    client: TestClient, signer: Callable[..., dict[str, Any]]
) -> None:
    assert (
        post(client, signer({"Type": "Notification", "Message": bounce("x@example.com")})).status_code == 403
    )


# ---------------------------------------------------------------------------
# End to end: send through SES, then a bounce comes back


def test_bounce_is_linked_to_its_campaign(
    settings: Settings,
    client: TestClient,
    make_key: Callable[[str], Headers],
    connected: None,
    signer: Callable[..., dict[str, Any]],
) -> None:
    key = make_key("read write send")
    client.patch(
        "/api/v1/settings",
        headers=key,
        json={
            "business": {"name": "Acme", "address": "1 Hay St"},
            "sender": {"from_email": "news@acme.example"},
        },
    )
    lst = client.post("/api/v1/lists", json={"name": "News"}, headers=key).json()["id"]
    for email in ("ok@example.com", "gone@example.com"):
        client.post(
            "/api/v1/contacts", json={"email": email, "lists": [lst], "consent": CONSENT}, headers=key
        )
    campaign = client.post(
        "/api/v1/campaigns", json={"name": "N", "subject": "S", "lists": [lst]}, headers=key
    )
    campaign_id = campaign.json()["id"]
    client.post(f"/api/v1/campaigns/{campaign_id}/schedule", headers=key)

    ses_client, stub = stubbed()
    for i in range(2):
        stub.add_response("send_email", {"MessageId": f"ses-{i}"}, {"Content": {"Raw": {"Data": ANY}}})
    worker = Worker(
        settings,
        lambda _: ses.SesProvider(SES_CONFIG, ses_client),
        sleep=lambda _: None,
        clock=lambda: datetime.now(UTC) + timedelta(seconds=1),
    )
    with stub:
        for _ in range(3):
            worker.tick()
    sent_ids = dict(worker.conn.execute("SELECT email, provider_message_id FROM send_jobs").fetchall())
    worker.conn.close()

    gone = bounce("gone@example.com", sent_ids["gone@example.com"])
    delivered = ses_event("Delivery", sent_ids["ok@example.com"], delivery={"recipients": ["ok@example.com"]})
    for event in (gone, gone, delivered):  # SNS can deliver the same notification twice
        assert post(client, signer({"Type": "Notification", "Message": event})).status_code == 200

    stats = client.get(f"/api/v1/campaigns/{campaign_id}/stats", headers=key).json()
    assert (stats["sent"], stats["delivered"], stats["bounced"], stats["complained"]) == (2, 1, 1, 0)
    assert client.get("/api/v1/suppressions/gone@example.com", headers=key).json()["reason"] == "bounce"


# ---------------------------------------------------------------------------
# Sending setup: account and domain


@pytest.fixture
def ses_stub(monkeypatch: pytest.MonkeyPatch) -> Iterator[Stubber]:
    client, stub = stubbed()
    monkeypatch.setattr(ses, "client", lambda _settings: client)
    with stub:
        yield stub


def test_account_warns_about_sandbox_rate_and_notifications(
    client: TestClient, auth: Headers, ses_stub: Stubber
) -> None:
    assert client.get("/api/v1/sending/account", headers=auth).json()["provider"] is None
    client.patch(
        "/api/v1/settings",
        headers=auth,
        json={
            "ses": {"region": "ap-southeast-2", "access_key_id": "A", "secret_access_key": "B"},
            "sending": {"rate_per_second": 20},
        },
    )
    ses_stub.add_response(
        "get_account",
        {
            "ProductionAccessEnabled": False,
            "SendingEnabled": True,
            "SendQuota": {"Max24HourSend": 200, "MaxSendRate": 1, "SentLast24Hours": 0},
        },
    )
    body = client.get("/api/v1/sending/account", headers=auth).json()
    assert body["provider"] == "ses" and body["ses"]["production_access"] is False
    warnings = " ".join(body["warnings"])
    assert "sandbox" in warnings and "above your SES limit" in warnings and "notifications" in warnings


def test_domain_setup_lists_records_and_reports_progress(
    client: TestClient, auth: Headers, ses_stub: Stubber, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.patch("/api/v1/settings", headers=auth, json={"ses": SES_CONFIG.model_dump()})
    tokens = ["tok1", "tok2", "tok3"]
    ses_stub.add_client_error("get_email_identity", "NotFoundException", "missing")
    ses_stub.add_response(
        "create_email_identity",
        {"VerifiedForSendingStatus": False, "DkimAttributes": {"Status": "PENDING", "Tokens": tokens}},
    )
    dns: dict[str, Any] = {}
    monkeypatch.setattr(domain, "cname_target", lambda host: dns.get(host))
    monkeypatch.setattr(domain, "txt_records", lambda host: dns.get(host, []))

    created = client.post("/api/v1/sending/domain", json={"domain": " Acme.Example. "}, headers=auth).json()
    assert created["domain"] == "acme.example" and created["ready"] is False
    records = {r["host"]: r for r in created["records"]}
    assert records["tok1._domainkey.acme.example"]["value"] == "tok1.dkim.amazonses.com"
    assert records["_dmarc.acme.example"]["status"] == "missing"

    for t in tokens:
        dns[f"{t}._domainkey.acme.example"] = f"{t}.dkim.amazonses.com"
    dns["_dmarc.acme.example"] = ["v=DMARC1; p=none"]
    dns["acme.example"] = ["v=spf1 include:_spf.google.com ~all", "v=spf1 include:other ~all"]
    ses_stub.add_response(
        "get_email_identity",
        {"VerifiedForSendingStatus": True, "DkimAttributes": {"Status": "SUCCESS", "Tokens": tokens}},
    )
    checked = client.get("/api/v1/sending/domain/acme.example", headers=auth).json()
    assert checked["ready"] is True
    spf = next(r for r in checked["records"] if r["purpose"].startswith("SPF"))
    assert spf["status"] == "warning"

    assert client.get("/api/v1/sending/domain/not_a_domain", headers=auth).status_code == 422
