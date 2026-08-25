# Deploying the CouponLive backend to Railway

One Railway **project** holds four services:

```
┌─────────────┐   ┌─────────────┐
│  Postgres   │   │    Redis    │   (Railway plugins)
└──────┬──────┘   └──────┬──────┘
       │                 │
   ┌───┴─────────────────┴───┐        ┌──────────────────────────┐
   │  API service (web)      │        │  Worker service          │
   │  bash start.sh          │        │  celery … worker --beat  │
   │  → migrations + uvicorn │        │  (scrape + schedule)     │
   └─────────────────────────┘        └──────────────────────────┘
       │ public HTTPS domain
       ▼
   the static frontend calls it
```

Build is automatic via **Nixpacks** (detects `requirements.txt` + `.python-version`).
`Procfile` defines the two process types (`web`, `worker`).

---

## 1. Create the project + API service

1. Railway → **New Project** → **Deploy from GitHub repo** → pick
   `estateharbor/couponlive-backend`. This first service is the **API** — it uses
   the `web` process (`bash start.sh`, which runs `alembic upgrade head` then
   `uvicorn`).

## 2. Add Postgres and Redis

2. In the project: **New → Database → Add PostgreSQL**, then **New → Database →
   Add Redis**. Railway provisions both and exposes `DATABASE_URL` / `REDIS_URL`
   as referenceable variables.

## 3. API service variables

On the **API** service → **Variables**, add:

| Variable | Value |
|---|---|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` |
| `COUPONLIVE_ENV` | `prod` |
| `SCRAPE_FREQUENCY_MULTIPLIER` | `1.0` |
| `VALIDATION_ENABLED` | `false` |
| `CORS_ORIGINS` | `https://couponlive.in,https://www.couponlive.in` |
| `INRDEALS_API_KEY` / `INRDEALS_USERNAME` | *(optional, when you have them)* |

`PORT` is injected by Railway automatically — `start.sh` reads it. The
`postgresql://` URL Railway provides is normalized to the psycopg driver in code,
so paste it as-is.

## 4. Give the API a domain

API service → **Settings → Networking → Generate Domain** → you get
`https://<something>.up.railway.app`. (Optional: set **Healthcheck Path** to
`/health`.) Test it: `https://<domain>/health` should return `{"status":"ok",…}`.

## 5. Add the Worker service (same repo)

5. **New → GitHub Repo → `estateharbor/couponlive-backend`** again → a second
   service. In its **Settings → Deploy → Custom Start Command**:

   ```
   celery -A scheduler.celery_app worker --beat --loglevel=info --concurrency=2
   ```

   Give it the **same** `DATABASE_URL`, `REDIS_URL`, `COUPONLIVE_ENV=prod`,
   `VALIDATION_ENABLED=false` variables. It needs **no** public domain.

## 6. Point the frontend at it

Rebuild the website with `NEXT_PUBLIC_API_URL=https://<api-domain>` (see the
website repo's `DEPLOY_HOSTINGER.md`) and re-upload. CORS is already handled by
step 3.

---

## Playwright validators (later)

`VALIDATION_ENABLED=false` for the initial deploy, so **no browser is needed** —
the worker runs scraping + scheduling only, and the `playwright` *package*
installs fine without the Chromium binary.

When you turn on checkout validation, the **worker** needs the Chromium binary +
OS libraries. Switch that service to a Dockerfile build:

```dockerfile
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["celery", "-A", "scheduler.celery_app", "worker", "--beat", "--loglevel=info"]
```

Set the worker service to **Builder: Dockerfile**, then set `VALIDATION_ENABLED=true`.
(That image ships the browsers and their system deps, so no `playwright install`
step is required.)

## Notes

- Migrations run on every API deploy (`start.sh` → `alembic upgrade head`).
- Costs: Postgres + Redis + two always-on services use Railway resource hours —
  check your plan. The worker can be scaled to zero if you only scrape on a
  schedule you trigger manually.
