"""Add poll auto schedule configuration.

Revision ID: 20260718_0006
Revises: 20260718_0005
Create Date: 2026-07-18 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_0006"
down_revision: str | None = "20260718_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "poll_auto_schedule",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("creation_weekday", sa.Integer(), nullable=False),
        sa.Column("creation_time", sa.String(length=16), nullable=False),
        sa.Column("announce_lead_minutes", sa.Integer(), nullable=False),
        sa.Column("skip_week_start", sa.Date(), nullable=True),
        sa.Column("last_announced_week_start", sa.Date(), nullable=True),
        sa.Column("last_created_week_start", sa.Date(), nullable=True),
        sa.Column("announce_message_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_poll_auto_schedule_scope
        ON poll_auto_schedule (
            environment,
            chat_id,
            COALESCE(thread_id, 0)
        )
        """
    )


def downgrade() -> None:
    op.drop_index("uq_poll_auto_schedule_scope", table_name="poll_auto_schedule")
    op.drop_table("poll_auto_schedule")
