"""Status CHECK constraints, one open seat offer per lift, outbox give-up, FK indexes.

The value sets are frozen here on purpose: a migration must keep meaning what
it meant when it ran, even after the model constants move on.
"""

import logging

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger("alembic.runtime.migration")

revision = "20261117_0035"
down_revision = "20261114_0034"
branch_labels = None
depends_on = None

CHECKS = {
    ("seat_offer", "status"): ("open", "closed"),
    ("poll_batch", "status"): (
        "draft",
        "posting",
        "posted",
        "sent_unconfirmed",
        "failed",
        "cancelled",
        "cleanup_pending",
        "cleanup_failed",
        "recreated",
        "deleted",
    ),
    ("bike_park_session", "status"): (
        "posting",
        "posted",
        "sent_unconfirmed",
        "failed",
        "cancelled",
        "not_reached",
    ),
    ("telegram_outbox", "status"): ("pending", "sent", "failed", "expired"),
    ("admin_command", "status"): ("pending", "running", "complete", "failed", "review"),
    ("payment_entry", "kind"): ("received", "reversal", "method_change", "refund"),
    ("payment_claim", "method"): ("cash", "transfer", "mixed"),
}


def upgrade() -> None:
    # One open offer per lift was only an application lock. Should two ever
    # have slipped through, keep the newest and close the rest so the unique
    # index can be built. Expected to touch nothing.
    op.execute("""
        UPDATE seat_offer SET status = 'closed'
        WHERE status = 'open'
          AND id NOT IN (
            SELECT DISTINCT ON (
                environment, chat_id, coalesce(thread_id, 0), service_date, lift_time
            ) id
            FROM seat_offer
            WHERE status = 'open'
            ORDER BY environment, chat_id, coalesce(thread_id, 0), service_date, lift_time,
                     created_at DESC, id DESC
          )
    """)
    op.execute("""
        CREATE UNIQUE INDEX uq_seat_offer_open_lift ON seat_offer
            (environment, chat_id, coalesce(thread_id, 0), service_date, lift_time)
        WHERE status = 'open'
    """)

    # Every table here holds at most a few thousand rows, so validating while
    # adding is a millisecond scan. A row with a value nobody listed must not
    # stop a deploy, though: that constraint is added NOT VALID, which still
    # checks every new write, and the stray values are logged for a follow-up.
    bind = op.get_bind()
    for (table, column), values in CHECKS.items():
        allowed = ", ".join(f"'{value}'" for value in values)
        stray = [
            row[0]
            for row in bind.execute(
                sa.text(
                    f"SELECT DISTINCT {column} FROM {table} "
                    f"WHERE {column} IS NOT NULL AND {column} NOT IN ({allowed})"
                )
            )
        ]
        if stray:
            logger.warning("ck_%s_%s added NOT VALID; unexpected values: %s", table, column, stray)
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT ck_{table}_{column} "
            f"CHECK ({column} IN ({allowed})){' NOT VALID' if stray else ''}"
        )

    op.create_index(
        "ix_telegram_outbox_pending",
        "telegram_outbox",
        ["environment", "id"],
        postgresql_where="status = 'pending'",
    )

    # A prefix of uq_poll_vote_poll_user, which serves the same lookups.
    op.drop_index("ix_poll_vote_poll_id", table_name="poll_vote")
    op.create_index("ix_poll_message_batch_id", "poll_message", ["batch_id"])
    op.create_index("ix_poll_option_snapshot_batch_id", "poll_option_snapshot", ["batch_id"])
    op.create_index("ix_payment_entry_reversed_entry_id", "payment_entry", ["reversed_entry_id"])


def downgrade() -> None:
    op.drop_index("ix_payment_entry_reversed_entry_id", table_name="payment_entry")
    op.drop_index("ix_poll_option_snapshot_batch_id", table_name="poll_option_snapshot")
    op.drop_index("ix_poll_message_batch_id", table_name="poll_message")
    op.create_index("ix_poll_vote_poll_id", "poll_vote", ["poll_id"])
    op.drop_index("ix_telegram_outbox_pending", table_name="telegram_outbox")
    # "failed"/"expired" outbox rows stay as they are: older code only ever reads
    # "pending", so they are left alone instead of being sent days late.
    for table, column in reversed(CHECKS):
        op.drop_constraint(f"ck_{table}_{column}", table, type_="check")
    op.drop_index("uq_seat_offer_open_lift", table_name="seat_offer")
