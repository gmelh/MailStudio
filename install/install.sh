#!/usr/bin/env bash
# Mail Studio installer: https://mailstudio.email
#
#   curl -fsSL https://get.mailstudio.email | sudo bash
#
# On a fresh Ubuntu or Debian server, this:
#   1. installs Docker (if it isn't there),
#   2. checks your domain points at this server,
#   3. sets up Mail Studio with Caddy, which gets and renews the HTTPS certificate,
#   4. adds a `mailstudio` command for updates, backups and logs, and a daily backup.
#
# Running it again is safe: it keeps your data and settings and updates the setup.
#
# Options (all optional; you're asked for anything missing):
#   --domain mail.example.com   the address Mail Studio will live at
#   --email you@example.com     for certificate expiry notices from Let's Encrypt
#   --dir /opt/mailstudio       where it's installed
#   --image IMAGE               the Mail Studio container image to run
#   --yes                       don't ask questions (needs --domain)
#   --skip-dns-check            don't check the domain points here
#   --dry-run                   write the files, but don't install or start anything

set -euo pipefail

DEFAULT_IMAGE="ghcr.io/mailstudio/mailstudio:1"
DEFAULT_DIR="/opt/mailstudio"
APP_UID=10001  # the container's mailstudio user

# Everything runs from main(), at the very end, so a half-downloaded script does nothing.

bold=""; dim=""; red=""; green=""; reset=""
if [ -t 1 ]; then
  bold=$'\e[1m'; dim=$'\e[2m'; red=$'\e[31m'; green=$'\e[32m'; reset=$'\e[0m'
fi

say()  { printf '%s\n' "$*"; }
step() { printf '\n%s==> %s%s\n' "$bold" "$*" "$reset"; }
ok()   { printf '%s✓%s %s\n' "$green" "$reset" "$*"; }
warn() { printf '%s!%s %s\n' "$red" "$reset" "$*" >&2; }
die()  { printf '\n%sError:%s %s\n' "$red" "$reset" "$*" >&2; exit 1; }

# Questions read from the terminal, because stdin is this script when piped from curl.
has_tty() { [ -r /dev/tty ] && [ -w /dev/tty ] && { : < /dev/tty; } 2>/dev/null; }
ask() {
  local prompt=$1 default=${2:-} answer
  if [ -n "$default" ]; then prompt="$prompt [$default]"; fi
  printf '%s: ' "$prompt" > /dev/tty
  IFS= read -r answer < /dev/tty || answer=""
  printf '%s' "${answer:-$default}"
}

valid_domain() {
  [[ $1 =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]]
}

usage() { sed -n '2,24p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//'; }

# ---------------------------------------------------------------------------

parse_args() {
  DOMAIN=""; ACME_EMAIL=""; DIR="$DEFAULT_DIR"; IMAGE=""; YES=0; DRY_RUN=0; SKIP_DNS=0
  while [ $# -gt 0 ]; do
    case $1 in
      --domain) DOMAIN=${2:-}; shift 2 ;;
      --email) ACME_EMAIL=${2:-}; shift 2 ;;
      --dir) DIR=${2:-}; shift 2 ;;
      --image) IMAGE=${2:-}; shift 2 ;;
      --yes|-y) YES=1; shift ;;
      --skip-dns-check) SKIP_DNS=1; shift ;;
      --dry-run) DRY_RUN=1; shift ;;
      --help|-h) usage; exit 0 ;;
      *) die "Unknown option: $1 (try --help)" ;;
    esac
  done
  DOMAIN=$(printf '%s' "$DOMAIN" | tr '[:upper:]' '[:lower:]' | sed 's|^https\{0,1\}://||; s|/.*$||')
}

load_existing() {
  # Re-running: keep what was chosen last time unless told otherwise.
  if [ -f "$DIR/.env" ]; then
    local key value
    while IFS='=' read -r key value; do
      case $key in
        MAILSTUDIO_DOMAIN) [ -n "$DOMAIN" ] || DOMAIN=$value ;;
        ACME_EMAIL) [ -n "$ACME_EMAIL" ] || ACME_EMAIL=$value ;;
        MAILSTUDIO_IMAGE) [ -n "$IMAGE" ] || IMAGE=$value ;;
      esac
    done < "$DIR/.env"
    EXISTING=1
  else
    EXISTING=0
  fi
  [ -n "$IMAGE" ] || IMAGE=$DEFAULT_IMAGE
}

