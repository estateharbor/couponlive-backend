"""Validation orchestration tests (no browser): confidence scoring, priority
selection, and result recording -> status/confidence/logs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from core.confidence import compute_confidence
from models.enums import CouponStatus, ValidationResultEnum
from models.models import Coupon, Merchant, UserFeedback, ValidationLog
from models.schemas import ValidationResult
from scheduler.validation import (
    record_validation_result,
    run_validation_batch,
    select_coupons_to_validate,
)


def _now():
    return datetime.now(timezone.utc)


# --- confidence ----------------------------------------------------------
def test_confidence_bounds_and_ordering():
    assert compute_confidence(ValidationResultEnum.valid) > compute_confidence(
        ValidationResultEnum.unverifiable
    ) > compute_confidence(ValidationResultEnum.invalid)
    assert 0.0 <= compute_confidence(None) <= 1.0


def test_confidence_feedback_pulls_score():
    high = compute_confidence(ValidationResultEnum.valid, feedback_positive=10, feedback_total=10)
    low = compute_confidence(ValidationResultEnum.valid, feedback_positive=0, feedback_total=10)
    assert high > low
    assert low < 0.85  # sustained negative feedback drags a 'valid' prior down


# --- helpers -------------------------------------------------------------
def _mk_coupon(session, *, merchant="myntra", code="C1", status=CouponStatus.unverified,
               last_validated=None, priority=0):
    m = session.scalar(select(Merchant).where(Merchant.normalized_name == merchant))
    if m is None:
        m = Merchant(name=merchant.title(), normalized_name=merchant, priority=priority)
        session.add(m); session.flush()
    c = Coupon(merchant_id=m.id, code=code, first_seen=_now(), last_seen=_now(),
               status=status, last_validated_at=last_validated)
    session.add(c); session.flush()
    return c


# --- selection priority --------------------------------------------------
def test_new_codes_selected_first(db_session):
    # New (never validated) vs. an already-validated fresh one.
    new = _mk_coupon(db_session, code="NEW1", last_validated=None)
    _mk_coupon(db_session, code="FRESH", status=CouponStatus.valid,
               last_validated=_now())  # fresh -> not due
    db_session.commit()

    selected = select_coupons_to_validate(db_session, limit=10)
    assert selected, "expected at least the new code"
    assert selected[0][1].code == "NEW1"       # priority 0
    assert selected[0][0] == 0


def test_codeless_and_unsupported_merchants_excluded(db_session):
    _mk_coupon(db_session, merchant="myntra", code=None)          # code-less
    _mk_coupon(db_session, merchant="unknownstore", code="Z1")     # no validator
    db_session.commit()
    assert select_coupons_to_validate(db_session, limit=10) == []


# --- recording result ----------------------------------------------------
def test_record_result_updates_status_confidence_and_logs(db_session):
    coupon = _mk_coupon(db_session, code="APPLY1")
    db_session.commit()

    res = ValidationResult(result=ValidationResultEnum.valid,
                           response_snapshot="you saved ₹200", checked_at=_now())
    record_validation_result(db_session, coupon, res)
    db_session.commit()

    assert coupon.status is CouponStatus.valid
    assert coupon.last_validated_at is not None
    assert coupon.confidence_score == compute_confidence(ValidationResultEnum.valid)
    assert db_session.scalar(select(func.count()).select_from(ValidationLog)) == 1


def test_run_batch_with_injected_validator(db_session):
    _mk_coupon(db_session, code="GOOD", merchant="myntra")
    _mk_coupon(db_session, code="BAD", merchant="nykaa")
    db_session.commit()

    class _FakeValidator:
        def __init__(self, result): self.result = result
        def validate(self, code, merchant):
            r = ValidationResultEnum.valid if code == "GOOD" else ValidationResultEnum.invalid
            return ValidationResult(result=r, checked_at=_now())

    def provider(_name):
        return _FakeValidator(None)

    summary = run_validation_batch(db_session, limit=10, validator_provider=provider)
    assert summary.attempted == 2
    assert summary.valid == 1 and summary.invalid == 1

    good = db_session.scalar(select(Coupon).where(Coupon.code == "GOOD"))
    bad = db_session.scalar(select(Coupon).where(Coupon.code == "BAD"))
    assert good.status is CouponStatus.valid
    assert bad.status is CouponStatus.invalid
