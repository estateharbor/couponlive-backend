"""add coupons.expires_at (the merchant's stated end date)

Hand-checked editorial codes often carry a real end date ("valid till 31 Dec
2026"). Store it so such a code stays listed until then — instead of falling
to the 14-day editorial staleness window — and is expired once it passes.

Revision ID: 0010_coupon_expires_at
Revises: 0009_coupon_unverifiable_streak
Create Date: 2026-10-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010_coupon_expires_at"
down_revision: Union[str, None] = "0009_coupon_unverifiable_streak"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("coupons", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("coupons", "expires_at")
