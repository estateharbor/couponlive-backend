"""/coupons endpoints: listing (fresh+valid by default) and crowd feedback."""
from __future__ import annotations

import hashlib
from datetime import timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from core.config import get_settings
from core.confidence import compute_confidence
from models.base import utcnow
from models.enums import CouponStatus, ValidationResultEnum
from models.models import Coupon, Merchant, UserFeedback, ValidationLog
from models.schemas import CouponOut, FeedbackIn, FeedbackOut
from api.deps import get_db
from scrapers.normalize import normalize_merchant_name

router = APIRouter(prefix="/coupons", tags=["coupons"])


@router.get("", response_model=list[CouponOut])
def list_coupons(
    db: Session = Depends(get_db),
    merchant: str | None = Query(None, description="Merchant name (fuzzy-normalized)"),
    status: CouponStatus = Query(CouponStatus.valid),
    include_stale: bool = Query(False, description="Bypass the freshness filter"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Default: `status=valid` AND validated within the freshness window,
    ordered by confidence then recency — i.e. only codes we currently trust."""
    settings = get_settings()
    stmt = select(Coupon).join(Merchant).where(Coupon.status == status)

    if merchant:
        key = normalize_merchant_name(merchant)
        stmt = stmt.where(Merchant.normalized_name == key)

    # Freshness filter only makes sense for "valid"; opt out with include_stale.
    if status is CouponStatus.valid and not include_stale:
        fresh_cutoff = utcnow() - timedelta(hours=settings.serve_freshness_hours)
        stmt = stmt.where(
            Coupon.last_validated_at.is_not(None),
            Coupon.last_validated_at >= fresh_cutoff,
        )

    stmt = stmt.order_by(
        desc(Coupon.confidence_score), desc(Coupon.last_validated_at), desc(Coupon.last_seen)
    ).limit(limit).offset(offset)

    coupons = db.scalars(stmt).all()
    return [_to_out(c) for c in coupons]


def _to_out(c: Coupon) -> CouponOut:
    out = CouponOut.model_validate(c)
    out.merchant_name = c.merchant.name if c.merchant else None
    return out


def _ip_hash(request: Request) -> str:
    settings = get_settings()
    ip = request.client.host if request.client else "unknown"
    return hashlib.sha256(f"{settings.feedback_ip_salt}:{ip}".encode()).hexdigest()


def _latest_validation(db: Session, coupon_id: int) -> ValidationResultEnum | None:
    row = db.scalar(
        select(ValidationLog.result)
        .where(ValidationLog.coupon_id == coupon_id)
        .order_by(desc(ValidationLog.validated_at))
        .limit(1)
    )
    return row


@router.post("/{coupon_id}/feedback", response_model=FeedbackOut)
def submit_feedback(
    coupon_id: int,
    body: FeedbackIn,
    request: Request,
    db: Session = Depends(get_db),
):
    coupon = db.get(Coupon, coupon_id)
    if coupon is None:
        raise HTTPException(status_code=404, detail="coupon not found")

    ip_hash = _ip_hash(request)
    # Basic abuse prevention: one counted vote per IP per coupon per 24h.
    recent = db.scalar(
        select(UserFeedback).where(
            UserFeedback.coupon_id == coupon_id,
            UserFeedback.ip_hash == ip_hash,
            UserFeedback.submitted_at >= utcnow() - timedelta(hours=24),
        )
    )
    recorded = False
    if recent is None:
        db.add(UserFeedback(coupon_id=coupon_id, worked=body.worked,
                            submitted_at=utcnow(), ip_hash=ip_hash))
        db.flush()
        recorded = True

    # Recompute confidence from latest validation + all feedback.
    fbs = db.scalars(select(UserFeedback).where(UserFeedback.coupon_id == coupon_id)).all()
    positive = sum(1 for f in fbs if f.worked)
    coupon.confidence_score = compute_confidence(
        _latest_validation(db, coupon_id), positive, len(fbs)
    )
    db.commit()
    return FeedbackOut(coupon_id=coupon_id, recorded=recorded,
                       new_confidence_score=coupon.confidence_score)
