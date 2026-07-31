"""Add payment guests.

Revision ID: 20260731_0013
Revises: 20260731_0012
Create Date: 2026-07-31 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260731_0013"
down_revision: str | None = "20260731_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows keep `seats` as the amount already settled, which stays correct.
    # Their guest count is unknowable after the fact — it was only ever derived — so
    # it starts at zero and the rider re-declares if they bring someone.
    op.add_column(
        "payment_claim",
        sa.Column("guests", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("payment_claim", "guests")
