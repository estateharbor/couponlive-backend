"""/merchants endpoint: list merchants with coupon counts."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.config import get_settings
from models.base import utcnow
from models.enums import CouponStatus
from models.models import Coupon, Merchant
from models.schemas import MerchantOut
from api.deps import get_db

router = APIRouter(prefix="/merchants", tags=["merchants"])


@router.get("", response_model=list[MerchantOut])
def list_merchants(db: Session = Depends(get_db)):
    settings = get_settings()
    fresh_cutoff = utcnow() - timedelta(hours=settings.serve_freshness_hours)

    total_sub = (
        select(Coupon.merchant_id, func.count().label("total"))
        .group_by(Coupon.merchant_id)
        .subquery()
    )
    valid_sub = (
        select(Coupon.merchant_id, func.count().label("valid"))
        .where(
            Coupon.status == CouponStatus.valid,
            Coupon.last_validated_at.is_not(None),
            Coupon.last_validated_at >= fresh_cutoff,
        )
        .group_by(Coupon.merchant_id)
        .subquery()
    )

    rows = db.execute(
        select(
            Merchant,
            func.coalesce(total_sub.c.total, 0),
            func.coalesce(valid_sub.c.valid, 0),
        )
        .outerjoin(total_sub, total_sub.c.merchant_id == Merchant.id)
        .outerjoin(valid_sub, valid_sub.c.merchant_id == Merchant.id)
        .order_by(func.coalesce(total_sub.c.total, 0).desc())
    ).all()

    out = []
    for merchant, total, valid in rows:
        m = MerchantOut.model_validate(merchant)
        m.coupon_count = int(total)
        m.valid_coupon_count = int(valid)
        out.append(m)
    return out
