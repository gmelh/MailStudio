"""The sending worker.

Each `tick()`:

1. Starts campaigns whose scheduled time has come: renders them once, freezes the
   result as the campaign's snapshot, and queues one job per recipient.
2. Recovers after a crash. Jobs that were claimed but never handed to the
   provider go back in the queue. The one job that was being handed over is
   marked failed, not retried: the provider may already have accepted it, and
   sending twice is worse than missing one. So a crash costs at most one message.
3. Claims a batch of due jobs, sends them at the configured rate, and records each
   result: sent, retry later (with increasing delays), or failed.
4. Marks campaigns `sent` once nothing is left queued.
5. For SMTP relays, checks the bounce mailbox every few minutes (`bounces.py`).

Every state change is its own short transaction, so the API stays responsive and a
crash at any point leaves the queue consistent.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from . import bounces, mailer, render, tokens
from .config import Settings
from .db import connect, now, transaction
from .document import Document, check_sections
from .errors import ProblemError
from .providers import Outcome, Provider, ProviderUnavailable, Result
from .repositories import campaigns, settings
from .repositories.settings import Business, Sending
from .sections import load

log = logging.getLogger("mailstudio.worker")

RETRY_DELAYS = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=6),
)
LEASE = timedelta(minutes=10)
INTERRUPTED = "Interrupted while sending; delivery unknown, so not retried to avoid a duplicate"


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class TickResult:
    started: int = 0
    sent: int = 0
    retried: int = 0
    failed: int = 0
    skipped: int = 0
    recovered: int = 0
    finished: int = 0

    @property
    def processed(self) -> int:
        return self.sent + self.retried + self.failed + self.skipped


class Worker:
    def __init__(
        self,
        app: Settings,
        provider_factory: Callable[[sqlite3.Connection], Provider],
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = time.sleep,
        batch_size: int = 50,
    ) -> None:
        self.app = app
        self.provider_factory = provider_factory
        self.clock = clock
        self.sleep = sleep
        self.batch_size = batch_size
        self.conn = connect(app.database_path)
        self._provider: Provider | None = None

    def close(self) -> None:
        if self._provider is not None:
            self._provider.close()
        self.conn.close()

    # ------------------------------------------------------------------ tick

    def tick(self) -> TickResult:
        result = TickResult()
        result.started = self.start_due_campaigns()
        result.recovered = self.recover_interrupted()
        self.send_batch(result)
        result.finished = self.finish_campaigns()
        bounces.poll_if_due(self.conn, self.clock())  # SMTP relays only; at most every few minutes
        return result

    def run(self, stop: threading.Event, idle_seconds: float = 2.0) -> None:
        log.info("Worker started")
        while not stop.is_set():
            try:
                result = self.tick()
            except Exception:
                log.exception("Worker tick failed")
                result = TickResult()
            if not result.processed:
                stop.wait(idle_seconds)
        self.close()
        log.info("Worker stopped")

    # ------------------------------------------------------- starting campaigns

    def start_due_campaigns(self) -> int:
        due = [
            r["id"]
            for r in self.conn.execute(
                "SELECT id FROM campaigns WHERE status = 'scheduled' AND scheduled_at <= ?"
                " ORDER BY scheduled_at",
                (iso(self.clock()),),
            )
        ]
        for campaign_id in due:
            self.start(campaign_id)
        return len(due)

    def start(self, campaign_id: str) -> None:
        conn = self.conn
        with transaction(conn):
            campaign = campaigns.get(conn, campaign_id)
            if campaign is None or campaign["status"] != "scheduled":
                return
            try:
                snapshot = build_snapshot(conn, self.app, campaign)
            except ProblemError as exc:
                conn.execute(
                    "UPDATE campaigns SET status = 'paused', last_error = ?, updated_at = ? WHERE id = ?",
                    (f"Couldn't render: {exc}", now(), campaign_id),
                )
                return
            audience, params = campaigns.audience_sql(campaign["lists"], campaign["tags"])
            stamp = iso(self.clock())
            conn.execute(
                "INSERT OR IGNORE INTO send_jobs (campaign_id, contact_id, email, next_attempt_at)"
                f" SELECT ?, a.id, a.email, ? FROM ({audience}) a",
                (campaign_id, stamp, *params),
            )
            conn.execute(
                "UPDATE campaigns SET status = 'sending', snapshot = ?, started_at = ?, last_error = NULL,"
                " updated_at = ? WHERE id = ?",
                (json.dumps(snapshot), stamp, now(), campaign_id),
            )
        log.info("Started campaign %s", campaign_id)

    # ----------------------------------------------------------- crash recovery

    def recover_interrupted(self) -> int:
        """Returns how many in-flight jobs were marked failed."""
        cutoff = iso(self.clock() - LEASE)
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE send_jobs SET status = 'queued', claimed_at = NULL"
                " WHERE status = 'claimed' AND claimed_at < ?",
                (cutoff,),
            )
            return self.conn.execute(
                "UPDATE send_jobs SET status = 'failed', error = ?, claimed_at = NULL"
                " WHERE status = 'sending' AND claimed_at < ?",
                (INTERRUPTED, cutoff),
            ).rowcount

    # ------------------------------------------------------------------ sending

    def _claim(self) -> list[sqlite3.Row]:
        stamp = iso(self.clock())
        with transaction(self.conn):
            jobs = self.conn.execute(
                "SELECT j.campaign_id, j.contact_id, j.email, j.attempts FROM send_jobs j"
                " JOIN campaigns c ON c.id = j.campaign_id"
                " WHERE j.status = 'queued' AND j.next_attempt_at <= ? AND c.status = 'sending'"
                " ORDER BY j.next_attempt_at LIMIT ?",
                (stamp, self.batch_size),
            ).fetchall()
            self.conn.executemany(
                "UPDATE send_jobs SET status = 'claimed', claimed_at = ?"
                " WHERE campaign_id = ? AND contact_id = ?",
                [(stamp, j["campaign_id"], j["contact_id"]) for j in jobs],
            )
        return jobs

    def provider(self) -> Provider:
        if self._provider is None:
            self._provider = self.provider_factory(self.conn)
        return self._provider

    def send_batch(self, result: TickResult) -> None:
        jobs = self._claim()
        if not jobs:
            return
        rate = settings.get_group(self.conn, "sending", Sending).rate_per_second
        interval = 1 / rate if rate else 0.0
        secret = settings.secret_key(self.conn)
        snapshots: dict[str, dict[str, Any]] = {}

        for index, job in enumerate(jobs):
            started = time.monotonic()
            campaign_id, contact_id = job["campaign_id"], job["contact_id"]
            state = self._campaign_status(campaign_id)
            if state == "paused":
                self._release([job])  # back in the queue for when the campaign resumes
                continue
            if campaign_id not in snapshots:
                row = self.conn.execute(
                    "SELECT snapshot FROM campaigns WHERE id = ?", (campaign_id,)
                ).fetchone()
                snapshots[campaign_id] = json.loads(row["snapshot"])
            reason = self._skip_reason(campaign_id, contact_id, job["email"])
            if reason:
                self._finish_job(job, "skipped", error=reason)
                result.skipped += 1
                continue

            message = self._message(snapshots[campaign_id], campaign_id, contact_id, secret)
            self._mark_sending(job)
            try:
                outcome = self.provider().send(message)
            except ProviderUnavailable as exc:
                # Nothing was sent: put the rest of the batch back and pause until sending is set up.
                self._release(jobs[index:])
                self._pause_sending(str(exc))
                return
            self._record(job, outcome, result)

            if interval:
                self.sleep(max(0.0, interval - (time.monotonic() - started)))

    def _campaign_status(self, campaign_id: str) -> str | None:
        row = self.conn.execute("SELECT status FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
        return row["status"] if row else None

    def _skip_reason(self, campaign_id: str, contact_id: str, email: str) -> str | None:
        """Re-check just before sending: people can unsubscribe or be suppressed after queueing."""
        conn = self.conn
        if self._campaign_status(campaign_id) in (None, "cancelled"):
            return "Campaign cancelled"
        if conn.execute("SELECT 1 FROM suppressions WHERE email = ?", (email,)).fetchone():
            return "Address is suppressed"
        subscribed = conn.execute(
            "SELECT 1 FROM list_members m JOIN campaign_lists l ON l.list_id = m.list_id"
            " WHERE l.campaign_id = ? AND m.contact_id = ? AND m.status = 'subscribed'",
            (campaign_id, contact_id),
        ).fetchone()
        return None if subscribed else "No longer subscribed"

    def _message(self, snapshot: dict[str, Any], campaign_id: str, contact_id: str, secret: bytes) -> Any:
        row = self.conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
        contact = {**dict(row), "fields": json.loads(row["fields"])}
        token = tokens.make(secret, contact_id, campaign_id)
        unsubscribe_url = f"{self.app.base_url}/u/{token}"
        # The web version uses the same token: it's the reader's own copy.
        values = render.merge_values(contact, unsubscribe_url, f"{self.app.base_url}/v/{token}")
        email = mailer.build(
            mailer.Message(
                to=contact["email"],
                from_name=snapshot["from_name"],
                from_email=snapshot["from_email"],
                reply_to=snapshot["reply_to"],
                subject=render.personalise(snapshot["subject"], values, escape=False),
                html=render.personalise(snapshot["html"], values, escape=True),
                text=render.personalise(snapshot["text"], values, escape=False),
                unsubscribe_url=unsubscribe_url,
            )
        )
        email["X-Mailstudio-Campaign"] = campaign_id
        return email

    def _record(self, job: sqlite3.Row, result: Result, tally: TickResult) -> None:
        if result.outcome is Outcome.SENT:
            self._finish_job(job, "sent", message_id=result.message_id)
            tally.sent += 1
            return
        attempts = job["attempts"] + 1
        if result.outcome is Outcome.RETRY and attempts <= len(RETRY_DELAYS):
            retry_at = iso(self.clock() + RETRY_DELAYS[attempts - 1])
            with transaction(self.conn):
                self.conn.execute(
                    "UPDATE send_jobs SET status = 'queued', attempts = ?, next_attempt_at = ?,"
                    " claimed_at = NULL,"
                    " error = ? WHERE campaign_id = ? AND contact_id = ?",
                    (attempts, retry_at, result.error, job["campaign_id"], job["contact_id"]),
                )
            tally.retried += 1
            return
        error = (
            result.error
            if result.outcome is Outcome.FAILED
            else f"Gave up after {attempts} attempts: {result.error}"
        )
        self._finish_job(job, "failed", error=error, attempts=attempts)
        tally.failed += 1

    def _finish_job(
        self,
        job: sqlite3.Row,
        status: str,
        *,
        error: str | None = None,
        message_id: str | None = None,
        attempts: int | None = None,
    ) -> None:
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE send_jobs SET status = ?, error = ?, provider_message_id = ?, claimed_at = NULL,"
                " sent_at = CASE WHEN ? = 'sent' THEN ? ELSE NULL END, attempts = ?"
                " WHERE campaign_id = ? AND contact_id = ?",
                (
                    status,
                    error,
                    message_id,
                    status,
                    iso(self.clock()),
                    attempts if attempts is not None else job["attempts"] + (status == "sent"),
                    job["campaign_id"],
                    job["contact_id"],
                ),
            )

    def _mark_sending(self, job: sqlite3.Row) -> None:
        """Committed before the provider is called: if we crash during the call, recovery
        knows this one may have gone out."""
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE send_jobs SET status = 'sending', claimed_at = ?"
                " WHERE campaign_id = ? AND contact_id = ?",
                (iso(self.clock()), job["campaign_id"], job["contact_id"]),
            )

    def _release(self, jobs: list[sqlite3.Row]) -> None:
        """Return jobs to the queue unchanged (nothing was sent for them)."""
        with transaction(self.conn):
            self.conn.executemany(
                "UPDATE send_jobs SET status = 'queued', claimed_at = NULL"
                " WHERE campaign_id = ? AND contact_id = ? AND status IN ('claimed', 'sending')",
                [(j["campaign_id"], j["contact_id"]) for j in jobs],
            )

    def _pause_sending(self, reason: str) -> None:
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE campaigns SET status = 'paused', last_error = ?, updated_at = ?"
                " WHERE status = 'sending'",
                (reason, now()),
            )
        log.warning("Sending paused: %s", reason)

    # ---------------------------------------------------------------- finishing

    def finish_campaigns(self) -> int:
        stamp = iso(self.clock())
        with transaction(self.conn):
            return self.conn.execute(
                "UPDATE campaigns SET status = 'sent', sent_at = ?, updated_at = ? WHERE status = 'sending'"
                " AND NOT EXISTS (SELECT 1 FROM send_jobs j WHERE j.campaign_id = campaigns.id"
                "                 AND j.status IN ('queued', 'claimed', 'sending'))",
                (stamp, now()),
            ).rowcount


def build_snapshot(conn: sqlite3.Connection, app: Settings, campaign: dict[str, Any]) -> dict[str, Any]:
    """Render the campaign once, ready for per-recipient merge tags."""
    library = load(app.sections_dir)
    document = Document.model_validate(campaign["document"])
    check_sections(document, library)
    business = settings.get_group(conn, "business", Business)
    rendered = render.render(
        document,
        library,
        subject=campaign["subject"],
        preheader=campaign["preheader"],
        business=render.Business(name=business.name or "This email was sent by us", address=business.address),
        asset_base=app.base_url,
    )
    return {
        "subject": rendered.subject,
        "html": rendered.html,
        "text": rendered.text,
        "from_name": campaign["from_name"],
        "from_email": campaign["from_email"],
        "reply_to": campaign["reply_to"],
    }
