"""Editorial CSV importer: parsing rules + honest ingest."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from models.enums import CouponStatus, DiscountType
from models.models import Coupon, Merchant
from scrapers.pipeline import ingest_raw
from scheduler.import_editorial import _enqueue_validation, _keep, _parse_discount, build_raw

_NOW = datetime(2026, 10, 2, tzinfo=timezone.utc)


def _row(**kw) -> dict:
    base = dict(store="AJIO", category="shopping", code="NEW30",
                headline="Flat 30% Off first purchase", description="New users get 30% off.",
                how_to_redeem="", expiry="", terms="", verified_note="",
                source_url="https://www.grabon.in/ajio-coupons/", code_type="code",
                slug_suggestion="ajio-new30")
    base.update(kw)
    return base


def test_parse_discount_from_headline():
    assert _parse_discount("Flat 30% Off first purchase") == (DiscountType.percentage, 30.0)
    assert _parse_discount("Flat ₹500 Off") == (DiscountType.fixed, 500.0)
    assert _parse_discount("Up to ₹150 Off") == (DiscountType.fixed, 150.0)
    assert _parse_discount("₹300 Cashback (Ola)") == (DiscountType.cashback, None)
    # A freebie/combo has no rupee/percent DISCOUNT even if it mentions a price.
    assert _parse_discount("Free Classic Zinger") == (DiscountType.unknown, None)
    assert _parse_discount("Gold 3 months for ₹1") == (DiscountType.unknown, None)


def test_keep_rules_skip_ai_bank_and_wallet():
    assert _keep(_row()) == "code"
    assert _keep(_row(code_type="no_code", category="food", store="Pizza Hut", code="")) == "deal"
    assert _keep(_row(category="ai", code_type="no_code", store="ChatGPT", code="")) is None
    assert _keep(_row(code_type="no_code", category="upi", store="PhonePe", code="")) is None
    assert _keep(_row(code_type="no_code", category="food", store="KFC (via Swiggy)", code="")) is None
    # A shopping sale-event announcement (no code) is skipped.
    assert _keep(_row(code_type="no_code", category="shopping", store="Amazon.in", code="")) is None


def test_build_raw_sets_merchant_homepage_not_aggregator():
    raws = build_raw([_row()], _NOW)
    assert len(raws) == 1
    # The click-out is the merchant's own site, never the GrabOn source we scraped.
    assert raws[0].source_url == "https://www.ajio.com"
    assert "grabon" not in (raws[0].source_url or "")


def test_freebie_deal_is_not_mislabeled_as_rupee_discount(db_session):
    """A code-less freebie with a min-order ₹ must land as a generic offer, not
    '₹499 Off' (the min order mistaken for the discount)."""
    rows = [_row(store="KFC", category="food", code_type="no_code", code="",
                 headline="Free Classic Zinger",
                 description="Free Classic Zinger on first web/app order with min ₹499.",
                 slug_suggestion="kfc-free-classic-zinger")]
    raws = build_raw(rows, _NOW)
    ingest_raw(db_session, "Editorial curation", raws)
    deal = db_session.scalar(select(Coupon).where(Coupon.code.is_(None)))
    assert deal is not None
    assert deal.discount_type is DiscountType.unknown
    assert deal.discount_value is None


def test_enqueue_validation_noop_when_disabled(db_session):
    # VALIDATION_ENABLED is off by default in tests — enqueue must be a safe no-op
    # (and never raise even without a broker), so the import itself still succeeds.
    ingest_raw(db_session, "Editorial curation", build_raw([_row()], _NOW))
    assert _enqueue_validation(db_session) == 0


def test_editorial_rows_land_unverified(db_session):
    rows = [_row(), _row(store="Myntra", code="MYNTRA300", headline="Flat ₹300 off first order",
                         description="Flat ₹300 off first order; min ₹1699.",
                         slug_suggestion="myntra-300")]
    ingest_raw(db_session, "Editorial curation", build_raw(rows, _NOW))
    coupons = db_session.scalars(select(Coupon)).all()
    assert len(coupons) == 2
    assert all(c.status is CouponStatus.unverified for c in coupons)  # never auto-verified
    myntra = db_session.scalar(select(Merchant).where(Merchant.normalized_name == "myntra"))
    assert myntra is not None
