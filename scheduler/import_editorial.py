"""Import an editorial CSV of hand-curated codes/deals into the catalog.

Source material is a spreadsheet an editor assembled by cross-checking public
aggregators (GrabOn/Desidime/etc.) and official merchant pages. We treat it as a
NON-authoritative source: every row lands as `unverified` (shown "Not verified
yet") and only earns the green ✓ Verified badge if OUR checkout validator later
confirms it. See scrapers/pipeline.ingest_raw.

Two honest guarantees enforced here:
  * No row is ever marked valid/verified on import.
  * The click-out never points at an aggregator we scraped from — we store the
    MERCHANT's own homepage as the offer URL (no competitor traffic, no fake
    affiliate link), so a shopper lands on the store to use the code.

The CSV also carries AI free-trial rows (category=ai) and bank/UPI cashback
rows — those are NOT coupons and are skipped here. AI trials are curated into
scheduler/seed_trials.py instead.

    docker compose exec worker python -m scheduler.import_editorial
    # or point at a specific file:
    docker compose exec worker python -m scheduler.import_editorial data/editorial/coupons-2026-10-02.csv
"""
from __future__ import annotations

import csv
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.config import get_settings
from core.logging import get_logger
from models.base import get_sessionmaker
from models.enums import DiscountType, IngestionMethod
from models.schemas import RawCoupon
from scrapers.normalize import normalize_merchant_name
from scrapers.pipeline import ingest_raw

log = get_logger("import.editorial")

SOURCE_NAME = "Editorial curation"
DEFAULT_CSV = Path("data/editorial/coupons-2026-10-02.csv")

# The offer's click-out: the MERCHANT'S own homepage (never the aggregator we
# saw it on). Keyed by normalized merchant name.
MERCHANT_HOME: dict[str, str] = {
    "ajio": "https://www.ajio.com",
    "myntra": "https://www.myntra.com",
    "nykaa": "https://www.nykaa.com",
    "dominos": "https://www.dominos.co.in",
    "swiggy": "https://www.swiggy.com",
    "zomato": "https://www.zomato.com",
    "makemytrip": "https://www.makemytrip.com",
    "cleartrip": "https://www.cleartrip.com",
    "redbus": "https://www.redbus.in",
    "uber": "https://www.uber.com/in/en/",
    "ola": "https://www.olacabs.com",
    "pizzahut": "https://www.pizzahut.co.in",
    "burgerking": "https://www.burgerking.in",
    "kfc": "https://online.kfc.co.in",
    "amazon": "https://www.amazon.in",
    "flipkart": "https://www.flipkart.com",
    "tatacliqluxury": "https://luxury.tatacliq.com",
    "swiggydineout": "https://www.swiggy.com/dineout",
    "goibibo": "https://www.goibibo.com",
    "boat": "https://www.boat-lifestyle.com",
}

# Wallet/bank intermediaries (and "via <platform>" combos) that must not be
# imported as a standalone merchant coupon.
_SKIP_STORE_MARKERS = ("×", "(via", " via ", "phonepe", "cred", "paytm", "amazon pay")

_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
# Mirror scrapers.normalize._RUP_RE: word-boundary currency token + digit-led
# amount (so "rs" inside "users" and a stray "₹," are never matched).
_RUP = re.compile(r"(?:₹|\brs\.?|\binr\b)\s*(\d[\d,]*(?:\.\d+)?)", re.IGNORECASE)
_OFF_CTX = ("off", "discount", "save", "flat", "cashback")


