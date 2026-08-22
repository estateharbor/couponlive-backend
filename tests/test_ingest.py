"""Pipeline tests: raw -> normalize -> dedupe -> store, against SQLite."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from models.enums import CouponStatus, DiscountType, IngestionMethod
from models.models import Coupon, CouponSource, Merchant, Source
from models.schemas import RawCoupon
from scrapers.pipeline import ingest_raw


def _rc(**kw) -> RawCoupon:
    base = dict(
        merchant_name="Myntra",
        scraped_at=datetime(2026, 8, 22, tzinfo=timezone.utc),
        ingestion_method=IngestionMethod.scrape_requests,
    )
    base.update(kw)
    return RawCoupon(**base)


def test_ingest_creates_merchant_coupon_and_provenance(db_session):
    raw = [_rc(code="SAVE20", discount_type=DiscountType.percentage, discount_value=20,
               source_url="https://desidime.com/x")]
    summary = ingest_raw(db_session, "Desidime", raw)

    assert summary.coupons_created == 1
    assert summary.merchants_created == 1
    assert summary.provenance_created == 1
    assert summary.success_rate == 1.0

    coupon = db_session.scalar(select(Coupon))
    assert coupon.code == "SAVE20"
    assert coupon.status is CouponStatus.unverified
    assert coupon.merchant.normalized_name == "myntra"
    assert db_session.scalar(select(func.count()).select_from(CouponSource)) == 1


def test_ingest_is_idempotent_and_advances_last_seen(db_session):
    early = datetime(2026, 8, 20, tzinfo=timezone.utc)
    late = early + timedelta(days=3)
    ingest_raw(db_session, "Desidime", [_rc(code="SAVE20", scraped_at=early)])
    ingest_raw(db_session, "Desidime", [_rc(code="SAVE20", scraped_at=late)])

    # Still exactly one coupon, one merchant, one provenance link.
    assert db_session.scalar(select(func.count()).select_from(Coupon)) == 1
    assert db_session.scalar(select(func.count()).select_from(Merchant)) == 1
    assert db_session.scalar(select(func.count()).select_from(CouponSource)) == 1

    coupon = db_session.scalar(select(Coupon))
    # SQLite returns tz-naive datetimes; compare on the naive wall-clock value.
    assert coupon.first_seen.replace(tzinfo=None) == early.replace(tzinfo=None)
    assert coupon.last_seen.replace(tzinfo=None) == late.replace(tzinfo=None)


def test_codeless_offers_stored_by_external_ref(db_session):
    raw = [
        _rc(code=None, external_ref="551335", requires_reveal=True),
        _rc(code=None, external_ref="551335", requires_reveal=True),  # dup
        _rc(code=None, external_ref="999999", requires_reveal=True),
    ]
    summary = ingest_raw(db_session, "Desidime", raw)
    assert summary.coupons_created == 2
    codeless = db_session.scalars(select(Coupon).where(Coupon.code.is_(None))).all()
    assert len(codeless) == 2
    assert all(c.requires_reveal for c in codeless)


def test_two_sources_merge_provenance_for_same_code(db_session):
    ingest_raw(db_session, "Desidime", [_rc(code="SAVE20", source_url="https://desidime")])
    ingest_raw(db_session, "CashKaro", [_rc(code="SAVE20", source_url="https://cashkaro")])

    assert db_session.scalar(select(func.count()).select_from(Coupon)) == 1
    assert db_session.scalar(select(func.count()).select_from(Source)) == 2
    # One coupon, two provenance rows (one per source).
    assert db_session.scalar(select(func.count()).select_from(CouponSource)) == 2


def test_source_bookkeeping_updated(db_session):
    ingest_raw(db_session, "Desidime", [_rc(code="SAVE20")])
    src = db_session.scalar(select(Source).where(Source.name == "Desidime"))
    assert src.last_scraped_at is not None
    assert src.last_success_at is not None
    assert src.last_success_rate == 1.0
