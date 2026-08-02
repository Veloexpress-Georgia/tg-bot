"""Track the deadline reminder message so it can stay fresh.

Revision ID: 20260802_0015
Revises: 20260731_0014
Create Date: 2026-08-02 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_0015"
down_revision: str | None = "20260731_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "service_day_notice",
        sa.Column("deadline_message_id", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("service_day_notice", "deadline_message_id")
