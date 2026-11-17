"""Track payable cancellation refunds independently of the money ledger."""

import sqlalchemy as sa
from alembic import op

revision = "20261117_0036"
down_revision = "20261117_0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "refund_obligation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("environment", sa.String(64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("amount_gel", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column(
            "reversal_entry_id",
            sa.Integer(),
            sa.ForeignKey("payment_entry.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("refund_obligation")
