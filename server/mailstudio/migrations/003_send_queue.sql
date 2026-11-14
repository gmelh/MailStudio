-- 003: the send queue.

-- Frozen copy of what was sent (subject, HTML, text with merge tags, sender),
-- taken when sending starts, so later edits or template changes never alter it.
ALTER TABLE campaigns ADD COLUMN snapshot TEXT;
ALTER TABLE campaigns ADD COLUMN started_at TEXT;
ALTER TABLE campaigns ADD COLUMN last_error TEXT;

-- One row per recipient per campaign. The primary key and the unique address
-- constraint are what guarantee nobody is queued twice for the same campaign.
--
-- status: queued   waiting (next_attempt_at says when)
--         claimed  taken by the worker in a batch, not yet handed to the provider
--         sending  being handed to the provider right now; becomes sent / queued (retry) / failed
--         sent     accepted by the provider
--         failed   permanently rejected, out of retries, or interrupted mid-send
--         skipped  unsubscribed, suppressed or erased after queueing, or campaign cancelled
CREATE TABLE send_jobs (
    campaign_id         TEXT NOT NULL REFERENCES campaigns (id) ON DELETE CASCADE,
    contact_id          TEXT NOT NULL REFERENCES contacts (id) ON DELETE CASCADE,
    email               TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'queued'
                        CHECK (status IN ('queued', 'claimed', 'sending', 'sent', 'failed', 'skipped')),
    attempts            INTEGER NOT NULL DEFAULT 0,
    next_attempt_at     TEXT NOT NULL,
    claimed_at          TEXT,
    provider_message_id TEXT,
    error               TEXT,
    sent_at             TEXT,
    PRIMARY KEY (campaign_id, contact_id),
    UNIQUE (campaign_id, email)
);
CREATE INDEX send_jobs_due ON send_jobs (status, next_attempt_at);
CREATE INDEX send_jobs_by_contact ON send_jobs (contact_id);
