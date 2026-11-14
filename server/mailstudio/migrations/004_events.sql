-- 004: delivery events reported by the sending provider (Amazon SES via SNS).

-- type: delivery     the receiving server accepted the message
--       bounce       permanent bounce; the address is also suppressed
--       soft_bounce  temporary bounce (mailbox full, server busy); no action
--       complaint    the recipient marked it as spam; the address is also suppressed
CREATE TABLE events (
    id                  TEXT PRIMARY KEY,
    type                TEXT NOT NULL CHECK (type IN ('delivery', 'bounce', 'soft_bounce', 'complaint')),
    email               TEXT NOT NULL,
    campaign_id         TEXT REFERENCES campaigns (id) ON DELETE SET NULL,
    contact_id          TEXT REFERENCES contacts (id) ON DELETE CASCADE,
    provider_message_id TEXT,
    detail              TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL,
    UNIQUE (provider_message_id, type, email)   -- SNS may deliver a notification more than once
);
CREATE INDEX events_by_campaign ON events (campaign_id, type);
CREATE INDEX send_jobs_by_message ON send_jobs (provider_message_id);
