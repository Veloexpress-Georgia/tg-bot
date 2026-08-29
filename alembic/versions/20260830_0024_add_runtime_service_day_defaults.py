"""Add runtime-editable defaults for newly published service days.

Revision ID: 20260830_0024
Revises: 20260830_0023
Create Date: 2026-08-30 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260830_0024"
down_revision: str | None = "20260830_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "service_day_defaults",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("price_gel", sa.Integer(), nullable=False),
        sa.Column("deadline_time", sa.String(length=16), nullable=False),
        sa.Column("updated_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_service_day_defaults_scope
        ON service_day_defaults (
            environment, chat_id, COALESCE(thread_id, 0)
        )
        """
    )


def downgrade() -> None:
    op.drop_index("uq_service_day_defaults_scope", table_name="service_day_defaults")
    op.drop_table("service_day_defaults")
