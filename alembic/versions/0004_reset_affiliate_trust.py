"""reset coupons falsely marked valid by the removed affiliate-trust shortcut

The old `_apply_affiliate_trust` promoted affiliate-feed coupons straight to
status=valid with a static confidence of 0.7 (never checkout-tested). Those rows
are misleading users right now, so reset any coupon still in that exact state —
status=valid, confidence ~0.7, and NO validation_log (i.e. never actually
validated) — back to the honest unverified default so they re-enter the real
validation queue. Genuinely checkout-validated coupons (which have a
validation_log and a computed confidence like 0.85) are untouched.

Revision ID: 0004_reset_trust
Revises: 0003_sync_cursor
Create Date: 2026-08-27
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004_reset_trust"
down_revision: Union[str, None] = "0003_sync_cursor"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE coupons
        SET status = 'unverified',
            confidence_score = 0.0,
            last_validated_at = NULL
        WHERE status = 'valid'
          AND confidence_score > 0.69 AND confidence_score < 0.71
          AND id NOT IN (SELECT coupon_id FROM validation_logs)
        """
    )


def downgrade() -> None:
    # One-way data correction; the prior (misleading) state is not restored.
    pass
