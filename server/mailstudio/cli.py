"""Command line: `mailstudio serve | worker | migrate | backup | create-api-key | ...`."""

from __future__ import annotations

import argparse
import sqlite3
import sys

from .auth import SCOPES, create_key, parse_scopes
from .config import Settings
from .db import connect, migrate, now, transaction


def _serve(args: argparse.Namespace) -> int:
    import os

    import uvicorn

    # Behind the installer's Caddy, the app is only reachable through the proxy, so it
    # trusts the proxy's X-Forwarded-For (for signup limits and consent records).
    trusted = os.environ.get("MAILSTUDIO_TRUSTED_PROXIES", "127.0.0.1")
    uvicorn.run(
        "mailstudio.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        proxy_headers=True,
        forwarded_allow_ips=trusted,
    )
    return 0


def _backup(args: argparse.Namespace) -> int:
    """A consistent copy of the database while the app keeps running (SQLite's online backup)."""
    from pathlib import Path

    settings = Settings.from_env()
    target = (
        Path(args.path) if args.path else settings.backup_dir / f"mailstudio-{now()[:19].replace(':', '')}.db"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    source = connect(settings.database_path)
    destination = sqlite3.connect(target)  # plain journal mode: the copy is one self-contained file
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    print(target)
    return 0


def _worker(_: argparse.Namespace) -> int:
    import logging
    import signal
    import threading

    from .providers import configured_provider
    from .worker import Worker

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    settings = Settings.from_env()
    migrate(settings)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    Worker(settings, configured_provider).run(stop)
    return 0


def _migrate(_: argparse.Namespace) -> int:
    applied = migrate(Settings.from_env())
    print("Applied: " + ", ".join(applied) if applied else "Database is up to date.")
    return 0


def _create_key(args: argparse.Namespace) -> int:
    try:
        scopes = parse_scopes(args.scopes)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    settings = Settings.from_env()
    migrate(settings)
    conn = connect(settings.database_path)
    with transaction(conn):
        key = create_key(conn, args.name, scopes)
    conn.close()
    print(f"Created API key {key.id} ({' '.join(sorted(scopes))})\n")
    print(f"    {key.token}\n")
    print("Copy it now: it won't be shown again.")
    return 0


def _reset_password(args: argparse.Namespace) -> int:
    import secrets

    from .accounts import end_other_sessions, hash_password
    from .schemas import normalise_email

    settings = Settings.from_env()
    migrate(settings)
    conn = connect(settings.database_path)
    password = secrets.token_urlsafe(12)
    with transaction(conn):
        row = conn.execute("SELECT id FROM users WHERE email = ?", (normalise_email(args.email),)).fetchone()
        if row:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), row["id"])
            )
            end_other_sessions(conn, row["id"], None)
    conn.close()
    if not row:
        print("No user with that email address.", file=sys.stderr)
        return 1
    print(
        f"New password for {args.email}:\n\n    {password}\n\nThey've been logged out everywhere. "
        "Ask them to change it after logging in."
    )
    return 0


def _list_keys(_: argparse.Namespace) -> int:
    settings = Settings.from_env()
    migrate(settings)
    conn = connect(settings.database_path)
    rows = conn.execute("SELECT * FROM api_keys ORDER BY id").fetchall()
    conn.close()
    for r in rows:
        state = (
            f"revoked {r['revoked_at']}" if r["revoked_at"] else f"last used {r['last_used_at'] or 'never'}"
        )
        print(f"{r['id']}  {r['prefix']}…  {r['scopes']:<16}  {r['name']}  ({state})")
    if not rows:
        print("No API keys yet. Create one with: mailstudio create-api-key --name <name>")
    return 0


def _revoke_key(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    conn = connect(settings.database_path)
    with transaction(conn):
        changed = conn.execute(
            "UPDATE api_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (now(), args.key_id)
        ).rowcount
    conn.close()
    print("Revoked." if changed else "No active key with that ID.")
    return 0 if changed else 1


def campaign_schema() -> str:
    """The JSON Schema for campaign documents, as committed in schema/campaign.schema.json."""
    import json

    from .document import Document

    schema = Document.model_json_schema(by_alias=True)
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://mailstudio.email/schema/campaign.schema.json",
        **schema,
        "title": "Mail Studio campaign document",
    }
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def _export_schema(args: argparse.Namespace) -> int:
    from pathlib import Path

    Path(args.path).write_text(campaign_schema(), encoding="utf-8")
    print(f"Wrote {args.path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mailstudio", description="Mail Studio server")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="Run the web server and sending worker (applies migrations)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.set_defaults(run=_serve)

    commands.add_parser("migrate", help="Apply database migrations").set_defaults(run=_migrate)
    backup = commands.add_parser(
        "backup", help="Copy the database safely while the app runs; prints the path"
    )
    backup.add_argument("path", nargs="?", help="Where to write it (default: the data folder's backups/)")
    backup.set_defaults(run=_backup)
    commands.add_parser(
        "worker", help="Run only the sending worker (when serve runs with MAILSTUDIO_WORKER=0)"
    ).set_defaults(run=_worker)

    create = commands.add_parser("create-api-key", help="Create an API key and print it once")
    create.add_argument("--name", required=True, help="What the key is for, e.g. 'website signup form'")
    create.add_argument(
        "--scopes", default="read write", help=f"Any of: {' '.join(SCOPES)} (default: read write)"
    )
    create.set_defaults(run=_create_key)

    commands.add_parser("list-api-keys", help="List API keys").set_defaults(run=_list_keys)

    reset = commands.add_parser(
        "reset-password", help="Set a new random password for a user who is locked out"
    )
    reset.add_argument("--email", required=True)
    reset.set_defaults(run=_reset_password)

    revoke = commands.add_parser("revoke-api-key", help="Revoke an API key")
    revoke.add_argument("key_id")
    revoke.set_defaults(run=_revoke_key)

    export = commands.add_parser("export-schema", help="Write the campaign document JSON Schema")
    export.add_argument("path", nargs="?", default="../schema/campaign.schema.json")
    export.set_defaults(run=_export_schema)

    args = parser.parse_args(argv)
    result: int = args.run(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
