"""Keep the observed waitlist with a finished lift.

Revision ID: 20261002_0030
Revises: 20261001_0029
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261002_0030"
down_revision: str | None = "20261001_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("lift_day_result", sa.Column("waiting_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("lift_day_result", "waiting_count")
