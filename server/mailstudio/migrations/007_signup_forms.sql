-- 007: each list's public signup form settings (JSON; see schemas.SignupForm).
ALTER TABLE lists ADD COLUMN signup TEXT NOT NULL DEFAULT '{}';
