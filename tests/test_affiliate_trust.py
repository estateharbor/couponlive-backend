"""Affiliate-trust: code-bearing coupons from affiliate feeds show as valid
immediately; scraped/code-less/validator-owned coupons are left alone.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from models.enums import CouponStatus, DiscountType, IngestionMethod
from models.models import Coupon, Merchant
from models.schemas import RawCoupon
from scrapers.pipeline import ingest_raw


def _rc(**kw) -> RawCoupon:
    base = dict(scraped_at=datetime(2026, 8, 26, tzinfo=timezone.utc))
    base.update(kw)
    return RawCoupon(**base)


def test_affiliate_code_promoted_to_valid(db_session):
    ingest_raw(db_session, "LinkMyDeals", [_rc(
        merchant_name="Box8", code="APP50", external_ref="111",
        discount_type=DiscountType.percentage, discount_value=50,
        ingestion_method=IngestionMethod.affiliate_api)])
    c = db_session.scalar(select(Coupon).where(Coupon.code == "APP50"))
    assert c.status is CouponStatus.valid
    assert c.confidence_score == 0.7                 # trusted-feed prior
    assert c.last_validated_at is not None           # fresh -> served by default


def test_scraped_code_stays_unverified(db_session):
    ingest_raw(db_session, "Desidime", [_rc(
        merchant_name="Myntra", code="SAVE20",
        ingestion_method=IngestionMethod.scrape_requests)])
    c = db_session.scalar(select(Coupon).where(Coupon.code == "SAVE20"))
    assert c.status is CouponStatus.unverified       # scraped codes still need validation


def test_affiliate_codeless_deal_stays_unverified(db_session):
    ingest_raw(db_session, "LinkMyDeals", [_rc(
        merchant_name="Kushals", code=None, external_ref="999",
        ingestion_method=IngestionMethod.affiliate_api)])
    c = db_session.scalar(select(Coupon).where(Coupon.external_ref == "999"))
    assert c.status is CouponStatus.unverified       # a code-less deal isn't a "code"


def test_affiliate_resync_clears_stale_discount(db_session):
    m = Merchant(name="Firstcry", normalized_name="firstcry")
    db_session.add(m); db_session.flush()
    now = datetime.now(timezone.utc)
    db_session.add(Coupon(merchant_id=m.id, code="FCRY500", external_ref="7",
                          discount_type=DiscountType.unknown, discount_value=8377.0,
                          status=CouponStatus.valid, confidence_score=0.7,
                          first_seen=now, last_seen=now))
    db_session.commit()

    # Feed re-reads it with no parseable discount -> the stale 8377 must be cleared.
    ingest_raw(db_session, "LinkMyDeals", [_rc(
        merchant_name="Firstcry", code="FCRY500", external_ref="7",
        discount_type=DiscountType.unknown, discount_value=None,
        ingestion_method=IngestionMethod.affiliate_api)])
    c = db_session.scalar(select(Coupon).where(Coupon.code == "FCRY500"))
    assert c.discount_value is None


def test_affiliate_does_not_override_invalid(db_session):
    m = Merchant(name="Box8", normalized_name="box8")
    db_session.add(m); db_session.flush()
    now = datetime.now(timezone.utc)
    # A validator (or suspension) previously marked this invalid.
    db_session.add(Coupon(merchant_id=m.id, code="DEAD", external_ref="1",
                          status=CouponStatus.invalid, confidence_score=0.05,
                          first_seen=now, last_seen=now))
    db_session.commit()

    ingest_raw(db_session, "LinkMyDeals", [_rc(
        merchant_name="Box8", code="DEAD", external_ref="1",
        ingestion_method=IngestionMethod.affiliate_api)])
    c = db_session.scalar(select(Coupon).where(Coupon.code == "DEAD"))
    assert c.status is CouponStatus.invalid          # re-sync must NOT resurrect it
