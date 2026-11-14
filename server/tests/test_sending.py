from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mailstudio import tokens
from mailstudio.config import Settings
from mailstudio.db import connect, now, transaction
from mailstudio.ids import new_id
from mailstudio.providers import Outcome, ProviderUnavailable, Result
from mailstudio.repositories import settings as settings_repo
from mailstudio.worker import INTERRUPTED, LEASE, Worker

from .conftest import CONSENT

Headers = dict[str, str]


class FakeProvider:
    """Records messages; `script` gives outcomes for the first sends, then SENT."""

    def __init__(self, script: list[Outcome] | None = None, crash_on: int | None = None) -> None:
        self.messages: list[EmailMessage] = []
        self.script = list(script or [])
        self.crash_on = crash_on
        self.calls = 0

    def send(self, message: EmailMessage) -> Result:
        self.calls += 1
        outcome = self.script.pop(0) if self.script else Outcome.SENT
        if outcome is Outcome.SENT:
            self.messages.append(message)
        if self.crash_on is not None and self.calls == self.crash_on:
            raise SystemExit("simulated crash after the provider accepted the message")
        return Result(
            outcome, message_id=f"<m{self.calls}@test>", error=None if outcome is Outcome.SENT else "boom"
        )

    def close(self) -> None:
        pass

    @property
    def recipients(self) -> list[str]:
        return [str(m["To"]) for m in self.messages]


class Clock:
    def __init__(self) -> None:
        # A moment ahead of real time, so campaigns the API schedules "now" are already due.
        self.current = datetime.now(UTC) + timedelta(seconds=1)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


@pytest.fixture
def send_key(make_key: Callable[[str], Headers]) -> Headers:
    return make_key("read write send")


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def make_worker(settings: Settings, clock: Clock) -> Iterator[Callable[[Any], Worker]]:
    workers: list[Worker] = []

    def make(provider: Any) -> Worker:
        worker = Worker(settings, lambda _: provider, clock=clock, sleep=lambda _: None)
        workers.append(worker)
        return worker

    yield make
    for worker in workers:
        worker.conn.close()


def ready(client: TestClient, key: Headers) -> None:
    client.patch(
        "/api/v1/settings",
        headers=key,
        json={
            "business": {"name": "Acme Bakery", "address": "1 Hay St, Perth WA 6000"},
            "sender": {"from_name": "Acme", "from_email": "news@acme.example"},
        },
    )


def new_list(client: TestClient, key: Headers, name: str = "News") -> str:
    return str(client.post("/api/v1/lists", json={"name": name}, headers=key).json()["id"])


def new_contact(client: TestClient, key: Headers, email: str, lists: list[str], **extra: Any) -> str:
    body = {"email": email, "lists": lists, "consent": CONSENT, **extra}
    return str(client.post("/api/v1/contacts", json=body, headers=key).json()["id"])


def new_campaign(client: TestClient, key: Headers, lists: list[str], **extra: Any) -> str:
    body = {"name": "Nov", "subject": "Hi {{first_name|there}}", "lists": lists, **extra}
    response = client.post("/api/v1/campaigns", json=body, headers=key)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def schedule(client: TestClient, key: Headers, campaign: str, **body: Any) -> Any:
    return client.post(f"/api/v1/campaigns/{campaign}/schedule", json=body or None, headers=key)


def run_until_idle(worker: Worker, limit: int = 1000) -> None:
    for _ in range(limit):
        if not worker.tick().processed:
            return
    raise AssertionError("worker never went idle")


def status(client: TestClient, key: Headers, campaign: str) -> str:
    return str(client.get(f"/api/v1/campaigns/{campaign}", headers=key).json()["status"])


# ---------------------------------------------------------------------------
# Scheduling


def test_schedule_lists_what_is_missing(client: TestClient, send_key: Headers) -> None:
    campaign = new_campaign(client, send_key, [], subject="")
    response = schedule(client, send_key, campaign)
    assert response.status_code == 422
    problems = " ".join(response.json()["problems"])
    for needed in ("postal address", "subject", "sender", "list"):
        assert needed in problems


