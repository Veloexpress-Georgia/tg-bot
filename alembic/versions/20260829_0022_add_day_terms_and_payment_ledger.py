"""Snapshot service-day terms and add an append-only payment ledger.

Revision ID: 20260829_0022
Revises: 20260823_0021
Create Date: 2026-08-29 00:00:00.000000
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260829_0022"
down_revision: str | None = "20260823_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    price_gel = int(os.getenv("PAYMENT_PRICE_GEL", "15"))
    deadline_time = os.getenv("BOOKING_DEADLINE_TIME", "20:00")
    timezone = os.getenv("SCHEDULE_TIMEZONE", "Asia/Tbilisi")
    op.add_column(
        "deadline_roster",
        sa.Column("covered_seats", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "worker_checkpoint",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
    )

    op.create_table(
        "telegram_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("operation_key", sa.String(length=256), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("parse_mode", sa.String(length=16), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_telegram_outbox_environment_operation",
        "telegram_outbox",
        ["environment", "operation_key"],
        unique=True,
    )
    op.create_index(
        "uq_worker_checkpoint_environment_name",
        "worker_checkpoint",
        ["environment", "name"],
        unique=True,
    )

    op.create_table(
        "service_day_terms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("price_gel", sa.Integer(), nullable=False),
        sa.Column("deadline_time", sa.String(length=16), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_service_day_terms_scope_date
        ON service_day_terms (
            environment, chat_id, COALESCE(thread_id, 0), service_date
        )
        """
    )
    op.execute(
        sa.text(
            """
        INSERT INTO service_day_terms (
            environment, chat_id, thread_id, service_date,
            price_gel, deadline_time, timezone, created_at
        )
        SELECT environment, chat_id, thread_id, service_date,
               :price_gel, :deadline_time, :timezone, MIN(created_at)
        FROM poll_batch
        GROUP BY environment, chat_id, thread_id, service_date
        """
        ).bindparams(
            price_gel=price_gel,
            deadline_time=deadline_time,
            timezone=timezone,
        )
    )

    op.add_column(
        "payment_claim",
        sa.Column("amount_gel", sa.Integer(), nullable=True),
    )
    op.add_column(
        "payment_claim",
        sa.Column("cash_amount_gel", sa.Integer(), nullable=True),
    )
    op.execute(
        sa.text("UPDATE payment_claim SET amount_gel = seats * :price_gel").bindparams(
            price_gel=price_gel
        )
    )
    op.execute(
        "UPDATE payment_claim SET cash_amount_gel = "
        "CASE WHEN method = 'cash' THEN amount_gel ELSE 0 END"
    )
    op.alter_column("payment_claim", "amount_gel", nullable=False, server_default="0")
    op.alter_column("payment_claim", "cash_amount_gel", nullable=False, server_default="0")

    op.create_table(
        "payment_entry",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("amount_gel", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("reference_key", sa.String(length=256), nullable=True),
        sa.Column(
            "reversed_entry_id",
            sa.Integer(),
            sa.ForeignKey("payment_entry.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_payment_entry_scope_date_user",
        "payment_entry",
        ["environment", "chat_id", "service_date", "telegram_user_id"],
    )
    op.create_index(
        "uq_payment_entry_environment_reference",
        "payment_entry",
        ["environment", "reference_key"],
        unique=True,
    )
    op.execute(
        """
        INSERT INTO payment_entry (
            environment, chat_id, thread_id, service_date, telegram_user_id,
            amount_gel, method, kind, recorded_at
        )
        SELECT environment, chat_id, thread_id, service_date, telegram_user_id,
               amount_gel, method, 'received', claimed_at
        FROM payment_claim
        """
    )


def downgrade() -> None:
    op.drop_index("uq_telegram_outbox_environment_operation", table_name="telegram_outbox")
    op.drop_table("telegram_outbox")
    op.drop_index("ix_payment_entry_scope_date_user", table_name="payment_entry")
    op.drop_index("uq_payment_entry_environment_reference", table_name="payment_entry")
    op.drop_table("payment_entry")
    op.drop_column("payment_claim", "amount_gel")
    op.drop_column("payment_claim", "cash_amount_gel")
    op.drop_index("uq_service_day_terms_scope_date", table_name="service_day_terms")
    op.drop_table("service_day_terms")
    op.drop_column("deadline_roster", "covered_seats")
    op.drop_index("uq_worker_checkpoint_environment_name", table_name="worker_checkpoint")
    op.drop_table("worker_checkpoint")
