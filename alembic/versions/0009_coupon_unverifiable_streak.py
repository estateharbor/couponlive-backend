"""add coupons.unverifiable_streak for the re-validation circuit-breaker

A code that keeps coming back "unverifiable" (the merchant blocks our checkout
validator) was being re-tried on every sweep forever. Track the consecutive
inconclusive streak so the scheduler can back such codes off to a long cooldown.

Revision ID: 0009_coupon_unverifiable_streak
Revises: 0008_coupon_last_checked
Create Date: 2026-10-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0009_coupon_unverifiable_streak"
down_revision: Union[str, None] = "0008_coupon_last_checked"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "coupons",
        sa.Column("unverifiable_streak", sa.Integer(), nullable=False, server_default="0"),
    )
    # Drop the server_default now that existing rows are backfilled to 0 — the
    # app sets it explicitly from here on.
    op.alter_column("coupons", "unverifiable_streak", server_default=None)


def downgrade() -> None:
    op.drop_column("coupons", "unverifiable_streak")
