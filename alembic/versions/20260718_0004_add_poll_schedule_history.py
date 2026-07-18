"""Add poll schedule history.

Revision ID: 20260718_0004
Revises: 20260521_0003
Create Date: 2026-07-18 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_0004"
down_revision: str | None = "20260521_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "poll_schedule_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("service_week_start", sa.Date(), nullable=False),
        sa.Column("first_lift_time", sa.String(length=16), nullable=False),
        sa.Column("last_lift_time", sa.String(length=16), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_poll_schedule_history_scope_week
        ON poll_schedule_history (
            environment,
            chat_id,
            COALESCE(thread_id, 0),
            service_week_start
        )
        """
    )


def downgrade() -> None:
    op.drop_index(
        "uq_poll_schedule_history_scope_week",
        table_name="poll_schedule_history",
    )
    op.drop_table("poll_schedule_history")
