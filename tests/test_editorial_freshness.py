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
