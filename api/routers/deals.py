"""/deals endpoint: code-less offers (e.g. Amazon deals via Cuelinks).

A "deal" is an offer with no coupon code — a discounted product/landing page the
user reaches through our affiliate link. These are deliberately separate from
`/coupons`: no code to copy, never a ✓ Verified badge (nothing to checkout-test),
and they never appear in the codes directory (which requires a non-null code).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from api.deps import get_db
from models.enums import CouponStatus
from models.models import Coupon, Merchant
from models.schemas import DealOut
from scrapers.normalize import normalize_merchant_name

router = APIRouter(prefix="/deals", tags=["deals"])


@router.get("", response_model=list[DealOut])
def list_deals(
    db: Session = Depends(get_db),
    merchant: str | None = Query(None, description="Merchant name (fuzzy-normalized), e.g. 'amazon'"),
    limit: int = Query(12, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Fresh, non-expired code-less offers, newest first. Filter by `merchant`
    (e.g. `amazon`) to power a store-specific deals section."""
    stmt = (
        select(Coupon)
        .join(Merchant)
        .where(Coupon.code.is_(None), Coupon.status != CouponStatus.expired)
    )
    if merchant:
        stmt = stmt.where(Merchant.normalized_name == normalize_merchant_name(merchant))
    stmt = stmt.order_by(desc(Coupon.last_seen)).limit(limit).offset(offset)

    return [_to_deal(c) for c in db.scalars(stmt).all()]


def _to_deal(c: Coupon) -> DealOut:
    out = DealOut.model_validate(c)
    out.merchant_name = c.merchant.name if c.merchant else None
    # Representative affiliate link: the most recently seen source URL.
    urls = sorted(
        (s for s in c.sources if s.source_url),
        key=lambda s: s.last_seen_at,
        reverse=True,
    )
    out.url = urls[0].source_url if urls else None
    return out
