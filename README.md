# CouponLive

A coupon aggregation platform whose differentiator is **live validation**:
every code served to a user has been verified as actually working recently,
not merely scraped and displayed.

> **Build status:** All six phases complete — **46 tests passing**. Data model,
> ingestion pipeline, two sources (**Desidime** HTML metadata, **INRDeals**
> affiliate API with real codes), the **validation layer** (Playwright checkout
> validators for 5 merchants, priority queue, retry/backoff, proxy hook), the
> **FastAPI service** (`/coupons`, `/merchants`, feedback, `/health`), and
> **ops** (structured logging, stale-expiry job, zero-result/low-success
> alerting). See [docs/affiliate_ingestion.md](docs/affiliate_ingestion.md) and
> [docs/validation.md](docs/validation.md).

## API

| Endpoint | Purpose |
|---|---|
| `GET /coupons?merchant=X&status=valid` | Default = valid **and** validated within `SERVE_FRESHNESS_HOURS`, ordered by confidence then recency. `include_stale=true` bypasses freshness. |
| `GET /merchants` | Merchants with total + fresh-valid coupon counts. |
| `POST /coupons/{id}/feedback` | Crowd worked/didn't-work; recomputes confidence; one counted vote per IP-hash per 24h. |
| `GET /health` | Per-source last-success/rate + totals; `status: degraded` when a source is stale or below the success-rate threshold. |

Ops jobs (Celery beat, [scheduler/tasks.py](scheduler/tasks.py)): `scrape_source`,
`validate_coupon` (priority queue), `enqueue_revalidations`, `expire_stale_coupons`,
`check_source_health`. Alerting is log-based ([core/alerting.py](core/alerting.py)),
POSTing to `ALERT_WEBHOOK_URL` when set.

### Sources

| Source | Mechanism | Yields codes? | Notes |
|---|---|---|---|
| Desidime | `requests`+BS4 | No (reveal-gated) | Offer metadata; robots-clean |
| INRDeals | affiliate API | **Yes** | Needs `INRDEALS_API_KEY` + `INRDEALS_USERNAME` in `.env` |
| GrabOn | — | — | ⛔ robots disallows coupon paths — do not scrape |
| PaisaWapas / CouponDunia | — | — | Cloudflare/captcha — use affiliate API |

## Architecture (target)

```
                 ┌───────────────┐        ┌──────────────────┐
 Affiliate APIs ─┤               │        │                  │
 (preferred)     │  Ingestion    │─raw──▶ │  Normalize/Dedup │──▶ Postgres
 Scrapers ───────┤  (scrapers/)  │        │  (merchant+code) │      (coupons,
 (fallback)      └───────────────┘        └──────────────────┘       provenance)
                                                                        │
                         ┌──────────────────────────────────┐          │
                         │  Validators (validators/)         │◀─priority queue
                         │  → VALID / INVALID / UNVERIFIABLE  │──updates confidence
                         └──────────────────────────────────┘          │
                                                                        ▼
                                                          FastAPI (api/) serves only
                                                          fresh, validated coupons
```

- **scrapers/** — one module per source, common `BaseScraper` → `list[RawCoupon]`.
- **validators/** — one module per merchant, common `BaseValidator`.
- **models/** — SQLAlchemy models, enums, Pydantic schemas.
- **api/** — FastAPI service.
- **scheduler/** — Celery app; tasks + beat schedule.
- **core/** — config, structured logging, retry/backoff, proxy hook.
- **alembic/** — migrations.

## Data model (Phase 1)

| Table | Purpose |
|---|---|
| `merchants` | Normalized merchant identity + checkout URL pattern (for validation). |
| `sources` | Aggregator sites / affiliate APIs; `ingestion_method`, cadence, health. |
| `coupons` | One row per `(merchant, code)`; status, confidence, last-validated. |
| `coupon_sources` | M:N provenance — which sources listed a coupon, and when. |
| `validation_logs` | Append-only audit of every validation attempt. |
| `user_feedback` | Crowdsourced worked/didn't-work, `ip_hash` for abuse throttling. |

**Hot-path indexes:** unique `(merchant_id, code)`, `status`,
`last_validated_at`, and a composite `ix_coupon_serve` covering the default API
query (fresh + valid, per merchant, ordered by confidence then recency).

## Setup

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # then edit DATABASE_URL / REDIS_URL
alembic upgrade head            # create the schema
pytest -q                       # schema coherence tests (no Postgres needed)
uvicorn api.main:app --reload   # boots the API stub at /health
```

`pytest` runs against in-memory SQLite, so it validates the models without a
database. `alembic upgrade head` needs a real Postgres (local, Supabase, or
Neon) pointed at by `DATABASE_URL`.

## Dev vs prod

Behaviour is env-driven (see `.env.example`): dev runs slow and limited
(`SCRAPE_FREQUENCY_MULTIPLIER`, `SCRAPE_MAX_SOURCES_PER_RUN`) and keeps
`VALIDATION_ENABLED=false`; prod uses full cadence. No `if ENV=="prod"` logic is
scattered through the code — it all flows from `core/config.py`.

## Deploy (Railway / Render)

One project, three-plus services sharing env config:
- **api** — `uvicorn api.main:app`
- **worker** — `celery -A scheduler.celery_app worker` (+ a `beat` process)
- **Postgres** and **Redis** managed add-ons.

## Adding a new scraper (Phase 2+)

1. Create `scrapers/<source>.py` with a class subclassing `BaseScraper`, set
   `source_name` / `ingestion_method`, implement `scrape() -> list[RawCoupon]`.
2. Add a **selector health-check test** in `tests/` that runs the live scraper
   and asserts `>0` results with valid-looking fields. This is how we catch a
   source's redesign breaking us immediately rather than silently.
3. Register the source row (`sources` table) and its schedule entry.

## Adding a new validator (Phase 4+)

1. Create `validators/<merchant>.py` subclassing `BaseValidator`, implement
   `validate(code, merchant) -> ValidationResult`.
2. Prefer the lightest reliable signal (merchant coupon endpoint / affiliate
   status) over full checkout automation — see the design note in
   `validators/base.py`.

## Open decisions flagged for the owner

- **Ingestion strategy:** prefer affiliate-network APIs (Cuelinks, Admitad,
  INRDeals, EarnKaro, vCommission) over scraping where available — fresher,
  structured, authorized. Scraping is the fallback. (Being researched per source.)
- **Validation mechanism & proxy rotation:** full headless checkout automation
  against major retailers, and proxy rotation to defeat bot protection, carry
  ToS/legal risk and are **not** defaulted in. To be decided explicitly before
  Phase 4 implementation.
```
