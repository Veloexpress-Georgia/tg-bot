"""Align required message foreign keys with the domain model.

Revision ID: 20260830_0023
Revises: 20260829_0022
Create Date: 2026-08-30 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260830_0023"
down_revision: str | None = "20260829_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "poll_message",
        "batch_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        "poll_option_snapshot",
        "batch_id",
        existing_type=sa.Integer(),
        nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "poll_option_snapshot",
        "batch_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.alter_column(
        "poll_message",
        "batch_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
