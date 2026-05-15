"""create poll tables

Revision ID: 20260515_0001
Revises:
Create Date: 2026-05-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260515_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "poll_batch",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("service_day", sa.String(length=32), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_lift_location", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
    )
    op.create_index(
        "ix_poll_batch_idempotency_key",
        "poll_batch",
        ["idempotency_key"],
        unique=True,
    )
    op.create_table(
        "poll_message",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("poll_batch.id", ondelete="CASCADE")),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column("poll_id", sa.Text(), nullable=True),
        sa.Column("pinned", sa.Boolean(), nullable=False),
        sa.Column("cleanup_status", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("poll_message")
    op.drop_index("ix_poll_batch_idempotency_key", table_name="poll_batch")
    op.drop_table("poll_batch")
