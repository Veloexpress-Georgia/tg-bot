"""Add guest seats per lift.

Revision ID: 20260731_0014
Revises: 20260731_0013
Create Date: 2026-07-31 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260731_0014"
down_revision: str | None = "20260731_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "guest_seat",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("lift_time", sa.String(length=16), nullable=False),
        sa.Column("host_user_id", sa.BigInteger(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_guest_seat_scope_date_time_host
        ON guest_seat (
            environment,
            chat_id,
            COALESCE(thread_id, 0),
            service_date,
            lift_time,
            host_user_id
        )
        """
    )
    # A day-level guest count cannot occupy a seat, because a seat belongs to a lift.
    # The column never reached production, so it goes rather than lingering unused.
    op.drop_column("payment_claim", "guests")


def downgrade() -> None:
    op.add_column(
        "payment_claim",
        sa.Column("guests", sa.Integer(), nullable=False, server_default="0"),
    )
    op.drop_index("uq_guest_seat_scope_date_time_host", table_name="guest_seat")
    op.drop_table("guest_seat")