check_system() {
  step "Checking this server"
  [ "$(uname -s)" = "Linux" ] || die "Mail Studio installs on Linux servers."
  if [ "$DRY_RUN" -eq 0 ] && [ "$(id -u)" -ne 0 ]; then
    die "Run it as root: curl -fsSL https://get.mailstudio.email | sudo bash"
  fi
  case $(uname -m) in
    x86_64|amd64|aarch64|arm64) ;;
    *) die "This processor ($(uname -m)) isn't supported. Use a 64-bit Intel/AMD or ARM server." ;;
  esac
  local memory_mb disk_mb
  memory_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0)
  if [ "$memory_mb" -gt 0 ] && [ "$memory_mb" -lt 900 ]; then
    warn "This server has ${memory_mb} MB of memory. 1 GB or more is recommended."
  fi
  disk_mb=$(df -Pm "$(dirname "$DIR")" 2>/dev/null | awk 'NR==2 {print $4}')
  if [ -n "$disk_mb" ] && [ "$disk_mb" -lt 2048 ]; then
    warn "Only ${disk_mb} MB of disk space is free. 2 GB or more is recommended."
  fi
  # shellcheck disable=SC1091
  ok "$(. /etc/os-release 2>/dev/null && printf '%s' "${PRETTY_NAME:-Linux}"), $(uname -m), ${memory_mb} MB memory"
}

choose_domain() {
  step "Your Mail Studio address"
  if [ -z "$DOMAIN" ]; then
    if [ "$YES" -eq 1 ] || ! has_tty; then die "Give the address with --domain, e.g. --domain mail.yourbusiness.com"; fi
    say "Mail Studio needs its own address, like ${bold}mail.yourbusiness.com${reset}."
    say "In your domain's DNS settings, add an ${bold}A record${reset} for it pointing to this server's IP address."
    while :; do
      DOMAIN=$(ask "Address" | tr '[:upper:]' '[:lower:]' | sed 's|^https\{0,1\}://||; s|/.*$||')
      valid_domain "$DOMAIN" && break
      warn "That doesn't look like a domain name. Try something like mail.yourbusiness.com"
    done
  fi
  valid_domain "$DOMAIN" || die "\"$DOMAIN\" isn't a valid domain name."
  if [ -z "$ACME_EMAIL" ] && [ "$YES" -eq 0 ] && has_tty; then
    ACME_EMAIL=$(ask "Your email, for certificate notices (optional)" "")
  fi
  ok "https://$DOMAIN"
}

install_docker() {
  step "Docker"
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    ok "Docker $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo installed) is ready"
    return
  fi
  if [ "$DRY_RUN" -eq 1 ]; then say "${dim}(dry run: would install Docker from get.docker.com)${reset}"; return; fi
  command -v curl >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y -qq curl; }
  say "Installing Docker (a minute or two)..."
  curl -fsSL https://get.docker.com | sh >/tmp/mailstudio-docker-install.log 2>&1 \
    || die "Installing Docker failed. Details: /tmp/mailstudio-docker-install.log"
  systemctl enable --now docker >/dev/null 2>&1 || true
  docker compose version >/dev/null 2>&1 || die "Docker installed, but 'docker compose' isn't available."
  ok "Docker installed"
}

public_ip() {
  curl -4 -fsS --max-time 10 https://api.ipify.org 2>/dev/null \
    || curl -4 -fsS --max-time 10 https://ifconfig.me 2>/dev/null \
    || true
}

resolved_ips() {
  getent ahostsv4 "$DOMAIN" 2>/dev/null | awk '{print $1}' | sort -u
}

check_dns() {
  step "Checking $DOMAIN points to this server"
  if [ "$SKIP_DNS" -eq 1 ] || [ "$DRY_RUN" -eq 1 ]; then say "${dim}(skipped)${reset}"; return; fi
  local ip found
  ip=$(public_ip)
  [ -n "$ip" ] || { warn "Couldn't find this server's public IP address; skipping the check."; return; }
  while :; do
    found=$(resolved_ips | tr '\n' ' ')
    if printf ' %s ' "$found" | grep -q " $ip "; then ok "$DOMAIN → $ip"; return; fi
    warn "$DOMAIN points to: ${found:-nothing yet}. It needs to point to this server: $ip"
    say "Add (or fix) an A record for $DOMAIN with the value ${bold}$ip${reset} at your domain's DNS provider."
    say "Changes usually show up within a few minutes, sometimes longer."
    if [ "$YES" -eq 1 ] || ! has_tty; then die "The domain doesn't point here yet. Run the installer again once it does."; fi
    ask "Press Enter to check again (or Ctrl+C to stop)" "" >/dev/null
  done
}

check_ports() {
  [ "$DRY_RUN" -eq 1 ] && return
  command -v ss >/dev/null 2>&1 || return
  local busy
  busy=$(ss -ltnpH '( sport = :80 or sport = :443 )' 2>/dev/null | grep -v docker-proxy || true)
  if [ -n "$busy" ]; then
    warn "Something else is using port 80 or 443:"
    printf '%s\n' "$busy" >&2
    die "Stop that web server (for example: systemctl disable --now nginx apache2), then run the installer again."
  fi
}

