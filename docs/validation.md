# Validation layer (Phase 4)

Every code served by the API is meant to have been *verified as working
recently*. The validator drives a real headless browser through a merchant's
cart, applies the code, and reads whether a discount was applied or an error
shown — returning **VALID / INVALID / UNVERIFIABLE**.

## How it works

- `validators/browser.py` — `BrowserValidator` harness (Playwright). A
  per-merchant `MerchantConfig` supplies selectors + a representative product +
  success/error signals, so each merchant is a small config, not a rewrite.
- `validators/merchants/*.py` — one isolated module per merchant.
- `scheduler/validation.py` — priority selection + result recording:
  - **priority 0** newly-ingested codes (validate first),
  - **priority 1** top-merchant valid codes gone stale (re-validate every
    `VALIDATE_TOP_MERCHANT_REVALIDATE_HOURS`),
  - **priority 2** other stale codes.
- `scheduler/tasks.py` — Celery tasks + beat schedule (Redis broker priorities).
- `core/confidence.py` — blends the latest validation outcome with crowd
  feedback into `confidence_score`; recency is applied at query time by the API.

The harness is **proven end-to-end** against a local controlled checkout fixture
(`tests/fixtures/checkout.html`, run via `pytest -m browser`) — it correctly
classifies a good vs. bad code without touching any live site.

## ⚠️ Risk register — read before running against live merchants

Automated checkout actions trip bot detection and touch each merchant's ToS.
The code is built; **running it live is an operational decision** that must
happen in an authorized environment, at low volume, on the re-validation cadence
(never tight loops). Selectors are realistic starting points and **must be tuned
against a live authenticated session** — most coupon fields require login and a
non-empty cart.

| Merchant | Risk | Notes |
|---|---|---|
| **Amazon India** | 🔴 **High** | Aggressive anti-automation + strict ToS. Least advisable target; prefer authorized promo programs/APIs. |
| **Myntra** | 🟠 Medium-high | Flipkart-group bot detection; coupon field behind login. |
| **Flipkart** | 🟠 Medium-high | Login + non-empty cart; many offers auto-apply (no manual field → UNVERIFIABLE). |
| **AJIO** | 🟡 Medium | Coupon field on authenticated bag page. |
| **Nykaa** | 🟡 Medium | Coupon field on cart; login may be required. |

## Deliberately NOT built (evasion boundary)

The proxy/session-rotation **hook** (`core/proxy.py`) is a plug point that is a
no-op by default. Detection-*evasion* machinery — stealth fingerprinting,
CAPTCHA solving, residential-proxy rotation engineered to defeat bot protection
— is intentionally **not** implemented. Wiring a real proxy provider, and any
decision to pursue evasion, is an explicit separate decision, not a default.

A lower-risk alternative for much of this: validate via the **affiliate feed's**
own coupon status/expiry (INRDeals returns codes with metadata) and crowd
feedback, reserving browser validation for spot-checks on top merchants.

## One honest bar for "valid" (no source-specific trust)

A coupon is `valid` **only after our validation worker checkout-tests it** — the
same bar for every source, affiliate feeds included. Feed coupons land
`unverified` like any scraped coupon and enter the real validation queue; they do
**not** get a "trusted-feed" status or a static confidence. (An earlier
`_apply_affiliate_trust` shortcut that marked affiliate coupons valid at 0.7 was
removed — it showed untested codes under the same "✓ Verified" badge as tested
ones, which misleads users.) Confidence is always computed from validation
results + crowd feedback (`core/confidence.py`), never a per-source constant.

The one immediate status change we allow is the **negative** signal: a supplier
`suspended` offer → `status=expired` right away (see `expire_suspended`). That
fails safe (worst case we hide a coupon that was actually fine); the positive
"mark untested as valid" direction does not, which is why only this one remains.

## Shopify guest-checkout validator (the live pilot — no login)

The big marketplaces above all gate the coupon field behind login, which makes
them poor first targets. **Shopify** does not: its hosted checkout is uniform
across every store it powers and exposes a guest **"Discount code or gift card"**
field with no sign-in. That makes one validator — `validators/merchants/shopify.py`
`ShopifyCheckoutValidator` — cover *every* Shopify merchant in the feed:

1. `GET /products.json` → pick an in-stock variant (no HTML scraping).
2. `GET /cart/<variant>:1` → Shopify seeds the cart and redirects into guest checkout.
3. Fill `input[name="reductions"]`, click **Apply**.
4. Classify: an inline **error** (`"Enter a valid discount code…"`, `"isn't valid"`,
   `"expired"`) → **INVALID**; an applied-discount tag / reduced total with no
   error → **VALID**; anything ambiguous → **UNVERIFIABLE** (fails safe).

Confirmed live (2026-09): on **kushals.com** the real code `NEW200` applies (a
remove-discount control appears, no error → VALID); on **fuaark.com** a bad code
surfaces its rejection via the discount input's `aria-describedby` → INVALID.
Stores are registered in `SHOPIFY_STORES` (keyed by normalized merchant name →
base URL); add a merchant only after confirming its storefront is Shopify.
Classifier is unit-tested offline in `tests/test_shopify_validator.py` (the safety
property: a rejection can never read as valid).

Pilot scope: **kushals** (has live usable codes in the feed) + fuaark. The
marketplace configs (Myntra/Amazon/Flipkart/Ajio/Nykaa) remain placeholders
needing login + selector tuning — the merchants with the most codes (firstcry,
Yatra, box8) are login-gated and out of scope for this pilot.

## Enabling checkout validation (opt-in)

Validation is **off by default** and gated by `VALIDATION_ENABLED`; nothing
dispatches a browser check until you turn it on, and only merchants with a
registered validator are ever checked (everything else stays `unverified` in the
honest directory).

To enable (in your own authorized environment):
1. Point the `worker` service at the Playwright image — already wired in
   `docker-compose.yml`:
   ```yaml
   worker:
     build: { context: ., dockerfile: Dockerfile.worker }
   ```
2. Set `VALIDATION_ENABLED=true` in `.env`.
3. `docker compose up -d --build worker` (first build pulls the ~2 GB Chromium image).
4. Trigger a batch: `docker compose exec worker python -c "from scheduler.tasks import enqueue_revalidations; print(enqueue_revalidations())"`.

For the **marketplace** configs you must additionally tune selectors against a
live authenticated session — most of their coupon fields need login + a non-empty
cart. The Shopify pilot needs none of that.

⚠️ This drives automated checkout actions against real retailers — the ToS /
bot-detection risk in the register above. Run at low volume, on the
re-validation cadence, never in tight loops.
