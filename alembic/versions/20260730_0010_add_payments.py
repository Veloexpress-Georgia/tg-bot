"""Add payment claims, payments board and payments topic activity.

Revision ID: 20260730_0010
Revises: 20260730_0009
Create Date: 2026-07-30 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_0010"
down_revision: str | None = "20260730_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payment_claim",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=128), nullable=True),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("seats", sa.Integer(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("posted_message_id", sa.BigInteger(), nullable=True),
        sa.Column("verified_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_payment_claim_scope_date_user
        ON payment_claim (
            environment,
            chat_id,
            COALESCE(thread_id, 0),
            service_date,
            telegram_user_id
        )
        """
    )

    op.create_table(
        "payments_board",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_payments_board_scope_date",
        "payments_board",
        ["environment", "chat_id", "service_date"],
        unique=True,
    )

    op.create_table(
        "payments_topic_post",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("last_posted_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_payments_topic_post_scope_user",
        "payments_topic_post",
        ["environment", "chat_id", "telegram_user_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_payments_topic_post_scope_user", table_name="payments_topic_post")
    op.drop_table("payments_topic_post")
    op.drop_index("uq_payments_board_scope_date", table_name="payments_board")
    op.drop_table("payments_board")
    op.drop_index("uq_payment_claim_scope_date_user", table_name="payment_claim")
    op.drop_table("payment_claim")
