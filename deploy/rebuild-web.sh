#!/usr/bin/env bash
# Rebuild the CouponLive static frontend and publish it, so the coupon/deal
# content baked into the HTML (for SEO + AI crawlers) stays fresh. Safe to run
# from cron. Keeps a persistent checkout and pulls, so it's fast after the first
# run. Publishes with an atomic swap so the live site never serves a half-copy.
#
#   Manual run:  bash /opt/couponlive/backend/deploy/rebuild-web.sh
#   Cron (6h):   0 */6 * * * bash /opt/couponlive/backend/deploy/rebuild-web.sh >> /var/log/couponlive-web-rebuild.log 2>&1
set -euo pipefail

REPO="${WEB_REPO:-https://github.com/estateharbor/couponlive-website.git}"
SRC="${WEB_SRC:-/opt/couponlive/couponlive-website}"
WEB="${WEB_ROOT:-/opt/couponlive/backend/web}"
API="${NEXT_PUBLIC_API_URL:-https://api.couponlive.in}"

# Cron has a minimal PATH — make sure node/npm (incl. nvm installs) are found.
for d in /usr/local/bin /usr/bin /snap/bin "$HOME/.nvm/versions/node"/*/bin; do
  [ -d "$d" ] && case ":$PATH:" in *":$d:"*) ;; *) PATH="$d:$PATH" ;; esac
done
export PATH

log() { echo "[rebuild-web $(date -u +%FT%TZ)] $*"; }

command -v npm >/dev/null 2>&1 || { log "ERROR: npm not found on PATH ($PATH)"; exit 1; }

if [ -d "$SRC/.git" ]; then
  log "updating $SRC"
  git -C "$SRC" fetch --quiet origin
  git -C "$SRC" reset --hard --quiet origin/main
else
  log "cloning $REPO -> $SRC"
  rm -rf "$SRC"
  git clone --quiet "$REPO" "$SRC"
fi

cd "$SRC"
echo "NEXT_PUBLIC_API_URL=$API" > .env.production
log "npm install"
npm install --no-audit --no-fund --silent
log "npm run build"
npm run build

log "publishing to $WEB (atomic swap)"
mkdir -p "$(dirname "$WEB")"
rm -rf "$WEB.new"
cp -r out "$WEB.new"
rm -rf "$WEB.prev"
[ -d "$WEB" ] && mv "$WEB" "$WEB.prev"
mv "$WEB.new" "$WEB"
rm -rf "$WEB.prev"
log "done — $(find "$WEB" -name '*.html' | wc -l) HTML pages published"
