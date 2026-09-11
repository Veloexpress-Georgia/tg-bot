"""Remember which screen each admin monitor card is showing.

Revision ID: 20260911_0028
Revises: 20260910_0027
Create Date: 2026-09-11 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260911_0028"
down_revision: str | None = "20260910_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing cards are showing the day overview, which is what the background
    # refresh used to assume for every card unconditionally.
    op.add_column(
        "admin_booking_monitor",
        sa.Column("screen", sa.String(length=32), nullable=False, server_default="day"),
    )
    op.add_column(
        "admin_booking_monitor",
        sa.Column("screen_state", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("admin_booking_monitor", "screen_state")
    op.drop_column("admin_booking_monitor", "screen")
