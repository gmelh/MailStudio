-- 005: people who log in to the web app, and their sessions.

CREATE TABLE users (
    id            TEXT PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,  -- normalised to lower case
    name          TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,         -- scrypt$<n>$<r>$<p>$<salt>$<hash>
    created_at    TEXT NOT NULL,
    last_login_at TEXT
);

-- The session cookie holds a random token; only its SHA-256 is stored here.
CREATE TABLE sessions (
    token_hash   TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    csrf_token   TEXT NOT NULL,          -- sent back by the app on every change
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE INDEX sessions_by_user ON sessions (user_id);
CREATE INDEX sessions_by_expiry ON sessions (expires_at);
