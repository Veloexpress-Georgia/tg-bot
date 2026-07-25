"""Add cancelled lift.

Revision ID: 20260718_0008
Revises: 20260718_0007
Create Date: 2026-07-18 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_0008"
down_revision: str | None = "20260718_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cancelled_lift",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("lift_time", sa.String(length=16), nullable=False),
        sa.Column("cancelled_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_cancelled_lift_scope_date_time
        ON cancelled_lift (
            environment,
            chat_id,
            COALESCE(thread_id, 0),
            service_date,
            lift_time
        )
        """
    )


def downgrade() -> None:
    op.drop_index("uq_cancelled_lift_scope_date_time", table_name="cancelled_lift")
    op.drop_table("cancelled_lift")
