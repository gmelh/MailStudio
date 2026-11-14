# Installing Mail Studio

On a fresh Ubuntu or Debian server (1 GB of memory is plenty):

```sh
curl -fsSL https://get.mailstudio.email | sudo bash
```

Before you run it, point an address at the server: in your domain's DNS settings add an
**A record** for, say, `mail.yourbusiness.com` with the server's IP address. The installer
checks it, installs Docker if needed, starts Mail Studio behind [Caddy](https://caddyserver.com)
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
  compose.yml   the two containers: Mail Studio and Caddy (rewritten by the installer)
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

- `get.mailstudio.email` serves `install/install.sh` as plain text (any static host, or a
  redirect to the raw file in the repository).
- Images are built for amd64 and arm64 by `.github/workflows/release.yml` when a `v*.*.*`
  tag is pushed, or by hand with `scripts/release-image.sh`. The installer runs
  `ghcr.io/mailstudio/mailstudio:1`, so a new 1.x release reaches everyone on `mailstudio update`.
