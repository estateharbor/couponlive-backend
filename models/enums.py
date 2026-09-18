"""Enum types shared across ORM models and Pydantic schemas."""
from __future__ import annotations

from enum import Enum


class CouponStatus(str, Enum):
    """Lifecycle state of a coupon in our system.

    - unverified: scraped/ingested, never validated yet
    - valid:      last validation succeeded (a real discount applied)
    - invalid:    last validation failed (code rejected / no discount)
    - expired:    aged out by the staleness job, or source marked it expired
    """

    unverified = "unverified"
    valid = "valid"
    invalid = "invalid"
    expired = "expired"


class DiscountType(str, Enum):
    percentage = "percentage"        # e.g. 20% off
    fixed = "fixed"                  # e.g. ₹200 off
    free_shipping = "free_shipping"
    bogo = "bogo"                    # buy-one-get-one / bundle
    cashback = "cashback"
    unknown = "unknown"


class ValidationResultEnum(str, Enum):
    """Outcome of a single validation attempt (distinct from CouponStatus)."""

    valid = "valid"
    invalid = "invalid"
    unverifiable = "unverifiable"    # couldn't determine (blocked, timeout, layout change)


class IngestionMethod(str, Enum):
    """How a source's data reaches us. Affiliate APIs are preferred."""

    affiliate_api = "affiliate_api"
    scrape_requests = "scrape_requests"   # requests + BeautifulSoup
    scrape_playwright = "scrape_playwright"  # headless browser (JS/bot-protected)


# --- Free Trials vertical -------------------------------------------------
class TrialOfferType(str, Enum):
    """The kind of free-trial / promo offer (see the TrialLive taxonomy)."""

    no_card_trial = "no_card_trial"                    # full trial, no payment method
    card_trial = "card_trial"                          # trial needs a card / UPI mandate
    freemium_premium_trial = "freemium_premium_trial"  # free plan + timed premium unlock
    extended_trial = "extended_trial"                  # longer-than-standard via partner
    startup_credit = "startup_credit"                  # credits via a startup program
    student_offer = "student_offer"                    # education verification required
    telecom_bundle = "telecom_bundle"                  # bundled with Jio/Airtel/Vi
    bank_card_offer = "bank_card_offer"                # via bank/fintech card
    ai_credits = "ai_credits"                          # free AI usage credits
    lifetime_free_tier = "lifetime_free_tier"          # permanent free tier
    unknown = "unknown"


class TrialVerificationStatus(str, Enum):
    """Honesty ladder for a trial offer, mirroring the coupon side."""

    verified = "verified"            # a live verification run passed recently
    likely_active = "likely_active"  # signals good but not fully confirmed
    unverified = "unverified"        # listed/curated, not yet live-checked
    broken = "broken"                # a check failed (link dead / discontinued)
    expired = "expired"              # past its deadline


class TrialStatus(str, Enum):
    """Publication lifecycle for a tool or trial offer row."""

    live = "live"
    hidden = "hidden"
    pending_review = "pending_review"
    archived = "archived"
