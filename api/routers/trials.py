"""/trials + /tools endpoints: the Free Trials vertical.

Honest by design (same bar as coupons): a NULL fact renders as "Unknown", and
only offers that passed a live verification run carry the `verified` badge.
Broken/expired offers are hidden from default listings.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, desc, func, or_, select
from sqlalchemy.orm import Session, selectinload

from api.deps import get_db
from models.base import utcnow
from models.enums import TrialOfferType, TrialStatus, TrialVerificationStatus
from models.models import Tool, TrialOffer
from models.schemas import ToolOut, TrialCardOut, TrialOfferOut

router = APIRouter(tags=["trials"])

# Offers we never show in default lists.
_HIDDEN = (TrialVerificationStatus.broken, TrialVerificationStatus.expired)
# Verified first, then likely_active, then unverified.
_STATUS_RANK = case(
    (TrialOffer.verification_status == TrialVerificationStatus.verified, 0),
    (TrialOffer.verification_status == TrialVerificationStatus.likely_active, 1),
    else_=2,
)


def _card(o: TrialOffer) -> TrialCardOut:
    return TrialCardOut(
        id=o.id,
        tool_name=o.tool.name,
        tool_slug=o.tool.slug,
        logo_url=o.tool.logo_url,
        category=o.tool.category,
        is_ai_tool=o.tool.is_ai_tool,
        offer_type=o.offer_type,
        title=o.title,
        trial_days=o.trial_days,
        credit_amount=float(o.credit_amount) if o.credit_amount is not None else None,
        credit_currency=o.credit_currency,
        card_required=o.card_required,
        india_available=o.india_available,
        eligibility=o.eligibility,
        renew_price_inr=float(o.renew_price_inr) if o.renew_price_inr is not None else None,
        renew_price_usd=float(o.renew_price_usd) if o.renew_price_usd is not None else None,
        renew_period=o.renew_period,
        signup_url=o.signup_url,
        confidence_score=o.confidence_score,
        last_verified_at=o.last_verified_at,
        last_verified_from=o.last_verified_from,
        verification_status=o.verification_status,
        expires_at=o.expires_at,
    )


@router.get("/trials", response_model=list[TrialCardOut])
def list_trials(
    db: Session = Depends(get_db),
    q: str | None = Query(None, description="Search tool name / offer title"),
    category: str | None = Query(None),
    offer_type: TrialOfferType | None = Query(None),
    no_card: bool = Query(False, description="Only offers that need no card"),
    min_days: int | None = Query(None, ge=1, description="Minimum trial length in days"),
    india: bool = Query(False, description="Only India-available offers"),
    ai: bool = Query(False, description="Only AI tools"),
    sort: str = Query("recommended", pattern="^(recommended|newest|longest|expiring|verified)$"),
    limit: int = Query(60, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    stmt = (
        select(TrialOffer)
        .join(Tool)
        .options(selectinload(TrialOffer.tool))
        .where(
            TrialOffer.status == TrialStatus.live,
            Tool.status == TrialStatus.live,
            TrialOffer.verification_status.not_in(_HIDDEN),
        )
    )
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Tool.name.ilike(like), TrialOffer.title.ilike(like)))
    if category:
        stmt = stmt.where(Tool.category == category)
    if offer_type:
        stmt = stmt.where(TrialOffer.offer_type == offer_type)
    if no_card:
        stmt = stmt.where(TrialOffer.card_required.is_(False))
    if min_days is not None:
        stmt = stmt.where(TrialOffer.trial_days.is_not(None), TrialOffer.trial_days >= min_days)
    if india:
        stmt = stmt.where(TrialOffer.india_available.is_(True))
    if ai:
        stmt = stmt.where(Tool.is_ai_tool.is_(True))

    if sort == "newest":
        stmt = stmt.order_by(desc(TrialOffer.created_at))
    elif sort == "longest":
        stmt = stmt.order_by(desc(TrialOffer.trial_days).nulls_last())
    elif sort == "expiring":
        stmt = stmt.where(TrialOffer.expires_at.is_not(None)).order_by(TrialOffer.expires_at.asc())
    elif sort == "verified":
        stmt = stmt.order_by(_STATUS_RANK, desc(TrialOffer.last_verified_at).nulls_last())
    else:  # recommended: status, then confidence × popularity
        stmt = stmt.order_by(
            _STATUS_RANK,
            desc(TrialOffer.confidence_score + func.coalesce(Tool.popularity_score, 0)),
            desc(TrialOffer.created_at),
        )

    offers = db.scalars(stmt.limit(limit).offset(offset)).all()
    return [_card(o) for o in offers]


@router.get("/trials/collections/{key}", response_model=list[TrialCardOut])
def trial_collection(
    key: str,
    db: Session = Depends(get_db),
    limit: int = Query(12, ge=1, le=50),
):
    """Curated homepage-style rows: trending / new / expiring / no-card / ai / student / startup."""
    base = (
        select(TrialOffer).join(Tool).options(selectinload(TrialOffer.tool)).where(
            TrialOffer.status == TrialStatus.live,
            Tool.status == TrialStatus.live,
            TrialOffer.verification_status.not_in(_HIDDEN),
        )
    )
    if key == "new":
        stmt = base.order_by(desc(TrialOffer.created_at))
    elif key == "expiring":
        soon = utcnow() + timedelta(days=14)
        stmt = base.where(
            TrialOffer.expires_at.is_not(None), TrialOffer.expires_at <= soon,
            TrialOffer.expires_at >= utcnow(),
        ).order_by(TrialOffer.expires_at.asc())
    elif key in ("no-card", "no_card"):
        stmt = base.where(TrialOffer.card_required.is_(False)).order_by(_STATUS_RANK, desc(TrialOffer.confidence_score))
    elif key == "ai":
        stmt = base.where(Tool.is_ai_tool.is_(True)).order_by(_STATUS_RANK, desc(TrialOffer.confidence_score))
    elif key in ("student", "student_offer"):
        stmt = base.where(TrialOffer.offer_type == TrialOfferType.student_offer).order_by(desc(TrialOffer.confidence_score))
    elif key in ("startup", "startup_credit"):
        stmt = base.where(TrialOffer.offer_type == TrialOfferType.startup_credit).order_by(desc(TrialOffer.confidence_score))
    else:  # trending / default
        stmt = base.order_by(_STATUS_RANK, desc(TrialOffer.confidence_score + func.coalesce(Tool.popularity_score, 0)))

    return [_card(o) for o in db.scalars(stmt.limit(limit)).all()]


@router.get("/trial-categories")
def trial_categories(db: Session = Depends(get_db)):
    """Distinct categories with a live-offer count, for the filter UI + hubs."""
    rows = db.execute(
        select(Tool.category, func.count(TrialOffer.id))
        .join(TrialOffer, TrialOffer.tool_id == Tool.id)
        .where(
            Tool.category.is_not(None),
            TrialOffer.status == TrialStatus.live,
            TrialOffer.verification_status.not_in(_HIDDEN),
        )
        .group_by(Tool.category)
        .order_by(desc(func.count(TrialOffer.id)))
    ).all()
    return [{"category": c, "offer_count": n} for c, n in rows]


@router.get("/tools/{slug}", response_model=ToolOut)
def get_tool(slug: str, db: Session = Depends(get_db)):
    tool = db.scalar(
        select(Tool).options(selectinload(Tool.offers)).where(Tool.slug == slug)
    )
    if tool is None or tool.status is not TrialStatus.live:
        raise HTTPException(status_code=404, detail="tool not found")
    # Build the response WITHOUT mutating the ORM relationship (delete-orphan
    # cascade would otherwise delete the offers we filter out on flush).
    out = ToolOut.model_validate(tool)
    out.offers = [
        TrialOfferOut.model_validate(o)
        for o in sorted(tool.offers, key=lambda o: o.confidence_score, reverse=True)
        if o.status is TrialStatus.live and o.verification_status not in _HIDDEN
    ]
    return out
