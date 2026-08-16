"""Keep the refund report as a record.

Revision ID: 20260816_0019
Revises: 20260815_0018
Create Date: 2026-08-16 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260816_0019"
down_revision: str | None = "20260815_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "refund_report",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("lift_time", sa.String(length=16), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_refund_report_scope_created",
        "refund_report",
        ["environment", "chat_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_refund_report_scope_created", table_name="refund_report")
    op.drop_table("refund_report")
