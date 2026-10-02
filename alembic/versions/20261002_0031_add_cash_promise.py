"""Keep on-site cash intentions separate from received-money history.

Revision ID: 20261002_0031
Revises: 20261002_0030
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261002_0031"
down_revision: str | None = "20261002_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cash_promise",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("environment", sa.String(64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_cash_promise_scope",
        "cash_promise",
        [
            "environment",
            "chat_id",
            sa.text("coalesce(thread_id, 0)"),
            "service_date",
            "telegram_user_id",
        ],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_cash_promise_scope", "cash_promise")
    op.drop_table("cash_promise")
