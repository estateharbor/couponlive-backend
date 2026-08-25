"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Use the PostgreSQL ENUM with create_type=False: we create these types ONCE,
# explicitly (checkfirst) in upgrade(). On generic sa.Enum the create_type flag
# is ignored, so create_table() re-emits CREATE TYPE and Postgres rejects the
# duplicate ("type ... already exists"). postgresql.ENUM honors it.
coupon_status = postgresql.ENUM(
    "unverified", "valid", "invalid", "expired", name="coupon_status", create_type=False
)
discount_type = postgresql.ENUM(
    "percentage", "fixed", "free_shipping", "bogo", "cashback", "unknown",
    name="discount_type", create_type=False,
)
validation_result = postgresql.ENUM(
    "valid", "invalid", "unverifiable", name="validation_result", create_type=False
)
ingestion_method = postgresql.ENUM(
    "affiliate_api", "scrape_requests", "scrape_playwright",
    name="ingestion_method", create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    coupon_status.create(bind, checkfirst=True)
    discount_type.create(bind, checkfirst=True)
    validation_result.create(bind, checkfirst=True)
    ingestion_method.create(bind, checkfirst=True)

    op.create_table(
        "merchants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("normalized_name", sa.String(255), nullable=False),
        sa.Column("website", sa.String(512)),
        sa.Column("checkout_url_pattern", sa.String(512)),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("normalized_name", name="uq_merchant_normalized_name"),
    )
    op.create_index("ix_merchants_normalized_name", "merchants", ["normalized_name"])

    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("base_url", sa.String(512)),
        sa.Column("scrape_frequency_minutes", sa.Integer(), nullable=False, server_default="360"),
        sa.Column("scraper_class", sa.String(255)),
        sa.Column("ingestion_method", ingestion_method, nullable=False, server_default="scrape_requests"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_scraped_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_rate", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_source_name"),
    )

    op.create_table(
        "coupons",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("merchant_id", sa.Integer(), sa.ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(128), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("discount_type", discount_type, nullable=False, server_default="unknown"),
        sa.Column("discount_value", sa.Numeric(12, 2)),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", coupon_status, nullable=False, server_default="unverified"),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("last_validated_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("merchant_id", "code", name="uq_coupon_merchant_code"),
        sa.CheckConstraint("confidence_score >= 0.0 AND confidence_score <= 1.0", name="ck_coupon_confidence_range"),
    )
    op.create_index("ix_coupon_status", "coupons", ["status"])
    op.create_index("ix_coupon_last_validated_at", "coupons", ["last_validated_at"])
    op.create_index(
        "ix_coupon_serve",
        "coupons",
        ["merchant_id", "status", "last_validated_at", "confidence_score"],
    )

    op.create_table(
        "coupon_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("coupon_id", sa.Integer(), sa.ForeignKey("coupons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_url", sa.String(1024)),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("coupon_id", "source_id", name="uq_coupon_source"),
    )
    op.create_index("ix_coupon_source_source", "coupon_sources", ["source_id"])

    op.create_table(
        "validation_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("coupon_id", sa.Integer(), sa.ForeignKey("coupons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", validation_result, nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("response_snapshot", sa.Text()),
    )
    op.create_index("ix_validation_log_coupon", "validation_logs", ["coupon_id", "validated_at"])

    op.create_table(
        "user_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("coupon_id", sa.Integer(), sa.ForeignKey("coupons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("worked", sa.Boolean(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip_hash", sa.String(64)),
    )
    op.create_index("ix_feedback_coupon", "user_feedback", ["coupon_id", "submitted_at"])
    op.create_index("ix_user_feedback_ip_hash", "user_feedback", ["ip_hash"])


def downgrade() -> None:
    op.drop_table("user_feedback")
    op.drop_table("validation_logs")
    op.drop_table("coupon_sources")
    op.drop_index("ix_coupon_serve", table_name="coupons")
    op.drop_index("ix_coupon_last_validated_at", table_name="coupons")
    op.drop_index("ix_coupon_status", table_name="coupons")
    op.drop_table("coupons")
    op.drop_table("sources")
    op.drop_index("ix_merchants_normalized_name", table_name="merchants")
    op.drop_table("merchants")

    bind = op.get_bind()
    for enum in (coupon_status, discount_type, validation_result, ingestion_method):
        enum.drop(bind, checkfirst=True)
