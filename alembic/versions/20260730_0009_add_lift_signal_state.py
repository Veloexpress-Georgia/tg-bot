"""Add lift signal state.

Revision ID: 20260730_0009
Revises: 20260718_0008
Create Date: 2026-07-30 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_0009"
down_revision: str | None = "20260718_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lift_signal_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("lift_time", sa.String(length=16), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("threshold_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("undershoot_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("undershoot_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("departure_ping_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_lift_signal_state_scope_date_time
        ON lift_signal_state (
            environment,
            chat_id,
            COALESCE(thread_id, 0),
            service_date,
            lift_time
        )
        """
    )


def downgrade() -> None:
    op.drop_index("uq_lift_signal_state_scope_date_time", table_name="lift_signal_state")
    op.drop_table("lift_signal_state")