def _parse_discount(headline: str) -> tuple[DiscountType, float | None]:
    """Derive the discount from the HEADLINE (it always leads with the offer),
    which is more reliable than the free-text description for freebies/combos."""
    t = (headline or "").lower()
    if "cashback" in t:
        m = _PCT.search(t)
        return DiscountType.cashback, (float(m.group(1)) if m else None)
    if "bogo" in t or "buy 1" in t or "buy one" in t:
        return DiscountType.bogo, None
    m = _PCT.search(t)
    if m:
        return DiscountType.percentage, float(m.group(1))
    if any(k in t for k in _OFF_CTX):
        r = _RUP.search(t)
        if r:
            return DiscountType.fixed, float(r.group(1).replace(",", ""))
    return DiscountType.unknown, None


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
# The whole expiry cell is a date, optionally prefixed "valid till/until/through"
# ("31 Dec 2026", "31-Dec-26", "Till 31 Oct 2026 (per GrabOn)"). Anything
# vaguer ("Check at checkout", "main sale from 8 Oct (end TBA)") is NOT an end
# date → None.
_EXPIRY_RE = re.compile(
    r"^\s*(?:(?:valid\s+)?(?:till|until|through|thru|to)\s+)?"
    r"(\d{1,2})[\s-]+([a-z]{3,9})\.?,?[\s-]+(\d{4}|\d{2})"
    r"\s*\.?\s*(?:\([^)]*\))?\s*$",
    re.IGNORECASE,
)
IST = timezone(timedelta(hours=5, minutes=30))


def _parse_expiry(text: str) -> datetime | None:
    """The offer's end date as the END of that day in India (23:59:59 IST), or
    None when the cell isn't a plain date."""
    m = _EXPIRY_RE.match(text or "")
    if not m:
        return None
    month = _MONTHS.get(m.group(2)[:3].lower())
    if month is None:
        return None
    year = int(m.group(3))
    year += 2000 if year < 100 else 0
    try:
        day = datetime(year, month, int(m.group(1)), 23, 59, 59, tzinfo=IST)
    except ValueError:
        return None
    return day.astimezone(timezone.utc)


def _mask_currency(text: str) -> str:
    """Rewrite '₹499'/'Rs 499' as '499 rupees' so the discount reconciler
    (scrapers.normalize.sanitize_discount) doesn't mistake a MIN-ORDER amount for
    the discount on a freebie/combo that has no real rupee/percent discount."""
    return _RUP.sub(lambda m: f"{m.group(1)} rupees", text or "")


def _compose_description(row: dict, discount_known: bool) -> str:
    desc = (row.get("description") or "").strip()
    expiry = (row.get("expiry") or "").strip()
    # Append a real expiry date (ignore vague "Check at checkout" placeholders).
    if re.search(r"\d{4}|\d{1,2}[- ][A-Za-z]{3}", expiry) and "check" not in expiry.lower():
        if expiry.lower() not in desc.lower():
            desc = f"{desc} Valid till {expiry}.".strip()
    # For freebies/combos (no real %/₹ discount) neutralize currency so the
    # reconciler leaves the label as a generic offer instead of "₹<min> Off".
    return desc if discount_known else _mask_currency(desc)


def _keep(row: dict) -> str | None:
    """Return 'code' | 'deal' for rows to import, else None (skip)."""
    cat = (row.get("category") or "").strip().lower()
    code_type = (row.get("code_type") or "").strip().lower()
    store = (row.get("store") or "").strip().lower()
    if cat == "ai":
        return None  # free-trials handled in seed_trials.py
    if any(mark in store for mark in _SKIP_STORE_MARKERS):
        return None  # wallet/bank intermediaries (PhonePe/CRED/Paytm/"via" combos)
    if code_type == "code" and (row.get("code") or "").strip():
        return "code"
    # Code-less, direct-merchant offers → deals (shown as neutral "Deal" cards,
    # never Verified). Covers food deals AND shopping offers like the Amazon.in /
    # Flipkart festive/bank deals that fill otherwise-empty mega-store pages.
    if code_type == "no_code" and cat in ("food", "shopping"):
        return "deal"
    return None  # UPI/standalone-bank rows — skipped


def build_raw(rows: list[dict], now: datetime) -> list[RawCoupon]:
    raws: list[RawCoupon] = []
    for row in rows:
        kind = _keep(row)
        if kind is None:
            continue
        store = (row.get("store") or "").strip()
        dtype, dval = _parse_discount(row.get("headline") or "")
        description = _compose_description(row, dtype is not DiscountType.unknown)
        slug = (row.get("slug_suggestion") or "").strip() or None
        home = MERCHANT_HOME.get(normalize_merchant_name(store))
        raws.append(
            RawCoupon(
                merchant_name=store,
                code=(row.get("code") or "").strip() or None if kind == "code" else None,
                external_ref=slug,
                requires_reveal=False,
                description=description or None,
                discount_type=dtype,
                discount_value=dval,
                source_url=home,  # merchant homepage, NEVER the aggregator
                scraped_at=now,
                ingestion_method=IngestionMethod.scrape_requests,
                expires_at=_parse_expiry(row.get("expiry") or ""),
            )
        )
    return raws


