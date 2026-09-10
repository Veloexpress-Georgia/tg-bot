"""Track the day payment card in the lift topic.

Revision ID: 20260910_0027
Revises: 20260910_0026
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260910_0027"
down_revision: str | None = "20260910_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("payments_board", sa.Column("lift_message_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("payments_board", "lift_message_id")
