"""Postmark, Mailgun, SMTP2GO and SMTP relays (AuthSMTP): sending, webhooks, the bounce
mailbox, and the settings and setup screens that go with them."""

from __future__ import annotations

import base64
import email
import email.policy
import hashlib
import hmac
import smtplib
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mailstudio import bounces, esp, mailer
from mailstudio.config import Settings
from mailstudio.db import connect
from mailstudio.notifications import webhook_token
from mailstudio.providers import Outcome, ProviderUnavailable, SmtpProvider, configured_provider
from mailstudio.repositories import settings as settings_repo
from mailstudio.repositories.settings import (
    MailgunSettings,
    PostmarkSettings,
    RelaySettings,
    Smtp2goSettings,
)
from mailstudio.ses import SesProvider

Headers = dict[str, str]


def message(to: str = "ann@example.com") -> EmailMessage:
    return mailer.build(
        mailer.Message(
            to=to,
            from_name="Bakery",
            from_email="news@bakery.example",
            reply_to="hello@bakery.example",
            subject="Fresh bread",
            html="<p>Hello</p>",
            text="Hello",
            unsubscribe_url="https://mail.bakery.example/u/abc",
        )
    )


class FakeHttp:
    """Stands in for `esp.request`: records each call and answers from a script."""

    def __init__(self, *answers: esp.Response | Exception) -> None:
        self.answers = list(answers)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, method: str, url: str, **kwargs: Any) -> esp.Response:
        self.calls.append({"method": method, "url": url, **kwargs})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


POSTMARK = PostmarkSettings(server_token="pm-token")
MAILGUN = MailgunSettings(api_key="key-1", domain="mg.bakery.example", region="eu")
SMTP2GO = Smtp2goSettings(api_key="api-1")


# ---------------------------------------------------------------------------
# Postmark


def test_postmark_sends_through_the_broadcast_stream() -> None:
    http = FakeHttp(esp.Response(200, {"ErrorCode": 0, "Message": "OK", "MessageID": "pm-1"}))
    result = esp.PostmarkProvider(POSTMARK, http).send(message())
    assert result.outcome is Outcome.SENT and result.message_id == "pm-1"
    call = http.calls[0]
    assert call["url"] == "https://api.postmarkapp.com/email"
    assert call["headers"] == {"X-Postmark-Server-Token": "pm-token"}
    body = call["json_body"]
    assert body["MessageStream"] == "broadcast" and body["To"] == "ann@example.com"
    assert body["TextBody"].strip() == "Hello" and "<p>Hello</p>" in body["HtmlBody"]
    assert body["ReplyTo"] == "hello@bakery.example"
    # Postmark adds its own unsubscribe headers until it lets the owner manage them.
    assert body["Headers"] == []


