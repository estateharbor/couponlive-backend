"""split coupon last_checked_at from last_validated_at

An inconclusive re-check used to overwrite last_validated_at + confidence_score,
so a previously-confirmed code showed "Verified {ago}" alongside "Low
confidence". Add last_checked_at (any attempt) so last_validated_at can mean
"last CONFIRMED valid" again, and heal the codes an inconclusive check tainted.

Revision ID: 0008_coupon_last_checked
Revises: 0007_trial_reminders
Create Date: 2026-09-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008_coupon_last_checked"
down_revision: Union[str, None] = "0007_trial_reminders"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "coupons",
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Seed last_checked_at from the existing last_validated_at (best available
    # signal for "last attempt" on rows that predate the split).
    op.execute("UPDATE coupons SET last_checked_at = last_validated_at")

    # Heal rows an inconclusive re-check dragged down: a code still marked valid,
    # with no crowd feedback, should carry the valid confidence base (0.85), not
    # the 0.40 unverifiable floor. Only touch valid + feedback-free rows so we
    # never override a score that real negative feedback earned.
    op.execute(
        """
        UPDATE coupons
        SET confidence_score = 0.85
        WHERE status = 'valid'
          AND confidence_score < 0.85
          AND id NOT IN (SELECT DISTINCT coupon_id FROM user_feedback)
        """
    )


def downgrade() -> None:
    op.drop_column("coupons", "last_checked_at")
