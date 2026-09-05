"""widen coupon_sources.source_url to TEXT (long affiliate tracking URLs)

Cuelinks/affiliate deeplinks can exceed varchar(1024) — e.g. a TataCliq search
deeplink is ~1080 chars — which raised StringDataRightTruncation and dropped the
offer. Store the URL as unbounded TEXT so no offer is lost to URL length.

Revision ID: 0005_widen_source_url
Revises: 0004_reset_trust
Create Date: 2026-09-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_widen_source_url"
down_revision: Union[str, None] = "0004_reset_trust"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "coupon_sources",
        "source_url",
        existing_type=sa.String(length=1024),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    # Note: rows longer than 1024 chars would fail this downcast; acceptable for a
    # rollback since such rows only exist because of this widening.
    op.alter_column(
        "coupon_sources",
        "source_url",
        existing_type=sa.Text(),
        type_=sa.String(length=1024),
        existing_nullable=True,
    )
