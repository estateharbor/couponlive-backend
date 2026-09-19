"""cancel-before-auto-debit reminders (T4)

Revision ID: 0007_trial_reminders
Revises: 0006_trials
Create Date: 2026-09-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0007_trial_reminders"
down_revision: Union[str, None] = "0006_trials"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

trial_reminder_status = postgresql.ENUM(
    "active", "cancelled", "converted", "expired", "unsubscribed",
    name="trial_reminder_status", create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    trial_reminder_status.create(bind, checkfirst=True)

    op.create_table(
        "trial_reminders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("offer_id", sa.Integer(),
                  sa.ForeignKey("trial_offers.id", ondelete="SET NULL")),
        sa.Column("tool_name", sa.String(255), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("renew_price_inr", sa.Numeric(12, 2)),
        sa.Column("renew_note", sa.String(64)),
        sa.Column("cancel_url", sa.Text()),
        sa.Column("remind_days_before", sa.String(32), nullable=False, server_default="3,1"),
        sa.Column("sent_offsets", sa.String(64), nullable=False, server_default=""),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("status", trial_reminder_status, nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("token", name="uq_trial_reminder_token"),
    )
    op.create_index("ix_trial_reminders_email", "trial_reminders", ["email"])
    op.create_index("ix_trial_reminders_token", "trial_reminders", ["token"])
    op.create_index("ix_trial_reminder_due", "trial_reminders", ["status", "ends_on"])


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_table("trial_reminders")
    trial_reminder_status.drop(bind, checkfirst=True)
