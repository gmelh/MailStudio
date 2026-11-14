-- 006: uploaded images. Files live in <data>/assets/<id>.<ext> and are served
-- publicly at /assets/<id>.<ext>, because mail apps must be able to load them.
CREATE TABLE assets (
    id           TEXT PRIMARY KEY,
    file_name    TEXT NOT NULL,           -- <id>.<ext> on disk and in the URL
    original_name TEXT NOT NULL DEFAULT '',
    content_type TEXT NOT NULL,
    bytes        INTEGER NOT NULL,
    width        INTEGER NOT NULL,
    height       INTEGER NOT NULL,
    created_at   TEXT NOT NULL
);
