# MailStudio server

The API and the sending worker. Python 3.12+, FastAPI, SQLite, and
[mrml](https://github.com/jdrouet/mrml) for rendering email from the shared [`sections/`](../sections/README.md).

## Run it locally

```sh
cd server
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

mailstudio create-api-key --name "local dev" --scopes "read write send"
mailstudio serve            # http://127.0.0.1:8080/api/docs
```

Data lives in `./data` (set `MAILSTUDIO_DATA_DIR` to change it). Migrations run
automatically on start; the database is copied to `data/backups/` first whenever
a new migration is about to be applied. `mailstudio backup [PATH]` makes a consistent
copy of the database while the app runs (the installer's `mailstudio backup` uses it).

### Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `MAILSTUDIO_DATA_DIR` | `./data` | Database, backups and assets |
| `MAILSTUDIO_TRUSTED_PROXIES` | `127.0.0.1` | Proxies whose `X-Forwarded-For` is trusted (the installer sets `*`: only Caddy can reach the app) |
| `MAILSTUDIO_BASE_URL` | `http://localhost:8080` | The install's public address, used in unsubscribe links |
| `MAILSTUDIO_WEB_DIR` | `../web/dist` | The built web app, served at `/` |
| `MAILSTUDIO_WORKER` | `1` | Run the sending worker inside `mailstudio serve`; set `0` and run `mailstudio worker` separately if preferred |
| `MAILSTUDIO_GALLERY_URL` | `https://mailstudio.email/gallery/index.json` | The online template gallery offered under New campaign; set it empty to turn the gallery off |
| `MAILSTUDIO_ALLOW_SMTP_SENDING` | unset | Development only: with no sending service chosen, send campaigns over the test-email SMTP in Settings (e.g. Mailpit) |

## Choosing a sending service

Campaigns go through one service, chosen in Settings (`"provider": {"active": "…"}`):

| `active` | Service | Sends with | Bounces, complaints and unsubscribes arrive by |
| --- | --- | --- | --- |
| `postmark` | Postmark | `POST /email` on a Broadcasts message stream | Webhook: Bounce, SpamComplaint, SubscriptionChange |
| `mailgun` | Mailgun (US or EU) | `POST /v3/<domain>/messages.mime` (the finished MIME message) | Webhook, HMAC-signed: failed, complained, unsubscribed |
| `smtp2go` | SMTP2GO | `POST /v3/email/mime` (the finished MIME message) | Webhook (JSON or form): bounce, spam, unsubscribe |
| `authsmtp` / `smtp` | AuthSMTP or any SMTP server | SMTP, with the bounce address as envelope sender | Bounce mailbox read over IMAP every 5 minutes |
| `ses` | Amazon SES | `SendEmail` with the raw message | SNS (see below) |

Installs from before the choice existed keep sending through SES. Credentials go in their own
settings groups (`postmark`, `mailgun`, `smtp2go`, `relay`, `ses`); secrets are write-only.
`POST /api/v1/sending/check` tries the saved credentials for real (and, for SMTP, the bounce
mailbox) and says what to fix.

**Webhooks.** Each service posts to `https://<your address>/webhooks/<service>/<token>`, where the
token is derived from the install's secret, so the address can't be guessed;
`GET /api/v1/sending/account` returns it as `webhook_url`. A wrong token gets 404. Mailgun's
events are also checked against its HTTP webhook signing key when one is saved. Settings shows
bounce handling as working once the first event arrives.

**Postmark and unsubscribes.** Broadcast streams add Postmark's own unsubscribe link and
one-click headers unless Postmark support has switched on "Manage unsubscribes on your own".
Until then MailStudio leaves its List-Unsubscribe headers out (two sets break one-click
unsubscribe) and takes Postmark's unsubscribes from the SubscriptionChange webhook. Once it's
on, tick `postmark.own_unsubscribes` and MailStudio's headers are sent.

**SMTP bounces.** SMTP can't report bounces, so relays send with `relay.bounce_address` as the
SMTP envelope sender (AuthSMTP needs it authorised, like the from address), and the worker
reads that mailbox (`relay.imap_*`) every 5 minutes. Delivery status reports (RFC 3464),
spam reports (ARF) and old-style "Returned mail" bounces that name one address are applied
and marked read; anything else stays unread for a person. The original Message-ID in the
report links a bounce to its campaign.

Problems that would stop every message (bad credentials, an unverified sender, a refused
sender address) pause the campaign, as for SES, instead of failing each email.

## Connecting Amazon SES

Until the Phase 2 setup wizard automates it, connecting SES takes five steps:

1. **Access key.** In AWS IAM, create a user for MailStudio with an access key and a policy
   allowing `ses:SendEmail`, `ses:SendRawEmail`, `ses:GetAccount`, `ses:GetEmailIdentity`
   and `ses:CreateEmailIdentity`.
2. **Settings.** `PATCH /api/v1/settings` with
   `{"ses": {"region": "ap-southeast-2", "access_key_id": "…", "secret_access_key": "…"}}`.
   The secret is write-only.
3. **Domain.** `POST /api/v1/sending/domain {"domain": "acme.com.au"}` returns the DKIM and
   DMARC records to add. `GET /api/v1/sending/domain/acme.com.au` re-checks them live;
   `ready` turns true once SES has verified the domain.
4. **Bounces and complaints.** Create an SNS topic, set it as the domain identity's bounce,
   complaint (and optionally delivery) notification topic in SES, and put its ARN in
   Settings as `ses.notification_topic_arn`. Then subscribe `https://<your address>/webhooks/ses`
   to the topic (HTTPS); MailStudio confirms the subscription itself.
5. **Production access.** New SES accounts start in the sandbox. `GET /api/v1/sending/account`
   warns until production access is granted, and also if the sending rate in Settings is
   above the account's limit.

Notifications are trusted only when their SNS signature verifies against an Amazon
certificate *and* they come from the configured topic, since anyone can make SNS send
signed messages from their own AWS account. Permanent bounces and complaints suppress the
address everywhere; soft bounces are recorded only.

Or with Docker, from the repository root: `docker compose up --build`.

## Checks

```sh
pytest
ruff check . && ruff format --check .
mypy
```

## API at a glance

All endpoints are under `/api/v1`. Integrations send `Authorization: Bearer <key>`; the web
app uses a session cookie instead, and must send the session's `X-CSRF-Token` header on
every change. Both reach the same endpoints.
Interactive docs: `/api/docs`. OpenAPI: `/api/v1/openapi.json`.

| Method | Path | Scope | Does |
| --- | --- | --- | --- |
| GET | `/health` | none | Health check |
| GET | `/me` | read | Describe the current key |
| GET | `/auth/status` | none | Whether setup is needed, and who is logged in |
| POST | `/auth/setup` | none | Create the first account (only while there are none) |
| POST | `/auth/login` · `/auth/logout` | none · session | Log in (10 failures per 15 min per email/address, then blocked) / out |
| GET | `/auth/me` | session | Current user and CSRF token |
| POST | `/auth/password` | session | Change password; logs out the account's other sessions |
| GET / POST / DELETE | `/users`, `/users/{id}` | session | People who can log in (API keys can't manage these) |
| GET / POST | `/contacts` | read / write | List (search with `q`; filter by `list_id`, `status`, `tag`, `email`) / create |
| GET / PATCH / DELETE | `/contacts/{id}` | read / write | Get / update / erase |
| GET | `/contacts/{id}/history` | read | Consent history: every subscribe, confirm and unsubscribe, with its source |
| POST | `/contacts/import` | write | CSV import (multipart: `file`, `consent_source`, optional `list_id`, `tags`) |
| GET / POST | `/lists` | read / write | List / create |
| GET / PATCH / DELETE | `/lists/{id}` | read / write | Get / update / delete |
| GET / POST | `/lists/{id}/members` | read / write | List members / subscribe contacts |
| POST | `/lists/{id}/members/{contact_id}/unsubscribe` | write | Unsubscribe |
| GET / POST | `/suppressions` | read / write | List / add a manual suppression |
| GET / DELETE | `/suppressions/{email}` | read / write | Check / lift a manual suppression |
| GET / PATCH | `/settings` | read / write | Business details, sender defaults, brand profile, the sending service and its credentials, test-email SMTP (secrets write-only) |
| GET / POST | `/campaigns` | read / write | List / create a draft (defaults to a starter email in the brand) |
| GET / PATCH / DELETE | `/campaigns/{id}` | read / write | Get / update / delete (drafts only) |
| POST | `/campaigns/{id}/preview` | read | Rendered subject, HTML and text, personalised for a contact or sample data |
| POST | `/campaigns/{id}/duplicate` | write | Copy any campaign into a new draft (design, subject, sender, audience) |
| POST | `/campaigns/{id}/test` | send | Send to up to 5 addresses, subject prefixed "[Test]": via the test-email SMTP in Settings if set, otherwise the sending service |
| POST | `/render` | read | Render any campaign document without saving it (the editor's live preview uses this) |
| GET | `/sections` | read | Section types, their fields and defaults, and style presets, from `sections/` |
| POST | `/assets` | write | Upload an image (multipart: `file`): resized to 1200px wide and compressed for email |
| GET / DELETE | `/assets/{id}` | read / write | Image details / delete |
| GET | `/campaigns/{id}/share` | read | A link to a web copy of a sent campaign anyone can open (no personal details) |
| GET | `/campaigns/{id}/checks` | read | Image checks: `problems` (images recipients can't load) stop sending; `warnings` cover descriptions, sharpness, weight and image-only emails |
| POST | `/campaigns/{id}/schedule` | send | Check it's ready (images included), then send now or at `at` (ISO time with offset) |
| POST | `/campaigns/{id}/pause` · `/resume` | send | Pause or resume sending |
| POST | `/campaigns/{id}/cancel` | send | Scheduled → back to draft; sending → stop, skip the rest |
| GET | `/campaigns/{id}/stats` | read | Queued, sent, failed, skipped; delivered, bounced and complained as reported by the service; unsubscribed |
| GET | `/sending/account` | read | The sending service, its bounce handling and webhook address, SES sandbox status and quota, and setup warnings |
| POST | `/sending/check` | write | Try the sending service's saved credentials (and an SMTP relay's bounce mailbox); says what to fix |
| POST | `/sending/domain` | write | With SES, register a sending domain and return the DNS records to add; otherwise check DMARC and SPF |
| GET | `/sending/domain/{domain}` | read | Live check of DKIM (SES), DMARC and SPF records, and whether the domain is ready |
| POST | `/sending/notifications` | write | Create the SNS topic and SES configuration set in your AWS account and subscribe this install (needs https) |
| GET | `/sending/iam-policy` | read | The AWS permissions MailStudio needs |
| GET | `/setup` | read | The getting-started checklist: what's done so far |

Public pages (no key): `GET /u/{token}` shows the unsubscribe confirmation;
`POST /u/{token}` unsubscribes, from the page's button or a mail app's one-click
unsubscribe (RFC 8058). A GET never unsubscribes, because mail scanners prefetch links.
`POST /webhooks/ses` receives SES notifications from SNS (see below), and
`POST /webhooks/{postmark|mailgun|smtp2go}/{token}` receives the other services' events.
`GET /assets/{id}.{jpg|png|gif}` serves uploaded images publicly, because mail apps fetch them.
`GET /social/{network}-{circle|plain}-{rrggbb}.png` serves social icons in any colour, made on
first use and kept in the data folder. `GET /v/{token}` is a reader's own web copy of an email
("View in browser", using the same token as unsubscribing), and `GET /v/s/{token}` the shareable
copy, with personal details left out.

### Signup forms and double opt-in

Each list has a `signup` object (`enabled`, `title`, `intro`, `button`, `ask_name`, `thanks`,
`redirect_url`), set with `PATCH /lists/{id}`. When enabled:

- `GET /f/{list_id}` is a hosted signup page in the brand's colours; `POST /f/{list_id}` takes
  ordinary form fields (`email`, `first_name`, `last_name`), so the same form can be pasted
  into any website. A hidden `website` field catches bots, and each client address can sign
  up 20 times an hour.
- With `double_opt_in` on (the default for new lists), people wait as `pending` and get a
  confirmation email. `GET /c/{token}` shows a button; only its POST subscribes them, because
  mail scanners open links. Links work for 14 days. Both the signup and the confirmation are
  recorded in the contact's consent history, with their IP address.
- Everyone sees the same thank-you, whether they're new, already subscribed or blocked, so
  the form can't be used to find out who's on a list. A repeat signup sends at most one
  confirmation email every 10 minutes.
- Signing up again after unsubscribing is allowed: it's the person's own fresh request.

### Designs

- **Preview** and **render** take `"scheme": "light"` or `"dark"` to show the email as mail
  apps in dark mode will. Sent email always contains both: the brand's dark palette goes in a
  `prefers-color-scheme: dark` style block (plus Outlook's `[data-ogsc]` selectors).
- **Formatting** in text fields: `**bold**`, `*italic*`, `[link](https://…)`, a blank line
  between paragraphs, and lines starting `- ` for a list. Everything else is escaped.
- **Merge tags**: `{{first_name}}`, `{{last_name}}`, `{{email}}`, with a fallback:
  `{{first_name|there}}`.

### Conventions

- **IDs** are prefixed and time-sortable: `ctc_…` contacts, `lst_…` lists, `key_…` API keys.
- **Pagination**: `?limit=` (1–200, default 50) and `?cursor=` from the previous page's `next_cursor`.
- **Errors** are RFC 9457 problem details (`application/problem+json`) with a stable `type` URI.
  Validation errors list every problem in `errors`.
- **Idempotency**: send `Idempotency-Key: <unique string>` on POSTs that create things or import.
  Repeating the request within 24 hours returns the original response (marked `Idempotent-Replayed: true`).
- **Every request is one transaction.** A failed request changes nothing.

### Compliance rules built into the API

- Subscribing anyone to a list requires a `consent.source`; every subscription and
  unsubscription is recorded with its source (and IP, when given).
- People who unsubscribed from a list are never re-added by the API or an import
  unless `resubscribe: true` is sent explicitly.
- Suppressed addresses (bounces, complaints, manual) stay suppressed even after
  the contact is erased. Bounce and complaint suppressions can't be lifted.
- Every email gets a working unsubscribe link and the business's postal address:
  if the design has no Footer or Unsubscribe section, the renderer adds one.
- Unsubscribe links are signed, so nobody can unsubscribe others by guessing IDs.

## Sending

`POST /campaigns/{id}/schedule` checks the campaign is ready (business name and
postal address, subject, sender, at least one list, someone to send to, renders
cleanly) and sets it to `scheduled`. The worker then:

1. **Starts it** when due: renders once, freezes the result as the campaign's
   snapshot, and queues one job per recipient, each address once, even across lists.
2. **Sends** at the rate in Settings (`sending.rate_per_second`, default 10), re-checking
   each person just before sending: anyone who unsubscribed or was suppressed since is skipped.
3. **Retries** temporary failures after 1 min, 5 min, 30 min, 2 h and 6 h, then gives up.
   Permanent rejections fail at once.
4. **Marks the campaign sent** when nothing is left.

**Never twice.** Each job is marked `sending` (committed) just before the provider is
called. After a crash, jobs claimed but not yet attempted go back in the queue; the one
being sent at the moment of the crash is marked failed ("delivery unknown") rather than
retried. A crash costs at most one message and can never cause a duplicate. The test
`test_gate_5000_contacts_crash_midway_no_duplicates` proves this on 5,000 contacts.

If no sending provider is connected, or SES stops the account from sending (suspended,
paused, unverified domain, sandbox), the worker pauses the campaign with the reason in
`last_error` and nothing is lost; `resume` continues once it's fixed. SES timeouts after a
request was sent are treated like a crash: marked "delivery unknown", never retried. The
SDK's own retries are switched off for the same reason.

## Rendering

Campaign JSON + brand → section templates (Mustache) → MJML → mrml → HTML, plus an
automatic plain-text part. The result keeps the owner's merge tags
(`{{first_name|there}}`, `{{unsubscribe_url}}`), which are filled per recipient in
one pass with values HTML-escaped. Templates are logic-free, so neither content nor
subscriber data can run code. The campaign document's JSON Schema is generated
from the models: `mailstudio export-schema` writes `schema/campaign.schema.json` (and
`schema/template.schema.json` for template files), and a test fails if a committed copy
is out of date.

Templates (whole designed emails to start from) are described in
[`sections/gallery/README.md`](../sections/gallery/README.md): the file format, the ones
bundled with the app, and how the online gallery at mailstudio.email is built.

## Logins

Passwords are hashed with scrypt (standard library, 64 MiB cost) and must be at least 10
characters. Sessions last 30 days from last use; the cookie is HttpOnly, SameSite=Lax, and
Secure on https installs. Only a hash of each session token is stored. Locked out?
`mailstudio reset-password --email you@example.com` prints a new password.

## Code layout

| Path | What's there |
| --- | --- |
| `mailstudio/app.py` | Application factory |
| `mailstudio/accounts.py` | Password hashing, sessions, login throttling |
| `mailstudio/web.py` | Serves the built web app with route fallback |
| `mailstudio/api/` | Endpoints, one module per resource |
| `mailstudio/repositories/` | All SQL. Repositories never open transactions |
| `mailstudio/deps.py` | DB connection, auth, write transactions with idempotency, pagination |
| `mailstudio/schemas.py` | Request/response models and validation rules |
| `mailstudio/importer.py` | CSV import |
| `mailstudio/document.py` | Campaign document models and checks against the section library |
| `mailstudio/render.py` | Rendering, plain text and merge tags |
| `mailstudio/sections.py` | Loads `sections/` |
| `mailstudio/public.py` | Unsubscribe pages |
| `mailstudio/tokens.py` | Signed unsubscribe tokens |
| `mailstudio/mailer.py` | Message building and SMTP (test sends) |
| `mailstudio/worker.py` | The sending worker |
| `mailstudio/providers.py` | Provider interface, choosing the provider, and SMTP sending (relays and development) |
| `mailstudio/esp.py` | Postmark, Mailgun and SMTP2GO over HTTPS (standard library only), and their connection checks |
| `mailstudio/ses.py` | Amazon SES: sending, account, domain identity |
| `mailstudio/notifications.py` | SNS signature checks; SES, Postmark, Mailgun and SMTP2GO events into suppressions |
| `mailstudio/bounces.py` | The SMTP bounce mailbox: IMAP polling and DSN/ARF parsing |
| `mailstudio/domain.py` | DNS checks for the sending domain |
| `mailstudio/social.py` | Social icons, coloured on demand from `sections/social` |
| `mailstudio/images.py` | Image checks before sending, and pointing MailStudio's own images at the install's address |
| `mailstudio/db.py` | Connection settings, transactions, migrations |
| `mailstudio/migrations/` | Numbered SQL migrations |
| `mailstudio/cli.py` | `mailstudio` command |
