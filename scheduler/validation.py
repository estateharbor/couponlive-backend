"""Validation orchestration: which coupons to validate, in what order, and how
results feed back into status + confidence.

Priority policy:
  0 (highest) newly-ingested codes never validated yet
  1           top-merchant valid codes gone stale (re-validate every few hours)
  2           other stale codes

Only coupons that (a) have a code and (b) have a registered merchant validator
are selectable — a code-less offer or an unsupported merchant can't be checked.

`run_validation_batch` takes an injectable `validator_provider`, so tests drive
it with canned results and no real browser.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import get_settings
from core.confidence import compute_confidence
from core.logging import get_logger
from models.base import utcnow
from models.enums import CouponStatus, ValidationResultEnum
from models.models import Coupon, Merchant, UserFeedback, ValidationLog
from models.schemas import ValidationResult
from validators.base import BaseValidator
from validators.registry import get_validator

log = get_logger("validation")


@dataclass
class BatchSummary:
    attempted: int = 0
    valid: int = 0
    invalid: int = 0
    unverifiable: int = 0
    skipped_no_validator: int = 0
    errors: list[str] = field(default_factory=list)


def _priority_for(coupon: Coupon, revalidate_after: timedelta) -> int | None:
    """Return a priority (lower = sooner) or None if not due for validation."""
    if not coupon.code:
        return None
    # Pace by last ATTEMPT (last_checked_at), not last confirmation — so a code
    # that keeps coming back inconclusive isn't re-picked every single batch.
    last_checked = coupon.last_checked_at or coupon.last_validated_at
    if last_checked is None:
        return 0  # newly ingested, never checked
    age = utcnow() - _aware(last_checked)
    if coupon.status is CouponStatus.valid and coupon.merchant.priority > 0:
        return 1 if age >= revalidate_after else None
    return 2 if age >= revalidate_after else None


def _aware(dt):
    from datetime import timezone
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def select_coupons_to_validate(session: Session, limit: int = 50) -> list[tuple[int, Coupon]]:
    settings = get_settings()
    revalidate_after = timedelta(hours=settings.validate_top_merchant_revalidate_hours)

    rows = session.scalars(
        select(Coupon).join(Merchant).where(Coupon.code.is_not(None))
    ).all()

    scored: list[tuple[int, Coupon]] = []
    for c in rows:
        # Must have a registered validator for this merchant.
        if get_validator(c.merchant.normalized_name) is None:
            continue
        prio = _priority_for(c, revalidate_after)
        if prio is not None:
            scored.append((prio, c))

    # Sort by priority, then oldest-checked first (never-checked treated as oldest).
    def _checked_key(c: Coupon):
        last = c.last_checked_at or c.last_validated_at
        return _aware(last) if last else utcnow() - timedelta(days=3650)

    scored.sort(key=lambda t: (t[0], _checked_key(t[1])))
    return scored[:limit]


def _feedback_counts(session: Session, coupon_id: int) -> tuple[int, int]:
    fbs = session.scalars(
        select(UserFeedback).where(UserFeedback.coupon_id == coupon_id)
    ).all()
    total = len(fbs)
    positive = sum(1 for f in fbs if f.worked)
    return positive, total


def record_validation_result(
    session: Session, coupon: Coupon, result: ValidationResult
) -> None:
    """Append a validation log, update status/confidence/last_validated_at."""
    session.add(
        ValidationLog(
            coupon_id=coupon.id,
            validated_at=result.checked_at,
            result=result.result,
            error_message=result.error_message,
            response_snapshot=result.response_snapshot,
        )
    )
    # Every attempt updates last_checked_at (scheduler pacing).
    coupon.last_checked_at = result.checked_at

    if result.result is ValidationResultEnum.unverifiable:
        # Inconclusive (site blocked us / checkout unreachable) — NOT evidence the
        # code changed. Don't confirm, don't refresh the "Verified" clock, and
        # don't slam a previously-valid code's confidence down to the 0.40 floor.
        # If we keep failing to re-confirm, the serve-freshness window (which
        # keys off last_validated_at) ages it out on its own.
        return

    # Decisive result: it's a real confirmation, so update status + confidence
    # and stamp last_validated_at.
    if result.result is ValidationResultEnum.valid:
        coupon.status = CouponStatus.valid
    elif result.result is ValidationResultEnum.invalid:
        coupon.status = CouponStatus.invalid

    coupon.last_validated_at = result.checked_at
    positive, total = _feedback_counts(session, coupon.id)
    coupon.confidence_score = compute_confidence(result.result, positive, total)


ValidatorProvider = Callable[[str], BaseValidator | None]


def run_validation_batch(
    session: Session,
    limit: int = 50,
    *,
    validator_provider: ValidatorProvider = get_validator,
) -> BatchSummary:
    summary = BatchSummary()
    for _prio, coupon in select_coupons_to_validate(session, limit):
        validator = validator_provider(coupon.merchant.normalized_name)
        if validator is None:
            summary.skipped_no_validator += 1
            continue
        summary.attempted += 1
        try:
            res = validator.validate(coupon.code, coupon.merchant.name)
            record_validation_result(session, coupon, res)
            setattr(summary, res.result.value, getattr(summary, res.result.value) + 1)
        except Exception as exc:  # a validator should not raise, but be defensive
            summary.errors.append(f"coupon {coupon.id}: {exc}")
            log.warning("validation.coupon_failed", coupon_id=coupon.id, error=str(exc))
    session.commit()
    log.info("validation.batch_done", attempted=summary.attempted, valid=summary.valid,
             invalid=summary.invalid, unverifiable=summary.unverifiable)
    return summary