def test_scheduled_campaigns_are_locked_until_cancelled(client: TestClient, send_key: Headers) -> None:
    ready(client, send_key)
    news = new_list(client, send_key)
    new_contact(client, send_key, "a@example.com", [news])
    campaign = new_campaign(client, send_key, [news])
    at = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    assert schedule(client, send_key, campaign, at=at).json()["status"] == "scheduled"
    url = f"/api/v1/campaigns/{campaign}"
    assert client.patch(url, json={"subject": "x"}, headers=send_key).status_code == 409
    assert client.post(f"{url}/cancel", headers=send_key).json()["status"] == "draft"
    assert client.patch(url, json={"subject": "x"}, headers=send_key).status_code == 200


def test_sending_needs_the_send_scope(client: TestClient, auth: Headers) -> None:
    campaign = new_campaign(client, auth, [])
    assert schedule(client, auth, campaign).status_code == 403


def test_naive_schedule_times_are_rejected(client: TestClient, send_key: Headers) -> None:
    campaign = new_campaign(client, send_key, [])
    assert schedule(client, send_key, campaign, at="2030-01-01T09:00:00").status_code == 422


# ---------------------------------------------------------------------------
# Sending


def test_send_reaches_each_subscriber_once_with_their_own_unsubscribe_link(
    settings: Settings, client: TestClient, send_key: Headers, make_worker: Callable[[Any], Worker]
) -> None:
    ready(client, send_key)
    news, offers = new_list(client, send_key, "News"), new_list(client, send_key, "Offers")
    ada = new_contact(client, send_key, "ada@example.com", [news, offers], first_name="Ada")
    new_contact(client, send_key, "bob@example.com", [news])
    new_contact(client, send_key, "cy@example.com", [offers])
    campaign = new_campaign(client, send_key, [news, offers])
    assert schedule(client, send_key, campaign).status_code == 200

    provider = FakeProvider()
    run_until_idle(make_worker(provider))

    assert sorted(provider.recipients) == ["ada@example.com", "bob@example.com", "cy@example.com"]
    assert status(client, send_key, campaign) == "sent"
    stats = client.get(f"/api/v1/campaigns/{campaign}/stats", headers=send_key).json()
    assert (stats["total"], stats["sent"], stats["queued"]) == (3, 3, 0)

    message = next(m for m in provider.messages if m["To"] == "ada@example.com")
    assert message["Subject"] == "Hi Ada"
    assert message["X-Mailstudio-Campaign"] == campaign
    url = str(message["List-Unsubscribe"]).strip("<>")
    token = url.rsplit("/u/", 1)[1]
    conn = connect(settings.database_path)
    assert tokens.read(settings_repo.secret_key(conn), token) == (ada, campaign)
    conn.close()
    assert url in message.get_body(("html",)).get_content()  # type: ignore[union-attr]


def test_future_campaigns_wait_for_their_time(
    client: TestClient, send_key: Headers, make_worker: Callable[[Any], Worker], clock: Clock
) -> None:
    ready(client, send_key)
    news = new_list(client, send_key)
    new_contact(client, send_key, "a@example.com", [news])
    campaign = new_campaign(client, send_key, [news])
    schedule(client, send_key, campaign, at=(clock() + timedelta(hours=2)).isoformat())

    provider = FakeProvider()
    worker = make_worker(provider)
    assert worker.tick().started == 0
    clock.advance(timedelta(hours=2, seconds=1))
    run_until_idle(worker)
    assert provider.recipients == ["a@example.com"]


