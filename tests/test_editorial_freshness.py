"""Editorial codes vs the stale-expiry job, and re-listing an expired code.

Regression for boAt FESTIVE (Oct 2026): an old feed row with the same code had
been expired; the editorial re-import matched it but left it expired (and kept
the feed's stale description), so the checked code never showed. Also guards
that never-validated editorial codes aren't expired 48h after first import."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from models.enums import CouponStatus, DiscountType, IngestionMethod
from models.models import Coupon
from models.schemas import RawCoupon
from scheduler.import_editorial import SOURCE_NAME
from scheduler.maintenance import expire_stale_coupons
from scrapers.pipeline import ingest_raw

NOW = datetime.now(timezone.utc)


def _raw(desc: str, when: datetime, *, dtype=DiscountType.percentage, value=5.0,
         method=IngestionMethod.scrape_requests) -> RawCoupon:
    return RawCoupon(merchant_name="boAt", code="FESTIVE", description=desc,
                     discount_type=dtype, discount_value=value, scraped_at=when,
                     ingestion_method=method)


def _coupon(db_session) -> Coupon:
    return db_session.query(Coupon).filter_by(code="FESTIVE").one()


def test_reimport_revives_expired_code_and_refreshes_text(db_session):
    old = NOW - timedelta(days=30)
    ingest_raw(db_session, "OldFeed",
               [_raw("Grand Festive Sale: up to 80% off", old, value=80.0)])
    c = _coupon(db_session)
    c.status = CouponStatus.expired
    db_session.commit()

    ingest_raw(db_session, SOURCE_NAME,
               [_raw("Extra 5% off on prepaid orders", NOW)], authoritative=True)
    c = _coupon(db_session)
    assert c.status is CouponStatus.unverified           # back, but never auto-valid
    assert c.description == "Extra 5% off on prepaid orders"
    assert float(c.discount_value) == 5.0


def test_non_authoritative_source_only_fills_gaps(db_session):
    ingest_raw(db_session, SOURCE_NAME, [_raw("Checked text", NOW)], authoritative=True)
    ingest_raw(db_session, "SomeScraper", [_raw("Scraped text", NOW, value=80.0)])
    c = _coupon(db_session)
    assert c.description == "Checked text" and float(c.discount_value) == 5.0


def test_editorial_code_survives_past_48h_but_expires_after_window(db_session):
    first = NOW - timedelta(days=3)
    ingest_raw(db_session, SOURCE_NAME, [_raw("Checked", first)], authoritative=True)
    c = _coupon(db_session)
    c.first_seen = c.last_seen = first
    db_session.commit()

    expire_stale_coupons(db_session)                      # 3 days < 14-day window
    assert _coupon(db_session).status is CouponStatus.unverified

    c = _coupon(db_session)
    c.last_seen = NOW - timedelta(days=15)
    db_session.commit()
    expire_stale_coupons(db_session)
    assert _coupon(db_session).status is CouponStatus.expired


def test_feed_code_uses_last_seen_not_first_seen(db_session):
    ingest_raw(db_session, "Feed", [_raw("Live in feed", NOW)])
    c = _coupon(db_session)
    c.first_seen = NOW - timedelta(days=10)               # old, but still being listed
    db_session.commit()
    expire_stale_coupons(db_session)
    assert _coupon(db_session).status is CouponStatus.unverified

    c = _coupon(db_session)
    c.last_seen = NOW - timedelta(days=3)                 # dropped from feed > 48h
    db_session.commit()
    expire_stale_coupons(db_session)
    assert _coupon(db_session).status is CouponStatus.expired


# --- Stated end dates (expires_at) ----------------------------------------
import pytest

from scheduler.import_editorial import _parse_expiry, backfill_expiry


@pytest.mark.parametrize("text,ymd", [
    ("31 Dec 2026", (2026, 12, 31)),
    ("31-Oct-26", (2026, 10, 31)),
    ("Till 31 Oct 2026 (per GrabOn)", (2026, 10, 31)),
    ("Valid till 24 October 2026", (2026, 10, 24)),
    ("31 Oct 2026 (flash)", (2026, 10, 31)),
])
def test_parse_expiry_dates(text, ymd):
    d = _parse_expiry(text)
    # End of that day in India: 23:59:59 IST == 18:29:59 UTC.
    assert (d.year, d.month, d.day, d.hour, d.minute) == (*ymd, 18, 29)


@pytest.mark.parametrize("text", [
    "Check at checkout", "Early Deals live now; main GIF from 8 Oct 2026 (end TBA)",
    "BFF window from 8 Oct 2026", "Before BFF early access (7 Oct 2026)", "", "31 Foo 2026",
])
def test_parse_expiry_rejects_vague_text(text):
    assert _parse_expiry(text) is None


def _dated(when: datetime, end: datetime) -> RawCoupon:
    r = _raw("Checked", when)
    return r.model_copy(update={"expires_at": end})


def test_dated_code_stays_past_editorial_window_until_end_date(db_session):
    long_ago = NOW - timedelta(days=30)
    ingest_raw(db_session, SOURCE_NAME, [_dated(long_ago, NOW + timedelta(days=60))],
               authoritative=True)
    c = _coupon(db_session)
    c.first_seen = c.last_seen = long_ago
    db_session.commit()
    expire_stale_coupons(db_session)                      # 30 days > 14, but date ahead
    assert _coupon(db_session).status is CouponStatus.unverified


def test_code_expires_once_end_date_passes_even_if_valid(db_session):
    ingest_raw(db_session, SOURCE_NAME, [_dated(NOW, NOW - timedelta(hours=1))],
               authoritative=True)
    c = _coupon(db_session)
    c.status, c.confidence_score, c.last_validated_at = CouponStatus.valid, 0.9, NOW
    db_session.commit()
    expire_stale_coupons(db_session)
    assert _coupon(db_session).status is CouponStatus.expired


def test_reimport_does_not_revive_code_past_end_date(db_session):
    ingest_raw(db_session, SOURCE_NAME, [_dated(NOW, NOW - timedelta(days=1))],
               authoritative=True)
    c = _coupon(db_session)
    c.status = CouponStatus.expired
    db_session.commit()
    ingest_raw(db_session, SOURCE_NAME, [_raw("Checked", NOW)], authoritative=True)
    assert _coupon(db_session).status is CouponStatus.expired


def test_backfill_sets_end_date_without_touching_last_seen(db_session, tmp_path):
    seen = NOW - timedelta(days=2)
    ingest_raw(db_session, SOURCE_NAME, [_raw("Checked", seen)], authoritative=True)
    c = _coupon(db_session)
    c.status = CouponStatus.expired                       # wrongly expired by old rule
    db_session.commit()

    f = tmp_path / "x.csv"
    f.write_text(
        "store,category,code,headline,description,how_to_redeem,expiry,terms,"
        "verified_note,source_url,code_type,slug_suggestion\n"
        "boAt,shopping,FESTIVE,Extra 5% off,d,h,31 Dec 2099,t,v,u,code,boat-festive\n",
        encoding="utf-8",
    )
    assert backfill_expiry(db_session, [f]) == 1
    c = _coupon(db_session)
    assert c.expires_at is not None and c.expires_at.year == 2099
    assert c.status is CouponStatus.unverified
    assert abs((c.last_seen.replace(tzinfo=timezone.utc) - seen).total_seconds()) < 1
