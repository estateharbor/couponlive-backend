#!/usr/bin/env bash
# Auto-publish: if GitHub `main` has new commits (e.g. from the scheduled offer
# scout), run the normal update — importing any NEW editorial CSVs — so the
# website reflects them without anyone running a command. Safe to run often.
#
#   Cron (every 15 min):
#   */15 * * * * bash /opt/couponlive/backend/deploy/auto-update.sh >> /var/log/couponlive-auto-update.log 2>&1
set -euo pipefail

cd /opt/couponlive/backend
log() { echo "[auto-update $(date -u +%FT%TZ)] $*"; }

# Never overlap with itself (a deploy can take a few minutes).
exec 8>/tmp/couponlive-auto-update.lock
flock -n 8 || { log "previous run still going — skipping"; exit 0; }

git fetch --quiet origin main
local_rev=$(git rev-parse HEAD)
remote_rev=$(git rev-parse origin/main)
[ "$local_rev" = "$remote_rev" ] && exit 0

# Coupon/deal CSVs added since what's deployed (trial CSVs are picked up by
# seed_trials automatically, so they're excluded here).
mapfile -t new_csvs < <(git diff --name-only --diff-filter=A "$local_rev" "$remote_rev" \
  -- 'data/editorial/*.csv' | grep -v '^data/editorial/trials/' || true)

log "new commits ${local_rev:0:7}..${remote_rev:0:7}; new CSVs: ${new_csvs[*]:-none}"
bash deploy/update.sh "${new_csvs[@]}"
log "done"
