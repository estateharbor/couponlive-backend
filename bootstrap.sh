#!/usr/bin/env bash
# =====================================================================
# CouponLive — one-command VPS installer.
# Run as root on a FRESH Ubuntu VPS (e.g. Hostinger's Browser Terminal):
#
#   curl -fsSL https://raw.githubusercontent.com/estateharbor/couponlive-backend/main/bootstrap.sh | bash
#
# It installs Docker + Node, fetches both repos, builds the website,
# generates secure secrets, and starts the whole stack. Safe to re-run
# (it updates code and rebuilds).
# =====================================================================
set -euo pipefail

SITE_DOMAIN="${SITE_DOMAIN:-couponlive.in}"
API_DOMAIN="${API_DOMAIN:-api.$SITE_DOMAIN}"
WORKDIR="/opt/couponlive"
BACKEND_REPO="https://github.com/estateharbor/couponlive-backend.git"
WEBSITE_REPO="https://github.com/estateharbor/couponlive-website.git"

say() { echo; echo "==> $*"; }

say "1/6 Installing Docker (if needed)…"
command -v docker >/dev/null 2>&1 || curl -fsSL https://get.docker.com | sh

say "2/6 Installing Node.js 20 (to build the website)…"
if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi

say "3/6 Fetching the code…"
mkdir -p "$WORKDIR" && cd "$WORKDIR"
clone_or_pull() { # $1=repo $2=dir
  if [ -d "$2/.git" ]; then (cd "$2" && git pull --ff-only); else
    git clone "$1" "$2" 2>/dev/null || {
      echo "!! Could not clone $1"
      echo "   If the repo is PRIVATE, either make it Public on GitHub"
      echo "   (Settings → General → Change visibility — the code has no secrets),"
      echo "   or re-run with a token:  GITHUB_TOKEN=ghp_xxx bash bootstrap.sh"
      exit 1
    }
  fi
}
AUTH=""; [ -n "${GITHUB_TOKEN:-}" ] && AUTH="https://${GITHUB_TOKEN}@github.com/"
if [ -n "$AUTH" ]; then
  BACKEND_REPO="${BACKEND_REPO/https:\/\/github.com\//$AUTH}"
  WEBSITE_REPO="${WEBSITE_REPO/https:\/\/github.com\//$AUTH}"
fi
clone_or_pull "$BACKEND_REPO" backend
clone_or_pull "$WEBSITE_REPO" website

say "4/6 Building the website (this takes a few minutes)…"
cd "$WORKDIR/website"
echo "NEXT_PUBLIC_API_URL=https://$API_DOMAIN" > .env.production
npm ci
npm run build
mkdir -p "$WORKDIR/backend/web"
rm -rf "$WORKDIR/backend/web"/* 2>/dev/null || true
cp -r out/* "$WORKDIR/backend/web/"

say "5/6 Configuring the backend (generating secrets)…"
cd "$WORKDIR/backend"
if [ ! -f .env ]; then
  cat > .env <<EOF
POSTGRES_USER=couponlive
POSTGRES_PASSWORD=$(openssl rand -hex 24)
POSTGRES_DB=couponlive
COUPONLIVE_ENV=prod
SCRAPE_FREQUENCY_MULTIPLIER=1.0
VALIDATION_ENABLED=false
FEEDBACK_IP_SALT=$(openssl rand -hex 16)
CORS_ORIGINS=https://$SITE_DOMAIN,https://www.$SITE_DOMAIN
SITE_DOMAIN=$SITE_DOMAIN
API_DOMAIN=$API_DOMAIN
WEB_ROOT=./web
EOF
  echo "   wrote .env with a random database password."
else
  echo "   .env already exists — keeping it."
fi

# Open the firewall for web + SSH (ignore errors if ufw absent).
if command -v ufw >/dev/null 2>&1; then
  ufw allow 22 >/dev/null 2>&1 || true
  ufw allow 80 >/dev/null 2>&1 || true
  ufw allow 443 >/dev/null 2>&1 || true
  yes | ufw enable >/dev/null 2>&1 || true
fi

say "6/6 Launching the stack…"
docker compose up -d --build

cat <<EOF

======================================================================
 ✅ CouponLive is starting up.

   Website:  https://$SITE_DOMAIN
   API:      https://$API_DOMAIN/health

 HTTPS certificates are issued automatically, but ONLY once your DNS
 A-records for  @ , www , api  point to THIS server's IP address.

 Handy commands (run from  $WORKDIR/backend ):
   docker compose ps            # are all services healthy?
   docker compose logs -f caddy # watch SSL certificate issuance
   docker compose logs -f api   # watch the API
======================================================================
EOF