def test_people_who_leave_after_queueing_are_skipped(
    settings: Settings, client: TestClient, send_key: Headers, make_worker: Callable[[Any], Worker]
) -> None:
    ready(client, send_key)
    news = new_list(client, send_key)
    leaver = new_contact(client, send_key, "leaver@example.com", [news])
    new_contact(client, send_key, "blocked@example.com", [news])
    new_contact(client, send_key, "stays@example.com", [news])
    campaign = new_campaign(client, send_key, [news])
    schedule(client, send_key, campaign)

    provider = FakeProvider()
    worker = make_worker(provider)
    worker.start_due_campaigns()  # queued, nothing sent yet
    client.post(f"/api/v1/lists/{news}/members/{leaver}/unsubscribe", headers=send_key)
    client.post("/api/v1/suppressions", json={"email": "blocked@example.com"}, headers=send_key)
    run_until_idle(worker)

    assert provider.recipients == ["stays@example.com"]
    stats = client.get(f"/api/v1/campaigns/{campaign}/stats", headers=send_key).json()
    assert (stats["sent"], stats["skipped"]) == (1, 2)


def test_temporary_failures_retry_with_backoff_then_give_up(
    settings: Settings,
    client: TestClient,
    send_key: Headers,
    make_worker: Callable[[Any], Worker],
    clock: Clock,
) -> None:
    ready(client, send_key)
    news = new_list(client, send_key)
    new_contact(client, send_key, "a@example.com", [news])
    new_contact(client, send_key, "b@example.com", [news])
    campaign = new_campaign(client, send_key, [news])
    schedule(client, send_key, campaign)

    # a@: one temporary failure, then accepted. b@: rejected for good.
    provider = FakeProvider(script=[Outcome.RETRY, Outcome.FAILED])
    worker = make_worker(provider)
    first = worker.tick()
    assert (first.retried, first.failed, first.sent) == (1, 1, 0)
    assert worker.tick().processed == 0  # retry isn't due yet
    clock.advance(timedelta(minutes=1, seconds=1))
    run_until_idle(worker)
    assert provider.recipients == ["a@example.com"] or provider.recipients == ["b@example.com"]
    assert status(client, send_key, campaign) == "sent"

    conn = connect(settings.database_path)
    rows = {r["email"]: (r["status"], r["attempts"]) for r in conn.execute("SELECT * FROM send_jobs")}
    conn.close()
    assert sorted(rows.values()) == [("failed", 1), ("sent", 2)]


def test_retries_stop_after_five_attempts(
    client: TestClient, send_key: Headers, make_worker: Callable[[Any], Worker], clock: Clock
) -> None:
    ready(client, send_key)
    news = new_list(client, send_key)
    new_contact(client, send_key, "a@example.com", [news])
    campaign = new_campaign(client, send_key, [news])
    schedule(client, send_key, campaign)
    provider = FakeProvider(script=[Outcome.RETRY] * 10)
    worker = make_worker(provider)
    for _ in range(8):
        worker.tick()
        clock.advance(timedelta(hours=7))
    assert provider.calls == 6  # first try + five retries
    stats = client.get(f"/api/v1/campaigns/{campaign}/stats", headers=send_key).json()
    assert stats["failed"] == 1 and stats["status"] == "sent"


def test_pause_resume_and_cancel(
    client: TestClient, send_key: Headers, make_worker: Callable[[Any], Worker]
) -> None:
    ready(client, send_key)
    news = new_list(client, send_key)
    for i in range(6):
        new_contact(client, send_key, f"p{i}@example.com", [news])
    campaign = new_campaign(client, send_key, [news])
    schedule(client, send_key, campaign)
    provider = FakeProvider()
    worker = make_worker(provider)
    worker.batch_size = 2
    url = f"/api/v1/campaigns/{campaign}"

    worker.tick()
    assert len(provider.messages) == 2
    assert client.post(f"{url}/pause", headers=send_key).json()["status"] == "paused"
    assert worker.tick().processed == 0
    assert client.post(f"{url}/resume", headers=send_key).json()["status"] == "sending"
    worker.tick()
    assert len(provider.messages) == 4
    assert client.post(f"{url}/cancel", headers=send_key).json()["status"] == "cancelled"
    run_until_idle(worker)
    assert len(provider.messages) == 4
    stats = client.get(f"{url}/stats", headers=send_key).json()
    assert (stats["sent"], stats["skipped"]) == (4, 2)


