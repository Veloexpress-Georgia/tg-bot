"""Add payment method.

Revision ID: 20260731_0012
Revises: 20260730_0011
Create Date: 2026-07-31 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260731_0012"
down_revision: str | None = "20260730_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("payment_claim", sa.Column("method", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("payment_claim", "method")
