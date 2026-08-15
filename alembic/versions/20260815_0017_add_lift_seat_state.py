"""Add the seat allocation the bot last saw.

Revision ID: 20260815_0017
Revises: 20260815_0016
Create Date: 2026-08-15 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_0017"
down_revision: str | None = "20260815_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lift_seat_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("lift_time", sa.String(length=16), nullable=False),
        sa.Column("holder_ids", sa.Text(), nullable=False),
        sa.Column("waitlist_ids", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_lift_seat_state_scope_date_time
        ON lift_seat_state (
            environment,
            chat_id,
            COALESCE(thread_id, 0),
            service_date,
            lift_time
        )
        """
    )


def downgrade() -> None:
    op.drop_index("uq_lift_seat_state_scope_date_time", table_name="lift_seat_state")
    op.drop_table("lift_seat_state")
