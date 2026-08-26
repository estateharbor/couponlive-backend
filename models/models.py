"""SQLAlchemy ORM models for CouponLive.

Schema goals:
- One coupon per (merchant, code), with provenance tracked across the many
  source sites that listed it (coupon_sources).
- Hot query paths indexed: unique (merchant_id, code), status,
  last_validated_at (the default API filter), and merchant lookups.
- Ingestion method is first-class on `sources` so the affiliate-API path is
  a clean swap-in, not a bolt-on to a scrape-only design.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, TimestampMixin
from models.enums import (
    CouponStatus,
    DiscountType,
    IngestionMethod,
    ValidationResultEnum,
)


class Merchant(Base, TimestampMixin):
    __tablename__ = "merchants"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # normalized_name: lowercased, stripped, punctuation-collapsed — the key
    # fuzzy dedup matches against. Unique so two spellings converge to one row.
    normalized_name: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
    website: Mapped[str | None] = mapped_column(String(512))
    # Pattern for locating the checkout/cart page during validation (Phase 4).
    checkout_url_pattern: Mapped[str | None] = mapped_column(String(512))
    # Higher = revalidate more aggressively (top-traffic merchants).
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    coupons: Mapped[list["Coupon"]] = relationship(
        back_populates="merchant", cascade="all, delete-orphan"
    )


class Source(Base, TimestampMixin):
    """An aggregator site (or affiliate API) we ingest coupons from."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    base_url: Mapped[str | None] = mapped_column(String(512))
    scrape_frequency_minutes: Mapped[int] = mapped_column(
        Integer, default=360, nullable=False
    )
    # Dotted path or registry key of the scraper/ingestor implementation.
    scraper_class: Mapped[str | None] = mapped_column(String(255))
    ingestion_method: Mapped[IngestionMethod] = mapped_column(
        SAEnum(IngestionMethod, name="ingestion_method"),
        default=IngestionMethod.scrape_requests,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_scraped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Rolling health signal for Phase 6 alerting (0.0–1.0).
    last_success_rate: Mapped[float | None] = mapped_column(Float)
    # Incremental-feed cursor (e.g. LinkMyDeals last_extract unix timestamp).
    # Stored as text to stay source-agnostic; None => next run is a full pull.
    sync_cursor: Mapped[str | None] = mapped_column(String(64))

    coupon_links: Mapped[list["CouponSource"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class Coupon(Base, TimestampMixin):
    __tablename__ = "coupons"

    id: Mapped[int] = mapped_column(primary_key=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    # Nullable: many aggregators reveal-gate the code behind an affiliate
    # redirect, so a scraped OFFER may have no code yet (filled later from an
    # affiliate API or a validation pass). Normalized upper/stripped when set.
    code: Mapped[str | None] = mapped_column(String(128))
    # Source-side stable id for the offer (e.g. Desidime coupon_id "551335").
    # Used as the dedup/identity key when `code` is absent.
    external_ref: Mapped[str | None] = mapped_column(String(128))
    # True when the code exists but is reveal-gated at the source (not scraped).
    requires_reveal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    discount_type: Mapped[DiscountType] = mapped_column(
        SAEnum(DiscountType, name="discount_type"),
        default=DiscountType.unknown,
        nullable=False,
    )
    # Numeric so 20 (percent) or 200 (rupees) both fit; interpret w/ discount_type.
    discount_value: Mapped[float | None] = mapped_column(Numeric(12, 2))

    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    status: Mapped[CouponStatus] = mapped_column(
        SAEnum(CouponStatus, name="coupon_status"),
        default=CouponStatus.unverified,
        nullable=False,
    )
    # 0.0–1.0. Blend of validation history + crowd feedback + recency.
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    merchant: Mapped["Merchant"] = relationship(back_populates="coupons")
    sources: Mapped[list["CouponSource"]] = relationship(
        back_populates="coupon", cascade="all, delete-orphan"
    )
    validation_logs: Mapped[list["ValidationLog"]] = relationship(
        back_populates="coupon", cascade="all, delete-orphan"
    )
    feedback: Mapped[list["UserFeedback"]] = relationship(
        back_populates="coupon", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # Partial uniqueness: one row per (merchant, code) when a code exists,
        # and separately one row per (merchant, external_ref) for code-less
        # offers. Partial so many NULL-code offers per merchant stay legal.
        Index(
            "uq_coupon_merchant_code",
            "merchant_id",
            "code",
            unique=True,
            postgresql_where=text("code IS NOT NULL"),
            sqlite_where=text("code IS NOT NULL"),
        ),
        Index(
            "uq_coupon_merchant_extref",
            "merchant_id",
            "external_ref",
            unique=True,
            postgresql_where=text("code IS NULL AND external_ref IS NOT NULL"),
            sqlite_where=text("code IS NULL AND external_ref IS NOT NULL"),
        ),
        Index("ix_coupon_status", "status"),
        Index("ix_coupon_last_validated_at", "last_validated_at"),
        # Composite covering the default API query: valid + fresh, per merchant,
        # ordered by confidence then recency.
        Index(
            "ix_coupon_serve",
            "merchant_id",
            "status",
            "last_validated_at",
            "confidence_score",
        ),
        CheckConstraint(
            "confidence_score >= 0.0 AND confidence_score <= 1.0",
            name="ck_coupon_confidence_range",
        ),
    )


class CouponSource(Base, TimestampMixin):
    """M:N provenance: which source sites listed a given coupon, and when.

    Keeps first/last-seen *per source* so we can tell which aggregator is the
    freshest, and audit where a code originated.
    """

    __tablename__ = "coupon_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    coupon_id: Mapped[int] = mapped_column(
        ForeignKey("coupons.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    source_url: Mapped[str | None] = mapped_column(String(1024))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    coupon: Mapped["Coupon"] = relationship(back_populates="sources")
    source: Mapped["Source"] = relationship(back_populates="coupon_links")

    __table_args__ = (
        UniqueConstraint("coupon_id", "source_id", name="uq_coupon_source"),
        Index("ix_coupon_source_source", "source_id"),
    )


class ValidationLog(Base):
    """One row per validation attempt (append-only audit trail)."""

    __tablename__ = "validation_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    coupon_id: Mapped[int] = mapped_column(
        ForeignKey("coupons.id", ondelete="CASCADE"), nullable=False
    )
    validated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    result: Mapped[ValidationResultEnum] = mapped_column(
        SAEnum(ValidationResultEnum, name="validation_result"), nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    # Small snapshot (parsed discount line, error text) — NOT full page HTML.
    response_snapshot: Mapped[str | None] = mapped_column(Text)

    coupon: Mapped["Coupon"] = relationship(back_populates="validation_logs")

    __table_args__ = (
        Index("ix_validation_log_coupon", "coupon_id", "validated_at"),
    )


class UserFeedback(Base):
    """Crowdsourced worked/didn't-work signal, feeding confidence_score."""

    __tablename__ = "user_feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    coupon_id: Mapped[int] = mapped_column(
        ForeignKey("coupons.id", ondelete="CASCADE"), nullable=False
    )
    worked: Mapped[bool] = mapped_column(Boolean, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Hash of the submitter IP (never the raw IP) — basic abuse throttling only.
    ip_hash: Mapped[str | None] = mapped_column(String(64), index=True)

    coupon: Mapped["Coupon"] = relationship(back_populates="feedback")

    __table_args__ = (
        Index("ix_feedback_coupon", "coupon_id", "submitted_at"),
    )
