"""make coupon.code nullable + add external_ref / requires_reveal for reveal-gated offers

Rationale: aggregators (e.g. Desidime) reveal-gate the actual coupon code
behind an affiliate redirect, so a scraped record is an OFFER whose code may be
absent until an affiliate API supplies it. This migration lets us store those
offers, identified by (merchant, external_ref) instead of (merchant, code).

Revision ID: 0002_offer_codeless
Revises: 0001_initial
Create Date: 2026-08-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_offer_codeless"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("coupons", sa.Column("external_ref", sa.String(128)))
    op.add_column(
        "coupons",
        sa.Column("requires_reveal", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("coupons", "code", existing_type=sa.String(128), nullable=True)

    # Replace the old full unique constraint with two partial unique indexes.
    op.drop_constraint("uq_coupon_merchant_code", "coupons", type_="unique")
    op.create_index(
        "uq_coupon_merchant_code",
        "coupons",
        ["merchant_id", "code"],
        unique=True,
        postgresql_where=sa.text("code IS NOT NULL"),
    )
    op.create_index(
        "uq_coupon_merchant_extref",
        "coupons",
        ["merchant_id", "external_ref"],
        unique=True,
        postgresql_where=sa.text("code IS NULL AND external_ref IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_coupon_merchant_extref", table_name="coupons")
    op.drop_index("uq_coupon_merchant_code", table_name="coupons")
    op.create_unique_constraint("uq_coupon_merchant_code", "coupons", ["merchant_id", "code"])
    op.alter_column("coupons", "code", existing_type=sa.String(128), nullable=False)
    op.drop_column("coupons", "requires_reveal")
    op.drop_column("coupons", "external_ref")
