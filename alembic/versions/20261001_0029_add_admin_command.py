"""Durable web commands and audit trail.

Revision ID: 20261001_0029
Revises: 20260911_0028
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261001_0029"
down_revision: str | None = "20260911_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_command",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("environment", sa.String(64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        sa.Column("request_key", sa.String(128), nullable=False, unique=True),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_admin_command_status", "admin_command", ["status"])


def downgrade() -> None:
    op.drop_index("ix_admin_command_status", "admin_command")
    op.drop_table("admin_command")
