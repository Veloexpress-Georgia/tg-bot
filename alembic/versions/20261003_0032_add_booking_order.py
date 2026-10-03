"""Admin booking order, retraction restoration and audit history."""

import sqlalchemy as sa
from alembic import op

revision = "20261003_0032"
down_revision = "20261002_0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "poll_vote", sa.Column("option_queue_ranks", sa.Text(), nullable=False, server_default="{}")
    )
    op.add_column(
        "poll_vote",
        sa.Column("option_previous_orders", sa.Text(), nullable=False, server_default="{}"),
    )
    op.create_table(
        "booking_order_change",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "snapshot_id",
            sa.Integer(),
            sa.ForeignKey("poll_option_snapshot.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("environment", sa.String(64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("lift_time", sa.String(16), nullable=False),
        sa.Column("poll_id", sa.Text(), nullable=False),
        sa.Column("admin_user_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("before_order", sa.Text(), nullable=False),
        sa.Column("after_order", sa.Text(), nullable=False),
        sa.Column("promoted_ids", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("demoted_ids", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_booking_order_change_snapshot_id", "booking_order_change", ["snapshot_id"])


def downgrade() -> None:
    op.drop_table("booking_order_change")
    op.drop_column("poll_vote", "option_previous_orders")
    op.drop_column("poll_vote", "option_queue_ranks")
