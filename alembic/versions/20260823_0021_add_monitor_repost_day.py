"""Track the lift day each admin monitor was reposted for.

Revision ID: 20260823_0021
Revises: 20260821_0020
Create Date: 2026-08-23 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260823_0021"
down_revision: str | None = "20260821_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Null for existing monitors: the first lift-day morning after this ships
    # reposts them once, which is exactly the intended behaviour.
    op.add_column(
        "admin_booking_monitor",
        sa.Column("reposted_for", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("admin_booking_monitor", "reposted_for")
