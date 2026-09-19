"""Select newly-verified coupons + trials and post them to the Telegram channel.

Posts link back to couponlive.in (not the raw affiliate URL) with utm_source=
telegram — so the channel drives traffic to the SITE, where the click then earns.
`send` / `is_posted` / `mark_posted` are injected so this is unit-testable and so
production can use Redis dedupe + the Bot API."""
from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import desc, select
from sqlalchemy.orm import Session, selectinload

from core.config import get_settings
from core.logging import get_logger
from models.enums import CouponStatus, DiscountType, TrialStatus, TrialVerificationStatus
from models.models import Coupon, Merchant, Tool, TrialOffer

log = get_logger("telegram.poster")


def _discount_label(dtype: DiscountType, value) -> str:
    v = None
    try:
        v = int(value) if value is not None and float(value).is_integer() else (float(value) if value is not None else None)
    except (TypeError, ValueError):
        v = None
    if dtype is DiscountType.percentage:
        return f"{v}% Off" if v is not None else "% Off"
    if dtype is DiscountType.fixed:
        return f"₹{v} Off" if v is not None else "₹ Off"
    if dtype is DiscountType.free_shipping:
        return "Free Shipping"
    if dtype is DiscountType.cashback:
        return f"{v}% Cashback" if v is not None else "Cashback"
    if dtype is DiscountType.bogo:
        return "Buy 1 Get 1"
    return "Deal"


def build_coupon_post(merchant: str, discount: str, code: str, url: str) -> str:
    return (
        f"🔥 <b>{merchant}</b> — {discount}\n"
        f"✅ Verified code: <code>{code}</code>\n"
        f"👉 <a href=\"{url}\">Reveal & use it</a>"
    )


def build_trial_post(tool: str, title: str, url: str) -> str:
    return (
        f"🆓 <b>{tool}</b> free trial — ✅ Verified\n"
        f"{title}\n"
        f"👉 <a href=\"{url}\">Start the trial</a>"
    )


Sender = Callable[[str], bool]
Seen = Callable[[str], bool]
Mark = Callable[[str], None]


def post_new(
    session: Session, *, send: Sender, is_posted: Seen, mark_posted: Mark, limit: int = 5
) -> dict:
    base = get_settings().app_url.rstrip("/")
    utm = "utm_source=telegram&utm_medium=channel"
    posted = 0
    items: list[str] = []

    # Verified coupons (freshest first).
    coupons = session.scalars(
        select(Coupon).join(Merchant).options(selectinload(Coupon.merchant))
        .where(Coupon.status == CouponStatus.valid, Coupon.code.is_not(None))
        .order_by(desc(Coupon.last_validated_at)).limit(40)
    ).all()
    for c in coupons:
        if posted >= limit:
            break
        key = f"coupon:{c.id}"
        if is_posted(key):
            continue
        slug = c.merchant.normalized_name
        url = f"{base}/store/{slug}/?{utm}"
        text = build_coupon_post(c.merchant.name, _discount_label(c.discount_type, c.discount_value), c.code, url)
        if send(text):
            mark_posted(key)
            posted += 1
            items.append(key)

    # Verified trials (freshest first).
    trials = session.scalars(
        select(TrialOffer).join(Tool).options(selectinload(TrialOffer.tool))
        .where(
            TrialOffer.status == TrialStatus.live,
            TrialOffer.verification_status == TrialVerificationStatus.verified,
        )
        .order_by(desc(TrialOffer.last_verified_at)).limit(40)
    ).all()
    for t in trials:
        if posted >= limit:
            break
        key = f"trial:{t.id}"
        if is_posted(key):
            continue
        url = f"{base}/tool/{t.tool.slug}/?{utm}"
        text = build_trial_post(t.tool.name, t.title, url)
        if send(text):
            mark_posted(key)
            posted += 1
            items.append(key)

    log.info("telegram.post_new_done", posted=posted)
    return {"posted": posted, "items": items}
