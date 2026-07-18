"""Add admin booking monitors and manual booking counts.

Revision ID: 20260718_0005
Revises: 20260718_0004
Create Date: 2026-07-18 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_0005"
down_revision: str | None = "20260718_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "manual_booking_count",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("lift_time", sa.String(length=16), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("updated_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_manual_booking_count_scope_date_time
        ON manual_booking_count (
            environment,
            chat_id,
            COALESCE(thread_id, 0),
            service_date,
            lift_time
        )
        """
    )

    op.create_table(
        "admin_booking_monitor",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("admin_user_id", sa.BigInteger(), nullable=False),
        sa.Column("private_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column("selected_service_date", sa.Date(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_admin_booking_monitor_scope_admin
        ON admin_booking_monitor (
            environment,
            chat_id,
            COALESCE(thread_id, 0),
            admin_user_id
        )
        """
    )


def downgrade() -> None:
    op.drop_index(
        "uq_admin_booking_monitor_scope_admin",
        table_name="admin_booking_monitor",
    )
    op.drop_table("admin_booking_monitor")
    op.drop_index(
        "uq_manual_booking_count_scope_date_time",
        table_name="manual_booking_count",
    )
    op.drop_table("manual_booking_count")
