"""/health endpoint: scraper/validator system health."""
from __future__ import annotations

from datetime import timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.config import get_settings
from models.base import utcnow
from models.enums import CouponStatus
from models.models import Coupon, Source
from models.schemas import HealthOut, SourceHealth
from api.deps import get_db

router = APIRouter(tags=["health"])


def _aware(dt):
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


@router.get("/health", response_model=HealthOut)
def health(db: Session = Depends(get_db)):
    settings = get_settings()
    now = utcnow()
    stale_after = timedelta(hours=settings.source_stale_hours)
    fresh_cutoff = now - timedelta(hours=settings.serve_freshness_hours)

    sources = db.scalars(select(Source)).all()
    source_healths = [SourceHealth.model_validate(s) for s in sources]

    # Degraded if any active source is stale or below the success-rate threshold.
    degraded = False
    for s in sources:
        if not s.is_active:
            continue
        if s.last_success_at is None or (now - _aware(s.last_success_at)) > stale_after:
            degraded = True
        if s.last_success_rate is not None and s.last_success_rate < settings.alert_min_success_rate:
            degraded = True

    total = db.scalar(select(func.count()).select_from(Coupon)) or 0
    valid_fresh = db.scalar(
        select(func.count()).select_from(Coupon).where(
            Coupon.status == CouponStatus.valid,
            Coupon.last_validated_at.is_not(None),
            Coupon.last_validated_at >= fresh_cutoff,
        )
    ) or 0

    return HealthOut(
        status="degraded" if degraded else "ok",
        sources=source_healths,
        total_coupons=int(total),
        total_valid_coupons=int(valid_fresh),
    )
