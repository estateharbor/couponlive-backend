"""Phase 6 maintenance jobs: expire stale coupons, alert on stale sources.

Run periodically from Celery beat. Pure DB work + alerting; injectable session
so it's unit-testable against SQLite.
"""
from __future__ import annotations

from datetime import timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from core.alerting import alert_source_stale
from core.config import get_settings
from core.logging import get_logger
from models.base import utcnow
from models.enums import CouponStatus
from models.models import Coupon, Source

log = get_logger("maintenance")

# Coupons below this confidence, once stale, are expired out of default results.
LOW_CONFIDENCE = 0.3


def _aware(dt):
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def expire_stale_coupons(session: Session, *, commit: bool = True) -> int:
    """Flag coupons expired when they're stale AND low-confidence, so stale data
    stops being served by default. Returns the number expired."""
    settings = get_settings()
    cutoff = utcnow() - timedelta(hours=settings.stale_expire_hours)

    candidates = session.scalars(
        select(Coupon).where(
            Coupon.status != CouponStatus.expired,
            Coupon.confidence_score < LOW_CONFIDENCE,
            or_(Coupon.last_validated_at.is_(None), Coupon.last_validated_at < cutoff),
        )
    ).all()

    expired = 0
    for c in candidates:
        # Never validated coupons are only expired if they've also aged out by
        # first_seen (avoid instantly expiring brand-new, not-yet-validated codes).
        if c.last_validated_at is None and _aware(c.first_seen) >= cutoff:
            continue
        c.status = CouponStatus.expired
        expired += 1

    if commit:
        session.commit()
    log.info("maintenance.expired_stale", count=expired, cutoff_hours=settings.stale_expire_hours)
    return expired


def check_source_staleness(session: Session) -> list[str]:
    """Alert for any active source that hasn't succeeded within the window.
    Returns the names alerted (also useful for tests)."""
    settings = get_settings()
    now = utcnow()
    threshold = timedelta(hours=settings.source_stale_hours)

    stale: list[str] = []
    for src in session.scalars(select(Source).where(Source.is_active.is_(True))):
        last = src.last_success_at
        hours = (now - _aware(last)).total_seconds() / 3600 if last else float("inf")
        if last is None or (now - _aware(last)) > threshold:
            alert_source_stale(src.name, hours if last else settings.source_stale_hours * 99)
            stale.append(src.name)
    return stale