def test_missing_provider_pauses_without_losing_anyone(
    client: TestClient, send_key: Headers, settings: Settings, clock: Clock
) -> None:
    ready(client, send_key)
    news = new_list(client, send_key)
    for i in range(3):
        new_contact(client, send_key, f"p{i}@example.com", [news])
    campaign = new_campaign(client, send_key, [news])
    schedule(client, send_key, campaign)

    def unavailable(_: Any) -> Any:
        raise ProviderUnavailable("No sending provider is connected.")

    worker = Worker(settings, unavailable, clock=clock, sleep=lambda _: None)
    worker.tick()
    worker.conn.close()
    details = client.get(f"/api/v1/campaigns/{campaign}", headers=send_key).json()
    assert details["status"] == "paused" and "No sending provider" in details["last_error"]
    stats = client.get(f"/api/v1/campaigns/{campaign}/stats", headers=send_key).json()
    assert (stats["queued"], stats["failed"]) == (3, 0)


# ---------------------------------------------------------------------------
# The Phase 1 gate


def test_gate_5000_contacts_crash_midway_no_duplicates(
    settings: Settings,
    client: TestClient,
    send_key: Headers,
    make_worker: Callable[[Any], Worker],
    clock: Clock,
) -> None:
    """5,000 subscribers, the process dies halfway through, sending resumes:
    nobody gets the email twice, and only the one in-flight message is lost."""
    total = 5000
    ready(client, send_key)
    news, extra = new_list(client, send_key, "News"), new_list(client, send_key, "Extra")

    conn = connect(settings.database_path)
    stamp = now()
    with transaction(conn):
        for i in range(total):
            contact = new_id("contact")
            conn.execute(
                "INSERT INTO contacts (id, email, first_name, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (contact, f"person{i}@example.com", f"P{i}", stamp, stamp),
            )
            lists = [news, extra] if i % 3 == 0 else [news]  # a third are on both lists
            for list_id in lists:
                conn.execute(
                    "INSERT INTO list_members"
                    " (list_id, contact_id, status, subscribed_at, created_at, updated_at)"
                    " VALUES (?, ?, 'subscribed', ?, ?, ?)",
                    (list_id, contact, stamp, stamp, stamp),
                )
    conn.close()

    campaign = new_campaign(client, send_key, [news, extra])
    assert client.get(f"/api/v1/campaigns/{campaign}", headers=send_key).json()["audience"] == total
    schedule(client, send_key, campaign)

    first = FakeProvider(crash_on=total // 2)
    with pytest.raises(SystemExit):
        run_until_idle(make_worker(first), limit=10_000)

    clock.advance(LEASE + timedelta(seconds=1))  # the crashed worker's claims expire
    second = FakeProvider()
    resumed = make_worker(second)
    assert resumed.tick().recovered == 1
    run_until_idle(resumed, limit=10_000)

    delivered = Counter(first.recipients + second.recipients)
    assert max(delivered.values()) == 1, "someone got the email twice"
    assert len(delivered) == total
    assert status(client, send_key, campaign) == "sent"

    stats = client.get(f"/api/v1/campaigns/{campaign}/stats", headers=send_key).json()
    assert (stats["total"], stats["sent"], stats["failed"], stats["queued"]) == (total, total - 1, 1, 0)
    conn = connect(settings.database_path)
    assert conn.execute("SELECT error FROM send_jobs WHERE status = 'failed'").fetchone()[0] == INTERRUPTED
    snapshot = json.loads(conn.execute("SELECT snapshot FROM campaigns").fetchone()[0])
    conn.close()
    assert "{{first_name|there}}" in snapshot["subject"]  # frozen with merge tags, filled per recipient
