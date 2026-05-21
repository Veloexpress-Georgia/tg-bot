"""Add poll vote tracking and recreate guards.

Revision ID: 20260521_0003
Revises: 20260521_0002
Create Date: 2026-05-21 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260521_0003"
down_revision: str | None = "20260521_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("poll_batch", sa.Column("superseded_by_batch_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_poll_batch_superseded_by_batch_id",
        "poll_batch",
        "poll_batch",
        ["superseded_by_batch_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_table(
        "poll_option_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("poll_batch.id", ondelete="CASCADE")),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column("poll_id", sa.Text(), nullable=False),
        sa.Column("option_index", sa.Integer(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("lift_time", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_poll_option_snapshot_poll_id", "poll_option_snapshot", ["poll_id"])
    op.create_table(
        "poll_vote",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("poll_id", sa.Text(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=128), nullable=True),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("option_ids", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_poll_vote_poll_id", "poll_vote", ["poll_id"])
    op.create_index(
        "uq_poll_vote_poll_user",
        "poll_vote",
        ["poll_id", "telegram_user_id"],
        unique=True,
    )
    op.create_table(
        "poll_vote_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("poll_id", sa.Text(), nullable=False),
        sa.Column(
            "batch_id",
            sa.Integer(),
            sa.ForeignKey("poll_batch.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=128), nullable=True),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("old_option_ids", sa.Text(), nullable=False),
        sa.Column("new_option_ids", sa.Text(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_poll_vote_event_poll_id", "poll_vote_event", ["poll_id"])
    op.create_index("ix_poll_vote_event_batch_id", "poll_vote_event", ["batch_id"])
    op.create_index("ix_poll_vote_event_user", "poll_vote_event", ["telegram_user_id"])
    op.execute(
        """
        WITH ranked_active_batches AS (
            SELECT
                id,
                row_number() OVER (
                    PARTITION BY environment, chat_id, COALESCE(thread_id, 0), service_date
                    ORDER BY created_at DESC, id DESC
                ) AS active_rank
            FROM poll_batch
            WHERE status IN ('posting', 'posted', 'sent_unconfirmed')
        )
        UPDATE poll_batch
        SET status = 'cleanup_pending'
        WHERE id IN (
            SELECT id
            FROM ranked_active_batches
            WHERE active_rank > 1
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_poll_batch_active_service_day
        ON poll_batch (environment, chat_id, COALESCE(thread_id, 0), service_date)
        WHERE status IN ('posting', 'posted', 'sent_unconfirmed')
        """
    )


def downgrade() -> None:
    op.drop_index("uq_poll_batch_active_service_day", table_name="poll_batch")
    op.drop_index("ix_poll_vote_event_user", table_name="poll_vote_event")
    op.drop_index("ix_poll_vote_event_batch_id", table_name="poll_vote_event")
    op.drop_index("ix_poll_vote_event_poll_id", table_name="poll_vote_event")
    op.drop_table("poll_vote_event")
    op.drop_index("uq_poll_vote_poll_user", table_name="poll_vote")
    op.drop_index("ix_poll_vote_poll_id", table_name="poll_vote")
    op.drop_table("poll_vote")
    op.drop_index("ix_poll_option_snapshot_poll_id", table_name="poll_option_snapshot")
    op.drop_table("poll_option_snapshot")
    op.drop_constraint(
        "fk_poll_batch_superseded_by_batch_id",
        "poll_batch",
        type_="foreignkey",
    )
    op.drop_column("poll_batch", "superseded_by_batch_id")
