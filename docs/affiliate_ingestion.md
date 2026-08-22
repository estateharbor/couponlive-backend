# Affiliate-API ingestion research (Phase 2/3 finding)

**Bottom line:** the actual coupon *codes* on the target aggregators are
reveal-gated behind affiliate redirects (confirmed empirically on Desidime —
see below). Scraping their public pages yields clean *offer metadata* but not
codes. The right source for real, fresh, attributed codes is an **affiliate
network API**. Recommendation: make affiliate APIs the **primary** code source
and treat aggregator scraping as a **discovery / cross-check** signal.

## Why (the Desidime evidence)

On `desidime.com/coupons`, every coupon card renders merchant, title, discount,
description, and a source-side `coupon_id` — but the code itself is **not in the
HTML**. All 60 reveal buttons ("Get Coupon") point to
`visit.desidime.com/visit/coupons-2/<id>`, an affiliate click-tracking redirect.
Extracting the code by scraping would require firing that redirect per coupon —
generating fake affiliate clicks. We do **not** do that. This pattern is
universal among aggregators because the redirect *is* their monetization.

## Affiliate networks — coupon/offer API availability (verified Aug 2026)

| Network | Coupon/offer API? | Notes |
|---|---|---|
| **Cuelinks** | ✅ **Offers API** | HTTP GET with API token; live offers of the day, filterable by category; plus URL→affiliate-link conversion. 100+ networks / 10k+ campaigns. Good breadth. [developers.cuelinks.com](https://developers.cuelinks.com/) |
| **INRDeals** | ✅ **Coupons API** | Store-wise & category-wise *latest coupons and offers* (`store_id`, `search`, `category`, token). Also Store API, Deal Feed, Short-URL API. Returns actual codes. Best fit for a coupon product. |
| **Admitad (Mitgo)** | ✅ Coupons endpoint | Global CPA network, OAuth REST API with a `/coupons/` (promocodes) resource. Good for cross-network coverage; heavier onboarding. |
| **vCommission** | ⚠️ Partial | Indian CPA network; offer/coupon feeds for approved publishers, case-by-case. |
| **EarnKaro** | ⚠️ Link-first | Primarily "Profit Link" generation across 200+ stores; no prominent public *bulk coupon feed* API. Better for deep-linking than ingestion. |
| Third-party (Coupomated, CouponAPI.org) | ✅ Commercial | Single API aggregating coupons across networks. A paid shortcut if first-party onboarding is slow. |

## Source-by-source recommendation

| Aggregator | Scrape verdict | Code source |
|---|---|---|
| **Desidime** | ✅ metadata only (robots OK, plain HTML) | Codes via Cuelinks/INRDeals (same merchants) |
| **CashKaro** | ⚠️ metadata only; it's itself a cashback competitor | Codes via affiliate API, not CashKaro |
| **PaisaWapas** | ⛔ Cloudflare + captcha on listing | Affiliate API |
| **CouponDunia** | ⛔ captcha markers | Affiliate API |
| **GrabOn** | ⛔ **robots disallows `*/coupon-codes/`** | Affiliate API / partner feed only |
| **HappySale** | ⛔ defunct (redirects to GrabOn) | Drop |
| **Nearbuy** | ✅ scrapeable, but experiences not codes | Lower priority |

## Proposed architecture consequence

The codebase already supports this: `sources.ingestion_method` is a first-class
enum (`affiliate_api | scrape_requests | scrape_playwright`), and every source —
scraper or API ingestor — implements the same `BaseScraper.scrape() ->
list[RawCoupon]`. An affiliate-API ingestor is therefore a drop-in that yields
`RawCoupon` **with `code` populated** (`requires_reveal=False`), flowing through
the exact same normalize→dedupe→store pipeline.

**Decision needed from owner:** which network(s) to onboard first. Recommended
order: **INRDeals Coupons API** (returns codes directly, coupon-site oriented) →
**Cuelinks Offers API** (breadth) → **Admitad** (cross-network). Keys already
have slots in `.env.example`.
