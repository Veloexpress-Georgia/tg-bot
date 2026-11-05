"""Bike park sessions and their independent price; no lift or money records."""

import sqlalchemy as sa
from alembic import op

revision = "20261029_0033"
down_revision = "20261003_0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "service_day_defaults",
        sa.Column("bike_park_price_gel", sa.Integer(), nullable=False, server_default="5"),
    )
    op.create_table(
        "bike_park_session",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("environment", sa.String(64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.String(5), nullable=False),
        sa.Column("price_gel", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("poll_id", sa.Text(), unique=True, nullable=True),
        sa.Column("replace_lift_time", sa.String(16), nullable=True),
        sa.Column("replacement_completed", sa.Boolean(), nullable=False),
        sa.Column("poll_closed", sa.Boolean(), nullable=False),
    )
    op.execute("""CREATE UNIQUE INDEX uq_bike_park_active_day ON bike_park_session
        (environment, chat_id, COALESCE(thread_id, 0), service_date)
        WHERE status IN ('posting', 'posted', 'sent_unconfirmed')""")
    op.create_table(
        "bike_park_vote",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("bike_park_session.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("attending", sa.Boolean(), nullable=False),
    )
    op.create_index(
        "uq_bike_park_voter", "bike_park_vote", ["session_id", "telegram_user_id"], unique=True
    )


def downgrade() -> None:
    op.drop_table("bike_park_vote")
    op.drop_table("bike_park_session")
    op.drop_column("service_day_defaults", "bike_park_price_gel")