def _enqueue_validation(session) -> int:
    """Queue the just-imported, never-checked codes for checkout validation.

    Only coupons whose merchant has a registered validator (AJIO/Myntra/Nykaa +
    Shopify stores today) are selectable — the rest honestly stay "Not verified
    yet". No-op when VALIDATION_ENABLED is off or the broker isn't reachable."""
    if not get_settings().validation_enabled:
        return 0
    try:
        from scheduler.tasks import validate_coupon
        from scheduler.validation import select_coupons_to_validate

        queued = 0
        for _prio, coupon in select_coupons_to_validate(session, limit=500):
            if coupon.last_checked_at is None:  # newly imported, never checked
                validate_coupon.apply_async(args=[coupon.id], priority=0)
                queued += 1
        return queued
    except Exception as exc:  # broker down / not in a worker — import still succeeded
        log.warning("editorial.enqueue_failed", error=str(exc))
        return 0


def main(path: str | Path = DEFAULT_CSV) -> None:
    path = Path(path)
    if not path.exists():
        raise SystemExit(f"CSV not found: {path}")
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    now = datetime.now(timezone.utc)
    raws = build_raw(rows, now)
    if not raws:
        print("No importable rows found (coupons/food deals).")
        return

    session = get_sessionmaker()()
    try:
        summary = ingest_raw(session, SOURCE_NAME, raws, authoritative=True)
        queued = _enqueue_validation(session)
    finally:
        session.close()

    print(
        f"editorial import done — csv rows: {len(rows)}, importable: {len(raws)}, "
        f"created: {summary.coupons_created}, updated: {summary.coupons_updated}, "
        f"merchants created: {summary.merchants_created}, errors: {len(summary.errors)}"
    )
    print(
        f"queued for checkout validation: {queued}"
        + ("" if get_settings().validation_enabled
           else " (VALIDATION_ENABLED is off — set it to validate)")
    )
    for err in summary.errors[:10]:
        print("  !", err)


def backfill_expiry(session, paths: list[Path]) -> int:
    """Set `expires_at` on already-imported editorial coupons from their CSVs,
    WITHOUT re-importing (so last_seen — and the 14-day editorial window for
    undated codes — isn't reset). A dated code that was expired early by the
    old staleness rule comes back as `unverified` if its end date is ahead.
    Returns the number of coupons whose end date was set."""
    from sqlalchemy import select

    from models.base import utcnow
    from models.enums import CouponStatus
    from models.models import Coupon, Merchant
    from scrapers.normalize import normalize_code

    now = utcnow()
    updated = 0
    for path in paths:
        with Path(path).open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        for row in rows:
            kind = _keep(row)
            when = _parse_expiry(row.get("expiry") or "")
            if kind is None or when is None:
                continue
            merchant = session.scalar(select(Merchant).where(
                Merchant.normalized_name == normalize_merchant_name(row.get("store") or "")))
            if merchant is None:
                continue
            stmt = select(Coupon).where(Coupon.merchant_id == merchant.id)
            code = normalize_code(row.get("code")) if kind == "code" else None
            slug = (row.get("slug_suggestion") or "").strip()
            if code:
                stmt = stmt.where(Coupon.code == code)
            elif slug:
                stmt = stmt.where(Coupon.code.is_(None), Coupon.external_ref == slug)
            else:
                continue
            coupon = session.scalar(stmt)
            if coupon is None:
                continue
            coupon.expires_at = when
            if coupon.status is CouponStatus.expired and when > now:
                coupon.status = CouponStatus.unverified
            updated += 1
    session.commit()
    return updated


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--backfill-expiry":
        files = [Path(a) for a in args[1:]] or sorted(Path("data/editorial").glob("*.csv"))
        s = get_sessionmaker()()
        try:
            print(f"end dates set on {backfill_expiry(s, files)} coupons "
                  f"from {len(files)} CSV file(s)")
        finally:
            s.close()
    else:
        main(args[0] if args else DEFAULT_CSV)
