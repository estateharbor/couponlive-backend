#!/usr/bin/env bash
# One command for every CouponLive update: pull the latest backend, rebuild the
# containers, import any editorial CSVs given as arguments, refresh the curated
# free trials, then rebuild + publish the website so changes show immediately.
#
#   bash /opt/couponlive/backend/deploy/update.sh
#   bash /opt/couponlive/backend/deploy/update.sh data/editorial/<new-file>.csv
set -euo pipefail

cd /opt/couponlive/backend
log() { echo "[update $(date -u +%FT%TZ)] $*"; }

log "pulling backend"
git pull --ff-only
log "rebuilding containers"
docker compose up -d --build

for csv in "$@"; do
  log "importing $csv"
  docker compose exec -T worker python -m scheduler.import_editorial "$csv"
done

log "refreshing free trials"
docker compose exec -T worker python -m scheduler.seed_trials

log "rebuilding website"
bash deploy/rebuild-web.sh
log "all done — site is live with the latest data"
