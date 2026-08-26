"""add sources.sync_cursor for incremental feeds (e.g. LinkMyDeals last_extract)

Revision ID: 0003_sync_cursor
Revises: 0002_offer_codeless
Create Date: 2026-08-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_sync_cursor"
down_revision: Union[str, None] = "0002_offer_codeless"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("sync_cursor", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("sources", "sync_cursor")
