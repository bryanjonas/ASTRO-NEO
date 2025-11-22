"""add mag sigma to measurement"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0012_add_mag_sigma"
down_revision = "0011_submission_log"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.add_column("measurement", sa.Column("mag_sigma", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("measurement", "mag_sigma")
