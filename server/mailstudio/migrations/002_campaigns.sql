-- 002: install settings and campaigns.

-- Install-wide settings as JSON values: business details, sender defaults,
-- brand profile, SMTP for test sends, and the secret that signs unsubscribe links.
CREATE TABLE settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE campaigns (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    subject      TEXT NOT NULL DEFAULT '',
    preheader    TEXT NOT NULL DEFAULT '',
    from_name    TEXT NOT NULL DEFAULT '',
    from_email   TEXT NOT NULL DEFAULT '',
    reply_to     TEXT NOT NULL DEFAULT '',
    document     TEXT NOT NULL,          -- campaign document JSON (schema 2: brand + sections)
    status       TEXT NOT NULL DEFAULT 'draft'
                 CHECK (status IN ('draft', 'scheduled', 'sending', 'paused', 'sent', 'cancelled')),
    scheduled_at TEXT,
    sent_at      TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX campaigns_by_status ON campaigns (status, id);

-- Who a campaign goes to: subscribed members of any of its lists,
-- narrowed to contacts with at least one of its tags when tags are given.
CREATE TABLE campaign_lists (
    campaign_id TEXT NOT NULL REFERENCES campaigns (id) ON DELETE CASCADE,
    list_id     TEXT NOT NULL REFERENCES lists (id) ON DELETE CASCADE,
    PRIMARY KEY (campaign_id, list_id)
);

CREATE TABLE campaign_tags (
    campaign_id TEXT NOT NULL REFERENCES campaigns (id) ON DELETE CASCADE,
    tag         TEXT NOT NULL,
    PRIMARY KEY (campaign_id, tag)
);
