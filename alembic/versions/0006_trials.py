"""free-trials vertical: tools + trial_offers

Revision ID: 0006_trials
Revises: 0005_widen_source_url
Create Date: 2026-09-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0006_trials"
down_revision: Union[str, None] = "0005_widen_source_url"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# postgresql.ENUM(create_type=False) + explicit checkfirst create — same pattern
# as 0001, so create_table() never re-emits CREATE TYPE (the double-create bug).
trial_status = postgresql.ENUM(
    "live", "hidden", "pending_review", "archived", name="trial_status", create_type=False
)
trial_offer_type = postgresql.ENUM(
    "no_card_trial", "card_trial", "freemium_premium_trial", "extended_trial",
    "startup_credit", "student_offer", "telecom_bundle", "bank_card_offer",
    "ai_credits", "lifetime_free_tier", "unknown",
    name="trial_offer_type", create_type=False,
)
trial_verification_status = postgresql.ENUM(
    "verified", "likely_active", "unverified", "broken", "expired",
    name="trial_verification_status", create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    trial_status.create(bind, checkfirst=True)
    trial_offer_type.create(bind, checkfirst=True)
    trial_verification_status.create(bind, checkfirst=True)

    op.create_table(
        "tools",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("vendor_name", sa.String(255)),
        sa.Column("tagline", sa.String(512)),
        sa.Column("description", sa.Text()),
        sa.Column("website_url", sa.Text()),
        sa.Column("pricing_page_url", sa.Text()),
        sa.Column("logo_url", sa.Text()),
        sa.Column("category", sa.String(64)),
        sa.Column("tags", sa.Text()),
        sa.Column("is_ai_tool", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("platforms", sa.String(255)),
        sa.Column("base_price_inr", sa.Numeric(12, 2)),
        sa.Column("base_price_usd", sa.Numeric(12, 2)),
        sa.Column("popularity_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", trial_status, nullable=False, server_default="live"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("slug", name="uq_tool_slug"),
    )
    op.create_index("ix_tools_slug", "tools", ["slug"])
    op.create_index("ix_tools_category", "tools", ["category"])

    op.create_table(
        "trial_offers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tool_id", sa.Integer(),
                  sa.ForeignKey("tools.id", ondelete="CASCADE"), nullable=False),
        sa.Column("offer_type", trial_offer_type, nullable=False, server_default="unknown"),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("trial_days", sa.Integer()),
        sa.Column("credit_amount", sa.Numeric(12, 2)),
        sa.Column("credit_currency", sa.String(8)),
        sa.Column("card_required", sa.Boolean()),
        sa.Column("india_available", sa.Boolean()),
        sa.Column("eligibility", sa.String(512)),
        sa.Column("auto_renews", sa.Boolean()),
        sa.Column("renew_price_inr", sa.Numeric(12, 2)),
        sa.Column("renew_price_usd", sa.Numeric(12, 2)),
        sa.Column("renew_period", sa.String(16)),
        sa.Column("signup_url", sa.Text(), nullable=False),
        sa.Column("cancel_url", sa.Text()),
        sa.Column("how_to_claim", sa.Text()),
        sa.Column("source", sa.String(64)),
        sa.Column("source_url", sa.Text()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("content_hash", sa.String(64)),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("last_verified_at", sa.DateTime(timezone=True)),
        sa.Column("last_verified_from", sa.String(8)),
        sa.Column("verification_status", trial_verification_status,
                  nullable=False, server_default="unverified"),
        sa.Column("status", trial_status, nullable=False, server_default="live"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_trial_offers_tool_id", "trial_offers", ["tool_id"])
    op.create_index("ix_trial_offers_offer_type", "trial_offers", ["offer_type"])
    op.create_index("ix_trial_offer_card_required", "trial_offers", ["card_required"])
    op.create_index("ix_trial_offer_expires_at", "trial_offers", ["expires_at"])
    op.create_index("ix_trial_offers_verification_status", "trial_offers", ["verification_status"])


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_table("trial_offers")
    op.drop_table("tools")
    trial_verification_status.drop(bind, checkfirst=True)
    trial_offer_type.drop(bind, checkfirst=True)
    trial_status.drop(bind, checkfirst=True)