def test_postmark_carries_our_unsubscribe_headers_once_allowed() -> None:
    settings = POSTMARK.model_copy(update={"own_unsubscribes": True})
    http = FakeHttp(esp.Response(200, {"ErrorCode": 0, "MessageID": "pm-2"}))
    esp.PostmarkProvider(settings, http).send(message())
    names = {h["Name"]: h["Value"] for h in http.calls[0]["json_body"]["Headers"]}
    assert names == {
        "List-Unsubscribe": "<https://mail.bakery.example/u/abc>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }


@pytest.mark.parametrize(
    ("answer", "outcome"),
    [
        (esp.Response(422, {"ErrorCode": 406, "Message": "Inactive recipient"}), Outcome.FAILED),
        (esp.Response(422, {"ErrorCode": 300, "Message": "Invalid 'To' address"}), Outcome.FAILED),
        (esp.Response(429, {"ErrorCode": 429, "Message": "Rate limit"}), Outcome.RETRY),
        (esp.Response(500, "Internal error"), Outcome.RETRY),
        (esp.Unreachable("Name or service not known"), Outcome.RETRY),
        (esp.NoAnswer("timed out"), Outcome.FAILED),
    ],
)
def test_postmark_outcomes(answer: esp.Response | Exception, outcome: Outcome) -> None:
    result = esp.PostmarkProvider(POSTMARK, FakeHttp(answer)).send(message())
    assert result.outcome is outcome
    if isinstance(answer, esp.NoAnswer):
        assert "delivery unknown" in (result.error or "")


@pytest.mark.parametrize(
    "answer",
    [
        esp.Response(401, {"ErrorCode": 10, "Message": "Bad or missing server token"}),
        esp.Response(422, {"ErrorCode": 400, "Message": "Sender signature not defined"}),
        esp.Response(422, {"ErrorCode": 412, "Message": "Account pending approval"}),
    ],
)
def test_postmark_problems_that_stop_every_send_pause_the_campaign(answer: esp.Response) -> None:
    with pytest.raises(ProviderUnavailable, match="Postmark"):
        esp.PostmarkProvider(POSTMARK, FakeHttp(answer)).send(message())


def test_postmark_check_wants_a_broadcasts_stream() -> None:
    server = esp.Response(200, {"Name": "Bakery"})
    ok = FakeHttp(server, esp.Response(200, {"ID": "broadcast", "MessageStreamType": "Broadcasts"}))
    assert "Bakery" in esp.check_postmark(POSTMARK, ok)
    wrong = FakeHttp(server, esp.Response(200, {"ID": "broadcast", "MessageStreamType": "Transactional"}))
    with pytest.raises(esp.CheckFailed, match="Broadcasts"):
        esp.check_postmark(POSTMARK, wrong)
    with pytest.raises(esp.CheckFailed, match="server token"):
        esp.check_postmark(POSTMARK, FakeHttp(esp.Response(401, {"Message": "Bad token"})))


# ---------------------------------------------------------------------------
# Mailgun


def test_mailgun_sends_the_finished_message_to_the_right_region() -> None:
    http = FakeHttp(esp.Response(200, {"id": "<2026.abc@mg.bakery.example>", "message": "Queued"}))
    result = esp.MailgunProvider(MAILGUN, http).send(message())
    assert result.outcome is Outcome.SENT and result.message_id == "2026.abc@mg.bakery.example"
    call = http.calls[0]
    assert call["url"] == "https://api.eu.mailgun.net/v3/mg.bakery.example/messages.mime"
    assert call["basic_auth"] == ("api", "key-1") and call["form"] == {"to": "ann@example.com"}
    sent = email.message_from_bytes(call["files"]["message"][1], policy=email.policy.default)
    assert sent["List-Unsubscribe"] == "<https://mail.bakery.example/u/abc>"


def test_mailgun_outcomes() -> None:
    def send(answer: esp.Response) -> Outcome:
        return esp.MailgunProvider(MAILGUN, FakeHttp(answer)).send(message()).outcome

    assert send(esp.Response(400, {"message": "'to' parameter is not a valid address"})) is Outcome.FAILED
    assert send(esp.Response(429, {"message": "Too many requests"})) is Outcome.RETRY
    with pytest.raises(ProviderUnavailable, match="credentials"):
        send(esp.Response(401, "Forbidden"))
    with pytest.raises(ProviderUnavailable, match=r"mg\.bakery\.example"):
        send(esp.Response(404, {"message": "Domain not found"}))


def test_mailgun_check_reports_an_unverified_domain() -> None:
    http = FakeHttp(esp.Response(200, {"domain": {"name": "mg.bakery.example", "state": "unverified"}}))
    with pytest.raises(esp.CheckFailed, match="unverified"):
        esp.check_mailgun(MAILGUN, http)
    active = FakeHttp(esp.Response(200, {"domain": {"state": "active"}}))
    assert "verified" in esp.check_mailgun(MAILGUN, active)


# ---------------------------------------------------------------------------
# SMTP2GO


def test_smtp2go_sends_base64_mime() -> None:
    http = FakeHttp(esp.Response(200, {"data": {"succeeded": 1, "failed": 0, "email_id": "1er8bV"}}))
    result = esp.Smtp2goProvider(SMTP2GO, http).send(message())
    assert result.outcome is Outcome.SENT and result.message_id == "1er8bV"
    call = http.calls[0]
    assert call["url"] == "https://api.smtp2go.com/v3/email/mime"
    assert call["headers"] == {"X-Smtp2go-Api-Key": "api-1"}
    sent = email.message_from_bytes(base64.b64decode(call["json_body"]["mime_email"]))
    assert sent["To"] == "ann@example.com"


def test_smtp2go_outcomes() -> None:
    def send(answer: esp.Response) -> Outcome:
        return esp.Smtp2goProvider(SMTP2GO, FakeHttp(answer)).send(message()).outcome

    refused = {"data": {"succeeded": 0, "failed": 1, "failures": ["ann@example.com: invalid"]}}
    assert send(esp.Response(200, refused)) is Outcome.FAILED
    assert send(esp.Response(503, {"data": {"error": "Service unavailable"}})) is Outcome.RETRY
    with pytest.raises(ProviderUnavailable, match="SMTP2GO"):
        send(esp.Response(400, {"data": {"error": "Unauthorised sender: news@bakery.example"}}))


# ---------------------------------------------------------------------------
# SMTP relays (AuthSMTP)


class FakeSmtp:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.sent: list[tuple[EmailMessage, str | None]] = []

    def send_message(self, msg: EmailMessage, from_addr: str | None = None) -> None:
        if self.error:
            raise self.error
        self.sent.append((msg, from_addr))

    def quit(self) -> None:
        pass


RELAY = RelaySettings(
    host="mail.authsmtp.com",
    port=2525,
    username="ac12345",
    password="pw",
    bounce_address="bounces@bakery.example",
)


def test_relay_sends_with_the_bounce_address_as_envelope_sender(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSmtp()
    provider = SmtpProvider(RELAY, envelope_from=str(RELAY.bounce_address))
    monkeypatch.setattr(provider, "_connect", lambda: fake)
    msg = message()
    result = provider.send(msg)
    assert result.outcome is Outcome.SENT
    assert fake.sent == [(msg, "bounces@bakery.example")]
    assert result.message_id == str(msg["Message-ID"]).strip("<>")  # matched again when it bounces


def test_relay_refusing_the_sender_pauses_the_campaign(monkeypatch: pytest.MonkeyPatch) -> None:
    refused = smtplib.SMTPSenderRefused(553, b"Sender address not authorised", "bounces@bakery.example")
    provider = SmtpProvider(RELAY, envelope_from="bounces@bakery.example")
    monkeypatch.setattr(provider, "_connect", lambda: FakeSmtp(refused))
    with pytest.raises(ProviderUnavailable, match="authorised"):
        provider.send(message())


# ---------------------------------------------------------------------------
# Choosing a provider


def test_the_chosen_provider_sends(settings: Settings, client: TestClient, auth: Headers) -> None:
    conn = connect(settings.database_path)
    with pytest.raises(ProviderUnavailable, match="No sending service"):
        configured_provider(conn)
    client.patch(
        "/api/v1/settings",
        headers=auth,
        json={
            "ses": {"region": "ap-southeast-2", "access_key_id": "AKIA", "secret_access_key": "s"},
            "postmark": {"server_token": "pm"},
            "mailgun": {"api_key": "k", "domain": "mg.bakery.example"},
            "smtp2go": {"api_key": "a"},
            "relay": {"host": "mail.authsmtp.com", "bounce_address": "b@bakery.example"},
        },
    )
    # Installs from before the choice existed keep sending through SES.
    assert isinstance(configured_provider(conn), SesProvider)
    expected = {
        "postmark": esp.PostmarkProvider,
        "mailgun": esp.MailgunProvider,
        "smtp2go": esp.Smtp2goProvider,
        "authsmtp": SmtpProvider,
        "smtp": SmtpProvider,
        "ses": SesProvider,
    }
    for name, kind in expected.items():
        client.patch("/api/v1/settings", headers=auth, json={"provider": {"active": name}})
        provider = configured_provider(conn)
        assert isinstance(provider, kind), name
    assert configured_provider_envelope(conn) == "b@bakery.example"
    conn.close()


def configured_provider_envelope(conn: Any) -> str | None:
    settings_repo.put_group(conn, "provider", settings_repo.ProviderChoice(active="authsmtp"))
    provider = configured_provider(conn)
    assert isinstance(provider, SmtpProvider)
    return provider.envelope_from


def test_secrets_are_never_sent_back_and_survive_updates(client: TestClient, auth: Headers) -> None:
    client.patch(
        "/api/v1/settings",
        headers=auth,
        json={
            "provider": {"active": "mailgun"},
            "mailgun": {
                "api_key": "key-secret",
                "domain": "mg.bakery.example",
                "webhook_signing_key": "sign",
            },
            "relay": {"host": "mail.authsmtp.com", "password": "pw", "imap_password": "ipw"},
        },
    )
    shown = client.get("/api/v1/settings", headers=auth).json()
    assert shown["provider"] == {"active": "mailgun"}
    assert shown["mailgun"] == {
        "domain": "mg.bakery.example",
        "region": "us",
        "api_key_set": True,
        "webhook_signing_key_set": True,
    }
    assert "password" not in shown["relay"] and shown["relay"]["imap_password_set"]
    assert "key-secret" not in str(shown)

    # Updating without the secrets keeps them; "" clears one.
    client.patch(
        "/api/v1/settings",
        headers=auth,
        json={"mailgun": {"domain": "mg2.bakery.example", "region": "eu", "webhook_signing_key": ""}},
    )
    shown = client.get("/api/v1/settings", headers=auth).json()["mailgun"]
    assert shown["api_key_set"] and not shown["webhook_signing_key_set"] and shown["region"] == "eu"


# ---------------------------------------------------------------------------
# Webhooks


def hook(settings: Settings, provider: str) -> str:
    conn = connect(settings.database_path)
    token = webhook_token(settings_repo.secret_key(conn))
    conn.close()
    return f"/webhooks/{provider}/{token}"


def suppression(client: TestClient, auth: Headers, address: str) -> dict[str, Any] | None:
    response = client.get(f"/api/v1/suppressions/{address}", headers=auth)
    return response.json() if response.status_code == 200 else None


def test_webhooks_need_this_installs_secret_address(client: TestClient) -> None:
    for provider in ("postmark", "mailgun", "smtp2go", "elsewhere"):
        response = client.post(f"/webhooks/{provider}/wrong-token", json={"RecordType": "Bounce"})
        assert response.status_code == 404


def test_postmark_webhooks(settings: Settings, client: TestClient, auth: Headers) -> None:
    url = hook(settings, "postmark")
    hard = {
        "RecordType": "Bounce",
        "Type": "HardBounce",
        "Name": "Hard bounce",
        "Email": "Gone@Example.com",
        "Description": "The server was unable to deliver your message",
        "MessageID": "pm-1",
    }
    soft = {**hard, "Type": "SoftBounce", "Email": "full@example.com", "MessageID": "pm-2"}
    spam = {"RecordType": "SpamComplaint", "Type": "SpamComplaint", "Email": "angry@example.com"}
    unsubscribed = {
        "RecordType": "SubscriptionChange",
        "Recipient": "bye@example.com",
        "SuppressSending": True,
        "SuppressionReason": "ManualSuppression",
        "Origin": "Recipient",
        "MessageID": None,
    }
    for event in (hard, soft, spam, unsubscribed, {"RecordType": "Open"}):
        assert client.post(url, json=event).status_code == 200

    assert suppression(client, auth, "gone@example.com")["reason"] == "bounce"  # type: ignore[index]
    assert suppression(client, auth, "full@example.com") is None
    assert suppression(client, auth, "angry@example.com")["reason"] == "complaint"  # type: ignore[index]
    assert suppression(client, auth, "bye@example.com")["reason"] == "manual"  # type: ignore[index]


def mailgun_payload(event: dict[str, Any], key: str = "signing-key") -> dict[str, Any]:
    timestamp, token = "1763000000", "a" * 50
    signature = hmac.new(key.encode(), f"{timestamp}{token}".encode(), hashlib.sha256).hexdigest()
    return {
        "signature": {"timestamp": timestamp, "token": token, "signature": signature},
        "event-data": event,
    }


def test_mailgun_webhooks_are_signed(settings: Settings, client: TestClient, auth: Headers) -> None:
    client.patch(
        "/api/v1/settings",
        headers=auth,
        json={
            "mailgun": {"api_key": "k", "domain": "mg.bakery.example", "webhook_signing_key": "signing-key"}
        },
    )
    url = hook(settings, "mailgun")
    failed = {
        "event": "failed",
        "severity": "permanent",
        "recipient": "gone@example.com",
        "delivery-status": {"description": "No such user"},
        "message": {"headers": {"message-id": "2026.abc@mg.bakery.example"}},
    }

    forged = mailgun_payload(failed, key="someone-elses-key")
    assert client.post(url, json=forged).status_code == 403
    assert suppression(client, auth, "gone@example.com") is None

    assert client.post(url, json=mailgun_payload(failed)).status_code == 200
    assert suppression(client, auth, "gone@example.com")["reason"] == "bounce"  # type: ignore[index]

    temporary = {**failed, "severity": "temporary", "recipient": "busy@example.com"}
    client.post(url, json=mailgun_payload(temporary))
    assert suppression(client, auth, "busy@example.com") is None

    complained = {"event": "complained", "recipient": "angry@example.com"}
    client.post(url, json=mailgun_payload(complained))
    assert suppression(client, auth, "angry@example.com")["reason"] == "complaint"  # type: ignore[index]


def test_smtp2go_webhooks_take_form_posts(settings: Settings, client: TestClient, auth: Headers) -> None:
    url = hook(settings, "smtp2go")
    client.post(
        url, data={"event": "bounce", "bounce": "hard", "rcpt": "gone@example.com", "email_id": "1er"}
    )
    client.post(
        url, data={"event": "bounce", "bounce": "soft", "rcpt": "full@example.com", "email_id": "1es"}
    )
    client.post(url, json={"event": "spam", "rcpt": "angry@example.com", "email_id": "1et"})
    assert suppression(client, auth, "gone@example.com")["reason"] == "bounce"  # type: ignore[index]
    assert suppression(client, auth, "full@example.com") is None
    assert suppression(client, auth, "angry@example.com")["reason"] == "complaint"  # type: ignore[index]


def test_account_shows_the_webhook_until_events_arrive(
    settings: Settings, client: TestClient, auth: Headers
) -> None:
    client.patch(
        "/api/v1/settings",
        headers=auth,
        json={"provider": {"active": "postmark"}, "postmark": {"server_token": "pm"}},
    )
    account = client.get("/api/v1/sending/account", headers=auth).json()
    assert account["provider"] == "postmark" and account["label"] == "Postmark"
    assert account["notifications"] == "pending"
    assert account["webhook_url"].endswith(hook(settings, "postmark"))
    assert any("webhook" in w for w in account["warnings"])

    client.post(hook(settings, "postmark"), json={"RecordType": "Delivery", "Recipient": "a@example.com"})
    account = client.get("/api/v1/sending/account", headers=auth).json()
    assert account["notifications"] == "confirmed"
    assert not any("webhook" in w for w in account["warnings"])


# ---------------------------------------------------------------------------
# Bounce mailbox

DSN = b"""From: Mail Delivery Subsystem <MAILER-DAEMON@punt12.authsmtp.com>
To: bounces@bakery.example
Subject: Returned mail: see transcript for details
MIME-Version: 1.0
Content-Type: multipart/report; report-type=delivery-status; boundary="B"

--B
Content-Type: text/plain

The original message was received but could not be delivered.

--B
Content-Type: message/delivery-status

Reporting-MTA: dns; punt12.authsmtp.com

Final-Recipient: RFC822; Gone@Example.com
Action: failed
Status: 5.1.1
Diagnostic-Code: SMTP; 550 5.1.1 User unknown

Final-Recipient: rfc822; full@example.com
Action: delayed
Status: 4.2.2
Diagnostic-Code: SMTP; 452 4.2.2 Mailbox full

--B
Content-Type: text/rfc822-headers

From: Bakery <news@bakery.example>
To: gone@example.com
Subject: Fresh bread
Message-ID: <abc.123@bakery.example>

--B--
"""

ARF = b"""From: feedback@isp.example
To: bounces@bakery.example
Subject: Abuse report
MIME-Version: 1.0
Content-Type: multipart/report; report-type=feedback-report; boundary="F"

--F
Content-Type: text/plain

This is an email abuse report.

--F
Content-Type: message/feedback-report

Feedback-Type: abuse
User-Agent: ISP-FBL/1.0
Version: 1
Original-Rcpt-To: angry@example.com

--F
Content-Type: message/rfc822

From: Bakery <news@bakery.example>
To: angry@example.com
Message-ID: <def.456@bakery.example>
Subject: Fresh bread

Hello
--F--
"""

PLAIN = b"""From: Mail Delivery System <MAILER-DAEMON@mx.example>
To: bounces@bakery.example
Subject: Undelivered Mail Returned to Sender
Content-Type: text/plain

This is the mail system. Your message could not be delivered.

<nobody@example.org>: host mx.example.org said: 550 5.1.1 <nobody@example.org>
    Recipient address rejected: User unknown
"""

REPLY = b"""From: Ann <ann@example.com>
To: bounces@bakery.example
Subject: Re: Fresh bread
Content-Type: text/plain

Lovely, thanks! 550 grams of sourdough please.
"""


def test_delivery_status_reports() -> None:
    findings = bounces.parse(DSN)
    assert findings == [
        bounces.Finding(
            "bounce", "gone@example.com", "abc.123@bakery.example", "SMTP; 550 5.1.1 User unknown"
        ),
        bounces.Finding(
            "soft_bounce", "full@example.com", "abc.123@bakery.example", "SMTP; 452 4.2.2 Mailbox full"
        ),
    ]


def test_spam_reports_and_plain_bounces() -> None:
    assert bounces.parse(ARF) == [
        bounces.Finding("complaint", "angry@example.com", "def.456@bakery.example", "Spam report (abuse)")
    ]
    plain = bounces.parse(PLAIN)
    assert plain is not None and [(f.kind, f.email) for f in plain] == [("bounce", "nobody@example.org")]
    assert bounces.parse(REPLY) is None  # a person's reply is left for a person


class FakeImap:
    def __init__(self, messages: list[bytes]) -> None:
        self.messages = messages
        self.seen: list[bytes] = []

    def select(self, mailbox: str) -> tuple[str, list[bytes]]:
        return "OK", [str(len(self.messages)).encode()]

    def search(self, charset: Any, criterion: str) -> tuple[str, list[bytes]]:
        return "OK", [b" ".join(str(i + 1).encode() for i in range(len(self.messages)))]

    def fetch(self, num: bytes, parts: str) -> tuple[str, list[Any]]:
        assert "PEEK" in parts  # reading must not mark it read
        return "OK", [(b"1 (BODY[] {n}", self.messages[int(num) - 1]), b")"]

    def store(self, num: bytes, command: str, flags: str) -> tuple[str, list[bytes]]:
        self.seen.append(num)
        return "OK", []

    def logout(self) -> None:
        pass


def test_the_worker_reads_the_bounce_mailbox_every_few_minutes(
    settings: Settings, client: TestClient, auth: Headers, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.patch(
        "/api/v1/settings",
        headers=auth,
        json={
            "provider": {"active": "authsmtp"},
            "relay": {
                "host": "mail.authsmtp.com",
                "bounce_address": "bounces@bakery.example",
                "imap_host": "imap.bakery.example",
                "imap_username": "bounces@bakery.example",
                "imap_password": "pw",
            },
        },
    )
    mailbox = FakeImap([DSN, REPLY, ARF])
    opened = []
    monkeypatch.setattr(bounces, "connect", lambda config: opened.append(config) or mailbox)

    conn = connect(settings.database_path)
    start = datetime(2026, 11, 15, 2, 0, tzinfo=UTC)
    bounces.poll_if_due(conn, start)
    assert mailbox.seen == [b"1", b"3"]  # the reply stays unread
    assert suppression(client, auth, "gone@example.com")["reason"] == "bounce"  # type: ignore[index]
    assert suppression(client, auth, "angry@example.com")["reason"] == "complaint"  # type: ignore[index]
    assert suppression(client, auth, "full@example.com") is None

    bounces.poll_if_due(conn, start + timedelta(minutes=2))  # not due yet
    bounces.poll_if_due(conn, start + timedelta(minutes=6))
    assert len(opened) == 2

    account = client.get("/api/v1/sending/account", headers=auth).json()
    assert account["notifications"] == "confirmed" and account["mailbox"]["last_count"] == 2

    def broken(config: RelaySettings) -> FakeImap:
        raise OSError("Connection refused")

    monkeypatch.setattr(bounces, "connect", broken)
    bounces.poll_if_due(conn, start + timedelta(minutes=12))  # errors are recorded, not raised
    conn.close()
    account = client.get("/api/v1/sending/account", headers=auth).json()
    assert account["notifications"] == "unknown"
    assert any("Connection refused" in w for w in account["warnings"])


# ---------------------------------------------------------------------------
# Connection check, setup checklist, test sends


def test_connection_check_and_setup_steps(
    settings: Settings, client: TestClient, auth: Headers, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.patch(
        "/api/v1/settings",
        headers=auth,
        json={"provider": {"active": "postmark"}, "postmark": {"server_token": "pm"}},
    )

    def steps() -> dict[str, bool]:
        return {s["key"]: s["done"] for s in client.get("/api/v1/setup", headers=auth).json()["steps"]}

    assert "production" not in steps()  # leaving the SES sandbox doesn't apply
    assert not steps()["provider"]

    def refuse(config: PostmarkSettings) -> str:
        raise esp.CheckFailed("Postmark didn't accept the server token (Bad token).")

    monkeypatch.setattr(esp, "check_postmark", refuse)
    checked = client.post("/api/v1/sending/check", headers=auth).json()
    assert checked == {
        "provider": "postmark",
        "ok": False,
        "message": "Postmark didn't accept the server token (Bad token).",
    }
    assert not steps()["provider"]

    monkeypatch.setattr(esp, "check_postmark", lambda config: "Connected to Bakery.")
    assert client.post("/api/v1/sending/check", headers=auth).json()["ok"]
    assert steps()["provider"] and not steps()["notifications"]

    client.post(hook(settings, "postmark"), json={"RecordType": "Delivery", "Recipient": "a@example.com"})
    assert steps()["notifications"]


def test_test_emails_go_through_the_chosen_service(
    client: TestClient, make_key: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = make_key("read write send")
    client.patch(
        "/api/v1/settings",
        headers=auth,
        json={
            "provider": {"active": "smtp2go"},
            "smtp2go": {"api_key": "a"},
            "sender": {"from_name": "Bakery", "from_email": "news@bakery.example"},
        },
    )
    campaign = client.post(
        "/api/v1/campaigns", json={"name": "Bread", "subject": "Fresh"}, headers=auth
    ).json()
    http = FakeHttp(esp.Response(200, {"data": {"succeeded": 1, "failed": 0, "email_id": "x"}}))
    monkeypatch.setattr(esp, "request", http)
    response = client.post(
        f"/api/v1/campaigns/{campaign['id']}/test", json={"to": ["me@bakery.example"]}, headers=auth
    )
    assert response.status_code == 202, response.text
    assert http.calls and http.calls[0]["url"].endswith("/email/mime")


# ---------------------------------------------------------------------------
# The HTTP client itself, against a real local server


@pytest.fixture
def local_server() -> Any:
    import http.server
    import socket
    import threading
    import time

    received: list[dict[str, Any]] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append({"path": self.path, "headers": dict(self.headers), "body": body})
            if self.path == "/slow":
                time.sleep(1.5)
            status = 422 if self.path == "/bad" else 200
            reply = b'{"ErrorCode": 300, "Message": "Invalid"}' if status == 422 else b'{"ok": true}'
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *args: Any) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with socket.socket() as probe:  # a port with nothing listening
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
    yield f"http://127.0.0.1:{server.server_address[1]}", received, closed_port
    server.shutdown()


def test_http_requests_and_failures(local_server: Any) -> None:
    base, received, closed_port = local_server
    ok = esp.request("POST", f"{base}/json", json_body={"a": 1}, headers={"X-Key": "k"})
    assert ok == esp.Response(200, {"ok": True})
    assert received[0]["body"] == b'{"a": 1}' and received[0]["headers"]["X-Key"] == "k"

    bad = esp.request("POST", f"{base}/bad", json_body={})
    assert bad.status == 422 and bad.body["ErrorCode"] == 300

    esp.request(
        "POST",
        f"{base}/form",
        form={"to": "a@example.com"},
        files={"message": ("m.eml", b"Subject: hi\r\n\r\nbody", "message/rfc822")},
        basic_auth=("api", "k"),
    )
    sent = received[-1]
    assert sent["headers"]["Content-Type"].startswith("multipart/form-data; boundary=")
    assert b'name="to"\r\n\r\na@example.com' in sent["body"] and b"Subject: hi" in sent["body"]
    assert sent["headers"]["Authorization"] == "Basic " + base64.b64encode(b"api:k").decode()

    with pytest.raises(esp.Unreachable):  # never sent: safe to retry
        esp.request("POST", f"http://127.0.0.1:{closed_port}/x", json_body={})
    with pytest.raises(esp.NoAnswer):  # sent, then silence: may have been accepted
        esp.request("POST", f"{base}/slow", json_body={}, timeout=0.5)
