"""Add service day notice.

Revision ID: 20260730_0011
Revises: 20260730_0010
Create Date: 2026-07-30 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_0011"
down_revision: str | None = "20260730_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "service_day_notice",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("deadline_reminded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_service_day_notice_scope_date
        ON service_day_notice (
            environment,
            chat_id,
            COALESCE(thread_id, 0),
            service_date
        )
        """
    )


def downgrade() -> None:
    op.drop_index("uq_service_day_notice_scope_date", table_name="service_day_notice")
    op.drop_table("service_day_notice")