open_firewall() {
  [ "$DRY_RUN" -eq 1 ] && return
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw allow 80/tcp >/dev/null && ufw allow 443/tcp >/dev/null && ufw allow 443/udp >/dev/null
    ok "Opened ports 80 and 443 in the firewall"
  fi
}

# ---------------------------------------------------------------------------
# Files

write_env() {
  cat > "$DIR/.env" <<EOF
# Mail Studio settings, read by docker compose. Edit, then run: mailstudio restart
MAILSTUDIO_DOMAIN=$DOMAIN
ACME_EMAIL=$ACME_EMAIL
MAILSTUDIO_IMAGE=$IMAGE
EOF
  chmod 600 "$DIR/.env"
}

write_compose() {
  cat > "$DIR/compose.yml" <<'EOF'
# Written by the Mail Studio installer; re-running it replaces this file.
# Your settings are in .env; your data is in ./data.
name: mailstudio

services:
  app:
    image: ${MAILSTUDIO_IMAGE}
    restart: unless-stopped
    environment:
      MAILSTUDIO_BASE_URL: https://${MAILSTUDIO_DOMAIN}
      # Only Caddy can reach the app, so its forwarded client addresses are trusted.
      MAILSTUDIO_TRUSTED_PROXIES: "*"
    volumes:
      - ./data:/data
    logging:
      driver: local
      options:
        max-size: 10m

  caddy:
    image: caddy:2
    restart: unless-stopped
    depends_on:
      - app
    ports:
      - "80:80"
      - "443:443"
      - "443:443/udp"
    environment:
      MAILSTUDIO_DOMAIN: ${MAILSTUDIO_DOMAIN}
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    logging:
      driver: local
      options:
        max-size: 10m

volumes:
  caddy_data:
  caddy_config:
EOF
}

write_caddyfile() {
  {
    printf '# Written by the Mail Studio installer. Caddy gets and renews the HTTPS certificate.\n'
    if [ -n "$ACME_EMAIL" ]; then printf '{\n\temail %s\n}\n\n' "$ACME_EMAIL"; fi
    cat <<'EOF'
{$MAILSTUDIO_DOMAIN} {
	encode zstd gzip
	header Strict-Transport-Security "max-age=31536000"
	request_body {
		max_size 25MB
	}
	reverse_proxy app:8080
}
EOF
  } > "$DIR/Caddyfile"
}

write_files() {
  step "Writing the setup to $DIR"
  mkdir -p "$DIR/data" "$DIR/backups"
  write_env
  write_compose
  write_caddyfile
  if [ "$DRY_RUN" -eq 0 ]; then chown -R "$APP_UID:$APP_UID" "$DIR/data"; fi
  chmod 700 "$DIR/backups"
  ok "compose.yml, Caddyfile and .env"
}

install_command() {
  local bin=/usr/local/bin/mailstudio cron=/etc/cron.daily/mailstudio-backup
  if [ "$DRY_RUN" -eq 1 ]; then bin="$DIR/mailstudio"; cron="$DIR/mailstudio-backup.cron"; fi
  write_ctl > "$bin"
  chmod 755 "$bin"
  printf '#!/bin/sh\n# Daily Mail Studio backup, keeping two weeks.\n%s backup --keep 14 --quiet\n' "$bin" > "$cron"
  chmod 755 "$cron"
  ok "Added the 'mailstudio' command and a daily backup"
}

# The `mailstudio` command installed on the server.
write_ctl() {
  printf '#!/usr/bin/env bash\nMAILSTUDIO_DIR=%q\n' "$DIR"
  cat <<'EOF'
# Manage Mail Studio. Installed by the Mail Studio installer.
set -euo pipefail
cd "$MAILSTUDIO_DIR"
compose() { docker compose --project-directory "$MAILSTUDIO_DIR" "$@"; }
domain() { sed -n 's/^MAILSTUDIO_DOMAIN=//p' .env; }

usage() {
  cat <<USAGE
Usage: mailstudio COMMAND

  status                     Is it running? Shows the address and version.
  logs [app|caddy]           Follow the logs (Ctrl+C to stop).
  update                     Get the latest version and restart. Your data is backed up first.
  backup [--keep N]          Back up the database and images to $MAILSTUDIO_DIR/backups.
  restore FILE               Replace everything with a backup (asks first).
  restart | stop | start
  reset-password EMAIL       Set a new password for someone who's locked out.
  create-api-key NAME        Create an API key (scopes: read write send).
  version
USAGE
}

backup() {
  local keep=0 quiet=0 stamp file
  while [ $# -gt 0 ]; do
    case $1 in --keep) keep=$2; shift 2 ;; --quiet) quiet=1; shift ;; *) shift ;; esac
  done
  stamp=$(date +%Y-%m-%d-%H%M%S)
  compose exec -T app mailstudio backup "/data/backups/snapshot-$stamp.db" >/dev/null
  file="backups/mailstudio-$stamp.tar.gz"
  local parts=("backups/snapshot-$stamp.db")
  if [ -d data/assets ]; then parts+=(assets); fi
  tar -czf "$file" -C data "${parts[@]}"
  rm -f "data/backups/snapshot-$stamp.db"
  chmod 600 "$file"
  if [ "$keep" -gt 0 ]; then
    find backups -maxdepth 1 -name 'mailstudio-*.tar.gz' | sort -r | tail -n +"$((keep + 1))" | xargs -r rm -f
  fi
  [ "$quiet" -eq 1 ] || echo "Backed up to $MAILSTUDIO_DIR/$file"
}

