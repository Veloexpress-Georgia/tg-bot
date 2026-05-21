"""Add poll message kind.

Revision ID: 20260521_0002
Revises: 20260515_0001
Create Date: 2026-05-21 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260521_0002"
down_revision: str | None = "20260515_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "poll_message",
        sa.Column("message_kind", sa.String(length=32), server_default="poll", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("poll_message", "message_kind")
