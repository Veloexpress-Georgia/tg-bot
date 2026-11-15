"""Seat offers: a freed seat is offered to the waitlist instead of assigned."""

import sqlalchemy as sa
from alembic import op

revision = "20261114_0034"
down_revision = "20261029_0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("lift_seat_state", sa.Column("held_waitlist_ids", sa.Text(), nullable=True))
    op.add_column("lift_seat_state", sa.Column("declined_ids", sa.Text(), nullable=True))
    op.create_table(
        "seat_offer",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("environment", sa.String(64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("lift_time", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("seats", sa.Integer(), nullable=False),
        sa.Column("offered_ids", sa.Text(), nullable=False),
        sa.Column("claimed_ids", sa.Text(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("widened_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_seat_offer_day",
        "seat_offer",
        ["environment", "chat_id", "service_date", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_seat_offer_day", table_name="seat_offer")
    op.drop_table("seat_offer")
    op.drop_column("lift_seat_state", "declined_ids")
    op.drop_column("lift_seat_state", "held_waitlist_ids")