restore() {
  local file=${1:-} work answer
  [ -f "$file" ] || { echo "Usage: mailstudio restore backups/mailstudio-....tar.gz" >&2; exit 1; }
  printf 'This replaces ALL current Mail Studio data with %s. A backup of the current data is made first.\nType "restore" to continue: ' "$file"
  read -r answer
  [ "$answer" = "restore" ] || { echo "Cancelled."; exit 1; }
  backup
  work=$(mktemp -d)
  tar -xzf "$file" -C "$work"
  compose stop app
  rm -f data/mailstudio.db data/mailstudio.db-wal data/mailstudio.db-shm
  cp "$work"/backups/snapshot-*.db data/mailstudio.db
  if [ -d "$work/assets" ]; then rm -rf data/assets && cp -r "$work/assets" data/assets; fi
  chown -R 10001:10001 data
  rm -rf "$work"
  compose start app
  echo "Restored. Open https://$(domain)"
}

case ${1:-help} in
  status)
    compose ps
    printf '\nAddress: https://%s\n' "$(domain)"
    curl -fsS --max-time 5 "https://$(domain)/api/v1/health" && echo || echo "Not answering at https://$(domain) yet."
    ;;
  logs) shift; compose logs -f --tail 200 "$@" ;;
  update)
    backup
    compose pull --ignore-pull-failures  # an image built on this server has nothing to pull
    compose up -d --remove-orphans
    docker image prune -f >/dev/null
    echo "Updated. The database is migrated automatically on start."
    ;;
  backup) shift; backup "$@" ;;
  restore) shift; restore "$@" ;;
  restart) compose restart ;;
  stop) compose stop ;;
  start) compose up -d ;;
  reset-password) compose exec -T app mailstudio reset-password --email "${2:?Usage: mailstudio reset-password EMAIL}" ;;
  create-api-key) compose exec -T app mailstudio create-api-key --name "${2:?Usage: mailstudio create-api-key NAME}" --scopes "read write send" ;;
  version) compose exec -T app python -c "import mailstudio; print(mailstudio.__version__)" ;;
  help|--help|-h) usage ;;
  *) usage; exit 1 ;;
esac
EOF
}

# ---------------------------------------------------------------------------

start() {
  step "Starting Mail Studio"
  if [ "$DRY_RUN" -eq 1 ]; then say "${dim}(dry run: would run docker compose pull and up)${reset}"; return; fi
  (cd "$DIR" && docker compose pull --quiet --ignore-pull-failures && docker compose up -d --remove-orphans) \
    || die "Starting failed. See: cd $DIR && docker compose logs"
  say "Waiting for the HTTPS certificate (usually under a minute)..."
  local i
  for i in $(seq 1 60); do
    if curl -fsS --max-time 5 "https://$DOMAIN/api/v1/health" >/dev/null 2>&1; then
      ok "Running at https://$DOMAIN"
      return
    fi
    sleep 3
    [ $((i % 10)) -eq 0 ] && say "${dim}still waiting...${reset}"
  done
  warn "Mail Studio started, but https://$DOMAIN isn't answering yet."
  say "Check the domain's A record, and that ports 80 and 443 are open in your provider's firewall."
  say "Then see what Caddy says: mailstudio logs caddy"
}

finish() {
  printf '\n%sMail Studio is installed.%s\n\n' "$bold" "$reset"
  if [ "$EXISTING" -eq 1 ]; then
    say "  Open ${bold}https://$DOMAIN${reset} and log in as usual."
  else
    say "  Open ${bold}https://$DOMAIN${reset} to create your account. Do it now:"
    say "  the first person to open it becomes the owner."
  fi
  say ""
  say "  Useful commands:  mailstudio status | logs | update | backup"
  say "  Your data:        $DIR/data   (backed up daily to $DIR/backups)"
  say ""
}

main() {
  parse_args "$@"
  load_existing
  printf '%sMail Studio installer%s\n' "$bold" "$reset"
  check_system
  choose_domain
  install_docker
  check_dns
  check_ports
  open_firewall
  write_files
  install_command
  start
  finish
}

main "$@"
