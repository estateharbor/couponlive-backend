"""Pydantic schemas: ingestion input, API responses, validation results.

`RawCoupon` is the structured contract every scraper/ingestor emits, before
normalization and dedup. Keeping it separate from the ORM model means a
scraper never has to know anything about the database.
"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from models.enums import (
    CouponStatus,
    DiscountType,
    IngestionMethod,
    TrialOfferType,
    TrialReminderStatus,
    TrialVerificationStatus,
    ValidationResultEnum,
)


# --- Ingestion contract (scrapers & affiliate ingestors emit this) -------
class RawCoupon(BaseModel):
    """Structured output of a single scraped/ingested coupon, pre-normalization.

    `code` is optional: many aggregators reveal-gate the code behind an
    affiliate redirect, so a scraped record is really an *offer* whose code
    may be absent (`requires_reveal=True`) until an affiliate API supplies it.
    `external_ref` is the source's own stable id for the offer and is the
    dedup identity when no code is present.
    """

    merchant_name: str
    code: str | None = None
    external_ref: str | None = None
    requires_reveal: bool = False
    description: str | None = None
    discount_type: DiscountType = DiscountType.unknown
    discount_value: float | None = None
    source_url: str | None = None
    scraped_at: datetime
    ingestion_method: IngestionMethod = IngestionMethod.scrape_requests


# --- Validation contract (validators return this) ------------------------
class ValidationResult(BaseModel):
    result: ValidationResultEnum
    error_message: str | None = None
    response_snapshot: str | None = None
    checked_at: datetime


# --- API response schemas ------------------------------------------------
class CouponOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    merchant_id: int
    merchant_name: str | None = None
    code: str | None
    requires_reveal: bool = False
    description: str | None
    discount_type: DiscountType
    discount_value: float | None
    status: CouponStatus
    confidence_score: float
    first_seen: datetime
    last_seen: datetime
    last_validated_at: datetime | None
    url: str | None = None  # affiliate deeplink for the click-out (earns commission)
    # Crowd feedback tallies so the client can show an HONEST denominator
    # ("3 of 4 said it worked") instead of a bare percentage with no sample size.
    feedback_up: int = 0
    feedback_total: int = 0


class DealOut(BaseModel):
    """A code-less offer (e.g. an Amazon deal). Unlike a coupon it carries no
    code to copy and is never checkout-verified; the CTA is the affiliate `url`."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    merchant_id: int
    merchant_name: str | None = None
    description: str | None
    discount_type: DiscountType
    discount_value: float | None
    url: str | None = None
    last_seen: datetime


class MerchantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    normalized_name: str
    website: str | None
    coupon_count: int = 0
    valid_coupon_count: int = 0
    updated_at: datetime | None = None  # last time this merchant row changed (sitemap lastmod)


# --- Free Trials vertical ------------------------------------------------
class TrialOfferOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    offer_type: TrialOfferType
    title: str
    trial_days: int | None
    credit_amount: float | None
    credit_currency: str | None
    card_required: bool | None
    india_available: bool | None
    eligibility: str | None
    auto_renews: bool | None
    renew_price_inr: float | None
    renew_price_usd: float | None
    renew_period: str | None
    signup_url: str
    cancel_url: str | None
    how_to_claim: str | None
    expires_at: datetime | None
    confidence_score: float
    last_verified_at: datetime | None
    last_verified_from: str | None
    verification_status: TrialVerificationStatus


class ToolOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    vendor_name: str | None = None
    tagline: str | None = None
    description: str | None = None
    website_url: str | None = None
    logo_url: str | None = None
    category: str | None = None
    is_ai_tool: bool = False
    platforms: str | None = None
    popularity_score: float = 0.0
    offers: list[TrialOfferOut] = []


class TrialCardOut(BaseModel):
    """One offer flattened with its tool summary — the unit the grid renders."""

    id: int                       # offer id
    tool_name: str
    tool_slug: str
    logo_url: str | None = None
    category: str | None = None
    is_ai_tool: bool = False
    offer_type: TrialOfferType
    title: str
    trial_days: int | None = None
    credit_amount: float | None = None
    credit_currency: str | None = None
    card_required: bool | None = None
    india_available: bool | None = None
    eligibility: str | None = None
    renew_price_inr: float | None = None
    renew_price_usd: float | None = None
    renew_period: str | None = None
    signup_url: str
    confidence_score: float = 0.0
    last_verified_at: datetime | None = None
    last_verified_from: str | None = None
    verification_status: TrialVerificationStatus
    expires_at: datetime | None = None


class ReminderIn(BaseModel):
    """Create a cancel-reminder. Either `ends_on` OR (`started_on` + `trial_days`)
    must resolve to an end date. `consent` must be true (DPDP)."""

    email: str
    tool_name: str
    offer_id: int | None = None
    ends_on: date | None = None
    started_on: date | None = None
    trial_days: int | None = None
    renew_price_inr: float | None = None
    renew_note: str | None = None
    cancel_url: str | None = None
    remind_days_before: list[int] | None = None
    consent: bool = False


class ReminderOut(BaseModel):
    token: str
    tool_name: str
    ends_on: date
    status: TrialReminderStatus
    remind_days_before: list[int]
    manage_url: str | None = None


class FeedbackIn(BaseModel):
    worked: bool


class FeedbackOut(BaseModel):
    coupon_id: int
    recorded: bool
    new_confidence_score: float


# --- Health / ops schemas (Phase 6) --------------------------------------
class SourceHealth(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    is_active: bool
    ingestion_method: IngestionMethod
    last_scraped_at: datetime | None
    last_success_at: datetime | None
    last_success_rate: float | None


class HealthOut(BaseModel):
    status: str = Field("ok")
    sources: list[SourceHealth] = []
    total_valid_coupons: int = 0
    total_coupons: int = 0
