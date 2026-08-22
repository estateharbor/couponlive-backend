"""Pydantic schemas: ingestion input, API responses, validation results.

`RawCoupon` is the structured contract every scraper/ingestor emits, before
normalization and dedup. Keeping it separate from the ORM model means a
scraper never has to know anything about the database.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from models.enums import (
    CouponStatus,
    DiscountType,
    IngestionMethod,
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


class MerchantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    normalized_name: str
    website: str | None
    coupon_count: int = 0
    valid_coupon_count: int = 0


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
