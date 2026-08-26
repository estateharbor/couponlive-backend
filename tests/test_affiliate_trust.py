"""Regression: NO source gets a fast-path to `valid`.

Every source's coupons — affiliate feeds (LinkMyDeals/INRDeals) included — land
`unverified` on ingest and must earn `valid` through the real validation worker,
exactly like a scraped Desidime coupon. The only immediate status change allowed
is the *negative* suspended -> expired signal (which fails safe).

This guards against silently reintroducing a per-source "trust bonus".
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from models.enums import CouponStatus, DiscountType, IngestionMethod
from models.models import Coupon, Merchant
from models.schemas import RawCoupon
from scrapers.linkmydeals_feed import SuspendedOffer
from scrapers.pipeline import expire_suspended, ingest_raw


def _rc(**kw) -> RawCoupon:
    base = dict(scraped_at=datetime(2026, 8, 27, tzinfo=timezone.utc))
    base.update(kw)
    return RawCoupon(**base)


@pytest.mark.parametrize("method,source", [
    (IngestionMethod.affiliate_api, "LinkMyDeals"),
    (IngestionMethod.affiliate_api, "INRDeals"),
    (IngestionMethod.scrape_requests, "Desidime"),
])
def test_ingest_never_marks_valid(db_session, method, source):
    """The core regression: ingest must NEVER produce status=valid, for ANY source."""
    ingest_raw(db_session, source, [_rc(
        merchant_name="Box8", code="APP50", external_ref="1",
        discount_type=DiscountType.percentage, discount_value=50,
        ingestion_method=method)])
    c = db_session.scalar(select(Coupon))
    assert c.status is CouponStatus.unverified   # no source-specific trust bonus
    assert c.confidence_score == 0.0             # confidence only from validation/feedback
    assert c.last_validated_at is None           # not faked to look "verified"


def test_suspended_still_expires(db_session):
    """The kept exception: a supplier 'suspended' signal short-circuits to expired."""
    m = Merchant(name="Nykaa", normalized_name="nykaa")
    db_session.add(m); db_session.flush()
    now = datetime.now(timezone.utc)
    db_session.add(Coupon(merchant_id=m.id, code="OLD10", external_ref="9",
                          status=CouponStatus.unverified, first_seen=now, last_seen=now))
    db_session.commit()

    assert expire_suspended(db_session, [SuspendedOffer("Nykaa", "OLD10", "9")]) == 1
    c = db_session.scalar(select(Coupon).where(Coupon.code == "OLD10"))
    assert c.status is CouponStatus.expired


def test_affiliate_resync_clears_stale_discount(db_session):
    """Unrelated to trust — the feed stays authoritative for its own DISCOUNT
    fields, so a stale value from an earlier mapping is cleared on re-sync."""
    m = Merchant(name="Firstcry", normalized_name="firstcry")
    db_session.add(m); db_session.flush()
    now = datetime.now(timezone.utc)
    db_session.add(Coupon(merchant_id=m.id, code="FCRY500", external_ref="7",
                          discount_type=DiscountType.unknown, discount_value=8377.0,
                          status=CouponStatus.unverified, first_seen=now, last_seen=now))
    db_session.commit()

    ingest_raw(db_session, "LinkMyDeals", [_rc(
        merchant_name="Firstcry", code="FCRY500", external_ref="7",
        discount_type=DiscountType.unknown, discount_value=None,
        ingestion_method=IngestionMethod.affiliate_api)])
    c = db_session.scalar(select(Coupon).where(Coupon.code == "FCRY500"))
    assert c.discount_value is None              # stale 8377 cleared
    assert c.status is CouponStatus.unverified   # ...and still NOT promoted to valid
