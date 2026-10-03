"""add sources.last_error / last_error_at

Feed syncs that raised (or authenticated but returned nothing) left no trace
anywhere visible — LinkMyDeals went quiet for 12h with /health showing only an
old last_scraped_at. Record the latest failure on the source so /health shows it.

Revision ID: 0011_source_last_error
Revises: 0010_coupon_expires_at
Create Date: 2026-10-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0011_source_last_error"
down_revision: Union[str, None] = "0010_coupon_expires_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("last_error", sa.Text(), nullable=True))
    op.add_column("sources", sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("sources", "last_error_at")
    op.drop_column("sources", "last_error")
