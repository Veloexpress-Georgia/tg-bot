"""Add per-option booking times to poll votes.

Revision ID: 20260821_0020
Revises: 20260816_0019
Create Date: 2026-08-21 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260821_0020"
down_revision: str | None = "20260816_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Empty for existing votes: their per-option times were never recorded, and
    # `updated_at` stands in for them, which is exactly today's behaviour. New
    # picks start carrying their own time from the next vote change onwards.
    op.add_column(
        "poll_vote",
        sa.Column("option_booked_at", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("poll_vote", "option_booked_at")
