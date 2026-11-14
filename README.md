# Mail Studio

Self-hosted email campaigns without the marketing machinery.
One container, SQLite, a section-based editor, and a complete API.
MIT licensed. [mailstudio.email](https://mailstudio.email)

| Folder | Contents |
| --- | --- |
| [`server/`](server/README.md) | API and sending worker (Python, FastAPI, SQLite) |
| [`web/`](web/README.md) | The web app (Preact + TypeScript), served by the server |
| [`sections/`](sections/README.md) | Section types and their MJML templates, shared by server and editor |
| [`install/`](install/README.md) | The one-line installer for a server, and the `mailstudio` command it adds |
| [`schema/`](schema/) | JSON Schema for campaign documents (generated) |

## Install

On a fresh Ubuntu or Debian server, with a domain pointed at it:

```sh
curl -fsSL https://get.mailstudio.email | sudo bash
```

Then open the address it gives you and follow **Get set up**. See [install/](install/README.md).

## Status

Phase 1 (sending core), done:

- [x] Project foundation: SQLite with automatic, backed-up migrations; API keys with scopes;
      standard errors, cursor pagination and idempotency keys
- [x] Contacts, lists, tags, consent history, suppressions, CSV import
- [x] Campaigns: drafts, audience counts, preview, test sends; settings and brand profile
- [x] Rendering: shared MJML section templates via mrml, plain-text part, merge tags
- [x] Signed unsubscribe page and RFC 8058 one-click unsubscribe
- [x] Send queue and worker: scheduling, rate limit, retries, pause/resume/cancel, crash-safe, never sends twice
      (gate test: 5,000 contacts, crash midway, zero duplicates)
- [x] Amazon SES sending, bounce and complaint notifications into suppressions, domain checks

**Phase 1 is complete.**

Phase 2 (web app and v1.0):

- [x] Accounts: first-run setup, login, sessions with CSRF protection, users, password change
- [x] App shell: navigation, read-only campaigns, contacts and lists, light and dark themes
- [x] Editor in the app: shared sections, live preview, image uploads, dark mode, style presets, formatting, autosave
- [x] Everyday screens: contacts, lists, import, campaigns, send flow, reports, settings
- [x] Signup form and double opt-in
- [x] Setup wizard and one-line installer with automatic HTTPS
- [ ] Docs and the stranger test
