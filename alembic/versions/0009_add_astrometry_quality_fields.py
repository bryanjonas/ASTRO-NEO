"""add astrometry quality fields"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0009_astrometry_q"
down_revision = "0007_add_equipment_profile"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.add_column("astrometricsolution", sa.Column("target", sa.String(length=128), nullable=True))
    op.add_column("astrometricsolution", sa.Column("snr", sa.Float(), nullable=True))
    op.add_column("astrometricsolution", sa.Column("mag_inst", sa.Float(), nullable=True))
    op.add_column("astrometricsolution", sa.Column("flags", sa.Text(), nullable=True))
    op.create_index("ix_astrometry_target", "astrometricsolution", ["target"])


def downgrade() -> None:
    op.drop_index("ix_astrometry_target", table_name="astrometricsolution")
    op.drop_column("astrometricsolution", "flags")
    op.drop_column("astrometricsolution", "mag_inst")
    op.drop_column("astrometricsolution", "snr")
    op.drop_column("astrometricsolution", "target")
