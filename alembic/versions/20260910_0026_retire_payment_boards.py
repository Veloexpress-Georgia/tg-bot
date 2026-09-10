"""Track completed payment-board cleanup.

Revision ID: 20260910_0026
Revises: 20260901_0025
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260910_0026"
down_revision: str | None = "20260901_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "payments_board", sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("payments_board", "retired_at")
