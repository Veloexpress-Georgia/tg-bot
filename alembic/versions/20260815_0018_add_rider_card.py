"""Add the rider's private card.

Revision ID: 20260815_0018
Revises: 20260815_0017
Create Date: 2026-08-15 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_0018"
down_revision: str | None = "20260815_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rider_card",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("private_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_rider_card_scope_user
        ON rider_card (environment, telegram_user_id)
        """
    )


def downgrade() -> None:
    op.drop_index("uq_rider_card_scope_user", table_name="rider_card")
    op.drop_table("rider_card")
