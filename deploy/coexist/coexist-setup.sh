#!/usr/bin/env bash
# =====================================================================
# CouponLive coexistence installer — puts CouponLive behind the server's
# EXISTING nginx (which keeps serving your other sites untouched).
#
#   curl -fsSL https://raw.githubusercontent.com/estateharbor/couponlive-backend/main/deploy/coexist/coexist-setup.sh -o coexist.sh
#   bash coexist.sh
# =====================================================================
set -euo pipefail

APPDIR=/opt/couponlive/backend
RAW=https://raw.githubusercontent.com/estateharbor/couponlive-backend/main/deploy/coexist

cd "$APPDIR"

echo "==> 1/5 Override: API on 127.0.0.1:8010, Caddy disabled (nginx is the front door)…"
cat > docker-compose.override.yml <<'YML'
services:
  api:
    ports:
      - "127.0.0.1:8010:8000"
  caddy:
    profiles: ["disabled"]
YML

echo "==> 2/5 Recreating the stack behind nginx…"
docker compose rm -sf caddy 2>/dev/null || true
docker compose up -d --remove-orphans
chmod 600 "$APPDIR/.env" 2>/dev/null || true

echo "==> 3/5 Permissions so nginx can read the static site…"
chmod a+x /opt /opt/couponlive "$APPDIR"
chmod -R a+rX "$APPDIR/web"

echo "==> 4/5 Installing the nginx site (couponlive.in + api.couponlive.in)…"
curl -fsSL "$RAW/nginx-couponlive.conf" -o /etc/nginx/sites-available/couponlive.conf
ln -sf /etc/nginx/sites-available/couponlive.conf /etc/nginx/sites-enabled/couponlive.conf
nginx -t                      # fails safely (existing sites keep running) if anything's off
systemctl reload nginx

echo "==> 5/5 HTTPS certificates via certbot…"
EMAIL="${CERTBOT_EMAIL:-estateharbor@gmail.com}"
certbot --nginx \
  -d couponlive.in -d www.couponlive.in -d api.couponlive.in \
  --redirect --agree-tos --non-interactive -m "$EMAIL"

cat <<EOF

======================================================================
 ✅ CouponLive is live — your other sites were not touched.
    Website:  https://couponlive.in
    API:      https://api.couponlive.in/health
 If certbot failed on a domain, it just means DNS isn't pointing here
 yet — wait a bit and re-run:
   certbot --nginx -d couponlive.in -d www.couponlive.in -d api.couponlive.in --redirect
======================================================================
EOF
