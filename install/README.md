# Installing MailStudio

On a fresh Ubuntu or Debian server (1 GB of memory is plenty):

```sh
curl -fsSL https://get.mailstudio.email | sudo bash
```

Before you run it, point an address at the server: in your domain's DNS settings add an
**A record** for, say, `mail.yourbusiness.com` with the server's IP address. The installer
checks it, installs Docker if needed, starts MailStudio behind [Caddy](https://caddyserver.com)
(which gets and renews the HTTPS certificate), and tells you where to create your account.

Then open the address and follow **Get set up** in the app.

## Day to day

| Command | What it does |
| --- | --- |
| `mailstudio status` | Is it running? |
| `mailstudio update` | Back up, get the latest version, restart. The database is migrated automatically. |
| `mailstudio backup` | Back up the database and images to `/opt/mailstudio/backups` (also runs daily; two weeks are kept) |
| `mailstudio restore FILE` | Put a backup back (asks first, and backs up the current data before replacing it) |
| `mailstudio logs [app\|caddy]` | Follow the logs |
| `mailstudio reset-password EMAIL` | For someone who's locked out |
| `mailstudio create-api-key NAME` | A key for the API |

Copy `/opt/mailstudio/backups` off the server now and then (or turn on your hosting
provider's snapshots): a backup on the same server doesn't help if the server is lost.

## What's where

```
/opt/mailstudio/
  .env          your address and the image version (edit, then `mailstudio restart`)
  compose.yml   the two containers: MailStudio and Caddy (rewritten by the installer)
  Caddyfile     HTTPS settings (rewritten by the installer)
  data/         the database and uploaded images
  backups/      daily backups
```

## Options

```sh
curl -fsSL https://get.mailstudio.email | sudo bash -s -- --domain mail.example.com --email you@example.com --yes
```

`--domain`, `--email` (for certificate notices), `--dir`, `--image`, `--yes` (no questions),
`--skip-dns-check`, `--dry-run` (write the files only). Running the installer again keeps
your data and settings.

## For maintainers

### Releasing

```sh
# 1. Set the version in server/pyproject.toml, server/mailstudio/__init__.py and web/package.json
git commit -am "Version 0.8.0" && git push
# 2. Tag it; pushing the tag starts .github/workflows/release.yml
git tag -a v0.8.0 -m "MailStudio 0.8.0"
git push origin v0.8.0
```

The release workflow runs all the checks (`ci.yml`), builds the image for amd64 and arm64,
pushes it to `ghcr.io/gmelh/mailstudio` (tags `0.8.0`, `0.8`, `latest`, and from 1.0 on also
the major version, `1`), then creates a GitHub release with `install.sh` and its SHA-256
checksum attached. A tag with a suffix, like `v1.0.0-rc.1`, becomes a pre-release and never
moves `latest`. The first time, make the package public: GitHub → Packages → mailstudio →
Package settings → Change visibility.

The installer runs `:latest` until 1.0. At 1.0, change `DEFAULT_IMAGE` in `install.sh` to `:1`.
Existing installs keep the image named in their `/opt/mailstudio/.env`.

If a release build fails before anyone has installed it, move the tag to the fixed commit:
`git tag -d v0.8.0 && git push origin :refs/tags/v0.8.0`, then tag and push again.

### get.mailstudio.email

The subdomain serves `install/install.sh` as plain text. Two ways, both in
[`hosting/`](hosting/), for any Apache or LiteSpeed host such as Hostinger (rename the
file to `.htaccess` in the subdomain's folder):

- **`htaccess-hosted`**: upload `install.sh` next to it, and upload it again for each
  release. Works before any GitHub release exists.
- **`htaccess-redirect`**: nothing else to upload: it redirects to `install.sh` in the
  newest GitHub release, so each release reaches it automatically.

Uploading from Windows: the repository's `.gitattributes` keeps Linux line endings, so the
file in your working copy is fine. Check after uploading:

```sh
curl -sI https://get.mailstudio.email                       # 200 (hosted) or 302 (redirect)
curl -fsSL https://get.mailstudio.email | grep -c $'\r'     # must print 0
curl -fsSL https://get.mailstudio.email | bash -s -- --dry-run --dir /tmp/ms-test --domain mail.example.com
```

Whoever controls the subdomain controls what runs as root on every new install, so keep
that hosting account locked down (two-factor login, WordPress and plugins updated). Each
GitHub release lists the checksum, so people can verify the script independently.
