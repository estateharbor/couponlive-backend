# Deploy CouponLive (website + backend) on a Hostinger VPS

Everything runs on **one VPS** via Docker Compose:

```
                    Hostinger VPS (Ubuntu + Docker)
   ┌──────────────────────────────────────────────────────────┐
   │  Caddy  :80/:443  (auto-HTTPS)                            │
   │    ├─ couponlive.in        → static site  (/srv/www)      │
   │    ├─ www.couponlive.in    → 301 → apex                   │
   │    └─ api.couponlive.in    → reverse_proxy → api:8000     │
   │  api (FastAPI/uvicorn) ── worker (Celery+beat)            │
   │  db (Postgres)          ── redis                          │
   └──────────────────────────────────────────────────────────┘
```

You need: a Hostinger **VPS (KVM) plan**, the domain `couponlive.in` (DNS
manageable in Hostinger), and ~15 minutes. I can't SSH into your VPS — these are
the commands you run on it.

---

## 1. Provision the VPS

- Hostinger → **VPS** → create a KVM VPS. OS template: **Ubuntu 24.04**, or the
  **"Ubuntu 24.04 with Docker"** template (saves step 3). Note the **VPS IP** and
  root password / SSH key.

## 2. Point DNS at the VPS

In Hostinger DNS for `couponlive.in`, create **A records** all pointing to the
VPS IP:

| Type | Name | Value |
|---|---|---|
| A | `@`   | `<VPS_IP>` |
| A | `www` | `<VPS_IP>` |
| A | `api` | `<VPS_IP>` |

(DNS can take a while to propagate. Caddy needs these live to issue SSL.)

## 3. SSH in and install Docker (skip if you used the Docker template)

```bash
ssh root@<VPS_IP>
curl -fsSL https://get.docker.com | sh
docker compose version   # confirm the compose plugin is present
```

Open the firewall for web + SSH:

```bash
ufw allow 22 && ufw allow 80 && ufw allow 443 && ufw --force enable
```

## 4. Get the backend (this repo) onto the VPS

```bash
git clone https://github.com/estateharbor/couponlive-backend.git
cd couponlive-backend
cp .env.vps.example .env
nano .env      # set a strong POSTGRES_PASSWORD + FEEDBACK_IP_SALT; confirm domains
```

## 5. Build the frontend and drop it in `web/`

The site is a static export served by Caddy from `couponlive-backend/web/`.

**Option A — build on your own machine, copy up** (simplest):
```bash
# in the couponlive-website repo, locally:
echo "NEXT_PUBLIC_API_URL=https://api.couponlive.in" > .env.production
npm ci && npm run build
# copy the built files to the VPS:
scp -r out/* root@<VPS_IP>:/root/couponlive-backend/web/
```

**Option B — build on the VPS** (needs Node 20+):
```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && apt-get install -y nodejs
git clone https://github.com/estateharbor/couponlive-website.git
cd couponlive-website
echo "NEXT_PUBLIC_API_URL=https://api.couponlive.in" > .env.production
npm ci && npm run build
mkdir -p ../couponlive-backend/web && cp -r out/* ../couponlive-backend/web/
cd ../couponlive-backend
```

## 6. Launch the whole stack

```bash
docker compose up -d --build
```

This starts Postgres, Redis, the API (which runs `alembic upgrade head` on boot),
the worker, and Caddy. Caddy automatically provisions HTTPS certificates for
`couponlive.in`, `www.couponlive.in`, and `api.couponlive.in` (needs step 2 DNS
live + ports 80/443 open).

## 7. Verify

```bash
docker compose ps                      # all services "running"/"healthy"
curl -s https://api.couponlive.in/health
```
- `https://couponlive.in` → the site (real coupons via the API).
- `https://www.couponlive.in` → redirects to apex.
- `https://api.couponlive.in/health` → `{"status":"ok",...}`.

Seed a first scrape (optional, immediate data):
```bash
docker compose exec worker python -c "from scheduler.tasks import scrape_source; scrape_source('Desidime')"
```
(Otherwise the beat schedule scrapes on its interval.)

---

## Updating

**Backend code:**
```bash
cd couponlive-backend && git pull && docker compose up -d --build
```
**Frontend:** rebuild (`npm run build`) and re-copy `out/*` into `web/` — Caddy
serves the new files immediately (no restart needed).

## Turning on checkout validation (Playwright) later

`VALIDATION_ENABLED=false` by default, so the worker needs no browser. When you
enable it, the worker needs Chromium + OS libs. Add a `Dockerfile.worker`:

```dockerfile
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
```
Point the `worker` service at it in `docker-compose.yml`
(`build: { context: ., dockerfile: Dockerfile.worker }`), set
`VALIDATION_ENABLED=true`, and **tune the merchant selectors** (see
`docs/validation.md`) before running it against live merchants.

## Backups (recommended)

Postgres data lives in the `pgdata` volume. Periodic dump:
```bash
docker compose exec db pg_dump -U couponlive couponlive > backup_$(date +%F).sql
```

## Troubleshooting

- `docker compose logs -f caddy` — SSL issuance problems (usually DNS not
  propagated yet, or 80/443 blocked).
- `docker compose logs -f api` — migration/startup errors.
- Browser console shows CORS errors → confirm `CORS_ORIGINS` in `.env` includes
  your exact site origin, then `docker compose up -d` to reload.
