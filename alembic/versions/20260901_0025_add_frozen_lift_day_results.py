"""Freeze what each finished lift day came to, instead of re-deriving it.

Revision ID: 20260901_0025
Revises: 20260830_0024
Create Date: 2026-09-01 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260901_0025"
down_revision: str | None = "20260830_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lift_day_result",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("lift_time", sa.String(length=16), nullable=False),
        sa.Column("ran", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cancelled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("seats", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("guest_seats", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("manual_seats", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("covered_seats", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("capacity", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("price_gel", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="closed"),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_lift_day_result_scope_date_time
        ON lift_day_result (
            environment, chat_id, COALESCE(thread_id, 0), service_date, lift_time
        )
        """
    )
    op.create_index(
        "ix_lift_day_result_scope_date",
        "lift_day_result",
        ["environment", "chat_id", "service_date"],
    )

    op.create_table(
        "lift_day_seat",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("lift_time", sa.String(length=16), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False, server_default=""),
        sa.Column("seats", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("guests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("covered_seats", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_lift_day_seat_scope_date_time_user
        ON lift_day_seat (
            environment, chat_id, COALESCE(thread_id, 0),
            service_date, lift_time, telegram_user_id
        )
        """
    )
    op.create_index(
        "ix_lift_day_seat_scope_user",
        "lift_day_seat",
        ["environment", "chat_id", "telegram_user_id"],
    )

    op.add_column(
        "service_day_notice",
        sa.Column("results_frozen_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("service_day_notice", "results_frozen_at")
    op.drop_index("ix_lift_day_seat_scope_user", table_name="lift_day_seat")
    op.drop_index("uq_lift_day_seat_scope_date_time_user", table_name="lift_day_seat")
    op.drop_table("lift_day_seat")
    op.drop_index("ix_lift_day_result_scope_date", table_name="lift_day_result")
    op.drop_index("uq_lift_day_result_scope_date_time", table_name="lift_day_result")
    op.drop_table("lift_day_result")
