# Working on Mail Studio

This guide gets you from a fresh clone to running Mail Studio under the debugger, and
explains how the project fits together. For installing Mail Studio on a server, see
[install/](install/README.md); for using it, see [the book](docs/The-Mail-Studio-Book.pdf).

- [What you need](#what-you-need)
- [Setting up](#setting-up)
- [Running and debugging](#running-and-debugging)
- [Sending email locally](#sending-email-locally)
- [Checks and tests](#checks-and-tests)
- [How the project fits together](#how-the-project-fits-together)
- [Making common changes](#making-common-changes)
- [Conventions](#conventions)
- [Releasing](#releasing)
- [When something goes wrong](#when-something-goes-wrong)

## What you need

- **Python 3.12** or newer. On Windows, tick *Add python.exe to PATH* when installing.
- **Node 22** or newer (for building the web app).
- **Git**.
- **VS Code** is recommended: the repository includes debug configurations and tasks for it.
- **Docker** is optional: only for catching test emails locally (Mailpit) or building the image.

## Setting up

### With VS Code

1. Clone the repository and open its folder in VS Code (the folder that contains `server`,
   `web` and `.vscode`).
2. Install the recommended extensions: Extensions view → search `@recommended` → install
   **Python**, **Python Debugger** and **Ruff**.
3. **Terminal → Run Task… → setup: everything (run once)**. This creates a Python virtual
   environment in `.venv`, installs the server in editable mode with its development tools,
   and installs the web app's packages.
4. **Ctrl+Shift+P → Python: Select Interpreter →** the one in `.venv`.

### Without VS Code

```sh
python3 -m venv .venv
.venv/bin/pip install -e "server[dev]"        # Windows: .venv\Scripts\pip install -e "server[dev]"
cd web && npm ci && cd ..
```

## Running and debugging

### With VS Code

Open **Run and Debug** (Ctrl+Shift+D), pick a configuration, press **F5**.

| Configuration | What it does |
| --- | --- |
| **Server (with sending worker)** | The whole app at http://localhost:8080, under the Python debugger. Starts the **web: watch** task first, which rebuilds the web app whenever you save. |
| **Server without worker** | The web server only. Use with **Worker only** to debug sending on its own. |
| **Worker only** | The background sender that delivers campaigns. |
| **Tests: this file** | Runs the open test file under the debugger. |
| **Web app in Edge / Chrome** | Opens the app in a browser VS Code is attached to, so breakpoints in `web/src` work. Start the server first. |
| **Everything: server + web app in Edge** | Both at once. |

The first time you open http://localhost:8080 you create a local account; you then land in
**Get set up**. Local data (the SQLite database and uploaded images) lives in `./data`, which
Git ignores. Delete that folder for a fresh start.

- Python changes need a restart (**Ctrl+Shift+F5**).
- Web changes rebuild on save; refresh the browser.

### Without VS Code

```sh
cd web && npm run dev &                 # rebuilds web/dist on change
cd .. && .venv/bin/mailstudio serve     # http://localhost:8080, data in ./data
```

`mailstudio --help` lists the other commands: `worker`, `migrate`, `backup`,
`create-api-key`, `reset-password`, `export-schema` and so on.

### Settings for local runs

The server is configured with environment variables; the VS Code configurations set the
first three.

| Variable | Default | Meaning |
| --- | --- | --- |
| `MAILSTUDIO_DATA_DIR` | `./data` | Database, backups and uploaded images |
| `MAILSTUDIO_BASE_URL` | `http://localhost:8080` | The public address, used in links in emails |
| `MAILSTUDIO_ALLOW_SMTP_SENDING` | off | Lets campaigns go out by SMTP (for Mailpit) instead of only Amazon SES |
| `MAILSTUDIO_WORKER` | `1` | `0` runs the web server without the sending worker |
| `MAILSTUDIO_SECTIONS_DIR` | `sections/` in the repository | Email section templates |
| `MAILSTUDIO_WEB_DIR` | `web/dist` in the repository | The built web app |
| `MAILSTUDIO_TRUSTED_PROXIES` | `127.0.0.1` | Proxies whose `X-Forwarded-For` is trusted |

## Sending email locally

Real email goes through Amazon SES, which you don't want while developing. Instead, catch
everything with [Mailpit](https://mailpit.axllent.org):

```sh
docker compose up -d mailpit
```

Then in the app, **Settings → Test emails**: tick *Send test emails through my own mail
server*, server `127.0.0.1`, port `1025`, security **None**. With
`MAILSTUDIO_ALLOW_SMTP_SENDING=1` (set by the VS Code configurations), test emails *and*
campaigns go to Mailpit. Read them at http://localhost:8025.

Alternatively, `docker compose up` runs the whole app plus Mailpit in containers, without
the debugger.

## Checks and tests

Everything below runs on GitHub for every push and pull request (`.github/workflows/ci.yml`).
Run it locally first with **Terminal → Run Task… → check: everything (as on GitHub)**, or:

```sh
cd server
ruff check . && ruff format --check .   # lint and formatting
mypy mailstudio                         # strict type checking
pytest -q                               # tests
cd ../web
npm run typecheck && npm run build
shellcheck ../install/install.sh        # if you changed the installer
```

- In VS Code, the **Testing** view (beaker icon) lists every test to run or debug one by one.
- Ruff formats Python on save (set in `.vscode/settings.json`).
- Tests use FastAPI's test client against a temporary data folder; nothing touches `./data`.
  Useful fixtures in `server/tests/conftest.py`: `client`, `auth` (headers with a full-access
  API key), `make_key(scopes)`, `settings`, `CONSENT`.
- Amazon calls are tested with botocore's `Stubber` (see `tests/test_setup.py`), never live.
- To try the installer safely: `bash install/install.sh --dry-run --dir /tmp/ms --domain mail.example.com`.

## How the project fits together

```
server/          Python 3.12, FastAPI, SQLite: the API, the sending worker, public pages
web/             Preact + TypeScript, built with esbuild into web/dist, served by the server
sections/        The email building blocks (MJML + Mustache), shared by server and editor
install/         The one-line installer and the `mailstudio` command it adds to servers
docs/            The book (docs/book/book.html → PDF) and other documentation
schema/          JSON Schema for campaign documents, generated by `mailstudio export-schema`
.github/         Checks (ci.yml) and releases (release.yml)
.vscode/         Debug configurations, tasks and settings
```

### The server (`server/mailstudio/`)

| Area | Files |
| --- | --- |
| Start-up and wiring | `app.py` (builds the FastAPI app, runs migrations), `cli.py`, `config.py`, `web.py` (serves `web/dist`) |
| Database | `db.py` (connections, migrations with automatic backup), `migrations/NNN_*.sql`, `repositories/` (plain SQL per table group) |
| API | `api/*.py`, one router per area, all under `/api/v1`; request and response models in `schemas.py` |
| Accounts and access | `accounts.py` (users, passwords, sessions), `auth.py` (API keys), `deps.py` (`require(scope)`, `run_write`, paging) |
| Email rendering | `document.py` (campaign document model), `sections.py` (loads `sections/`), `render.py` (MJML via mrml, dark mode, merge tags) |
| Sending | `worker.py` (the queue), `providers.py`, `ses.py`, `mailer.py` (SMTP and message building), `tokens.py` (unsubscribe and confirmation links) |
| Bounces, complaints, domains | `notifications.py` (SNS webhook), `domain.py` (DNS checks), `aws_setup.py` (creates the SNS topic and configuration set) |
| Public pages | `public.py` (unsubscribe, webhooks, images), `signup.py` (signup forms, double opt-in) |
| Other | `importer.py` (CSV import), `assets.py` (image uploads), `ids.py`, `errors.py` |

A request flows: router in `api/` → dependency checks (`require(scope)`) → `run_write`
(one transaction, idempotency keys) → functions in `repositories/` → SQLite.

### Sending, in short

Scheduling a campaign snapshots its rendered email and queues one job per recipient. The
worker claims jobs in batches, marks each `sending` *before* calling the provider, then
`sent` or `failed`. A job left in `sending` after a crash is never retried, so nobody gets an
email twice. Temporary failures retry with backoff (1 m, 5 m, 30 m, 2 h, 6 h); account-level
problems pause the campaign.

### The web app (`web/src/`)

| File or folder | What it is |
| --- | --- |
| `main.tsx`, `app.tsx` | Start-up; chooses the first-run, login or main screens |
| `router.tsx` | A small router (`Link`, `navigate`, `match`) |
| `api.ts` | Typed calls to the server, and the types for everything it returns |
| `session.tsx`, `ui.tsx`, `theme.ts` | Signed-in state, shared components and hooks, theme |
| `pages/` | One file per screen; `Shell.tsx` holds the sidebar and the route table |
| `editor/` | The email editor: `CampaignEditor.tsx` (page), `model.ts` (document changes), `Outline.tsx`, `Preview.tsx` (server-rendered preview in an iframe), `Inspector.tsx`, `fields.tsx` |
| `styles.css` | All the styles |

## Making common changes

**An API endpoint.** Add it to the right file in `server/mailstudio/api/`, with request and
response models in `schemas.py` and SQL in `repositories/`. Write tests in `server/tests/`.
Add the endpoint to the table in `server/README.md`, and its TypeScript types to
`web/src/api.ts` if the web app uses it.

**A database change.** Add the next numbered file in `server/mailstudio/migrations/`, e.g.
`008_something.sql`. Migrations run automatically on start, after a backup of the database.
Never edit a migration that's been released; add a new one.

**An email section.** Add its definition to `sections/sections.json` and its template to
`sections/templates/`. The editor picks it up automatically. See `sections/README.md` for
the format, and start from a similar template: keep its `ms-…` CSS classes (such as
`ms-s-{{section_id}}`, `ms-text`, `ms-button`), which dark mode and the editor's
click-to-select rely on. Run `mailstudio export-schema` if the document format changed.

**A screen in the web app.** Add a file in `web/src/pages/` and a route in
`pages/Shell.tsx`.

**The installer.** Edit `install/install.sh`, then run shellcheck and a `--dry-run`. Remember
it runs as root on other people's servers: fail loudly, never silently.

**The book.** Edit `docs/book/book.html`, then rebuild the PDF (needs Python with playwright,
pdfplumber and pypdf): `cd docs/book && python render.py && mv The-Mail-Studio-Book.pdf ..`.

## Conventions

- **Words.** Everything people see is written for a small-business owner, not a developer:
  plain words, say what to do next, no jargon.
- **API.** Prefixed IDs (`ctc_…`, `lst_…`, `cmp_…`), errors as RFC 9457 problem details,
  cursor pagination, an `Idempotency-Key` header on writes.
- **Python.** Ruff for linting and formatting, mypy in strict mode, synchronous handlers,
  plain SQL (no ORM).
- **Line endings.** LF everywhere, enforced by `.gitattributes`: the scripts run on Linux.
- **Commits.** Small and focused, with a message saying why. Check everything passes before
  pushing.

## Releasing

See [install/README.md](install/README.md#releasing). In short: set the version in
`server/pyproject.toml`, `server/mailstudio/__init__.py` and `web/package.json`, commit,
then `git tag -a v0.8.0 -m "Mail Studio 0.8.0" && git push origin v0.8.0`. GitHub runs the
checks, publishes the image and creates the release.

## When something goes wrong

| Problem | Fix |
| --- | --- |
| *No module named mailstudio* | Select the `.venv` interpreter (Ctrl+Shift+P → Python: Select Interpreter). |
| *Address already in use* | Something else has port 8080. Stop it, or change the port in `.vscode/launch.json`, including `MAILSTUDIO_BASE_URL`. |
| The page is blank, or says the web app hasn't been built | The web task failed: look for `[ERROR]` in its terminal tab, or run `npm run build` in `web/`. |
| Style changes don't show | Refresh the browser with Ctrl+F5, which bypasses the cache. |
| VS Code doesn't list the Mail Studio tasks | You opened the wrong folder: it must contain `.vscode`. Then Developer: Reload Window. |
| Campaigns don't send locally | Check Mailpit is running and set up in Settings, and that the server was started with `MAILSTUDIO_ALLOW_SMTP_SENDING=1`. |
| Python packages fail to install on Windows | Check you're on Python 3.12+ (`py -3.12 --version`). If a package needs Rust or a compiler, report it. |