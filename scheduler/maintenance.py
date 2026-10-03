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
from models.models import Coupon, CouponSource, Source

log = get_logger("maintenance")

# Coupons below this confidence, once stale, are expired out of default results.
LOW_CONFIDENCE = 0.3


def _aware(dt):
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _editorial_coupon_ids(session: Session) -> set[int]:
    """Coupons listed by the hand-checked editorial import (see
    scheduler/import_editorial.py), which get a longer shelf life."""
    from scheduler.import_editorial import SOURCE_NAME

    return set(
        session.scalars(
            select(CouponSource.coupon_id)
            .join(Source, Source.id == CouponSource.source_id)
            .where(Source.name == SOURCE_NAME)
        )
    )


def expire_stale_coupons(session: Session, *, commit: bool = True) -> int:
    """Flag coupons expired when they're stale AND low-confidence, so stale data
    stops being served by default. Returns the number expired."""
    settings = get_settings()
    now = utcnow()
    cutoff = now - timedelta(hours=settings.stale_expire_hours)
    editorial_cutoff = now - timedelta(days=settings.editorial_expire_days)

    # A stated end date is authoritative: past it, the code is expired whatever
    # its confidence; before it, the staleness window below doesn't apply.
    expired = 0
    for c in session.scalars(
        select(Coupon).where(
            Coupon.status != CouponStatus.expired,
            Coupon.expires_at.is_not(None),
            Coupon.expires_at < now,
        )
    ):
        c.status = CouponStatus.expired
        expired += 1

    candidates = session.scalars(
        select(Coupon).where(
            Coupon.status != CouponStatus.expired,
            Coupon.expires_at.is_(None),
            Coupon.confidence_score < LOW_CONFIDENCE,
            or_(Coupon.last_validated_at.is_(None), Coupon.last_validated_at < cutoff),
        )
    ).all()
    editorial = _editorial_coupon_ids(session) if candidates else set()

    for c in candidates:
        # Never-validated coupons expire only once no source has listed them for
        # the window (last_seen, not first_seen — a code a feed or editor is still
        # listing is not stale, however old it is). Hand-checked editorial codes
        # mostly can't be validated, so they get the longer editorial window.
        if c.last_validated_at is None:
            window = editorial_cutoff if c.id in editorial else cutoff
            if _aware(c.last_seen) >= window:
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
