"""Add the deadline roster.

Revision ID: 20260815_0016
Revises: 20260802_0015
Create Date: 2026-08-15 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_0016"
down_revision: str | None = "20260802_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deadline_roster",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("lift_time", sa.String(length=16), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("seats", sa.Integer(), nullable=False),
        sa.Column("guests", sa.Integer(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_deadline_roster_scope_date_time_user
        ON deadline_roster (
            environment,
            chat_id,
            COALESCE(thread_id, 0),
            service_date,
            lift_time,
            telegram_user_id
        )
        """
    )
    # Marks the day as frozen even when the roster came out empty, so "nobody
    # booked" is not retried on every tick as "not captured yet".
    op.add_column(
        "service_day_notice",
        sa.Column("roster_captured_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("service_day_notice", "roster_captured_at")
    op.drop_index("uq_deadline_roster_scope_date_time_user", table_name="deadline_roster")
    op.drop_table("deadline_roster")
