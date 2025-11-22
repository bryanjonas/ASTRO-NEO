"""add measurement table"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0010_measurements"
down_revision = "0009_astrometry_q"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "measurement",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("capture_id", sa.Integer, sa.ForeignKey("capturelog.id"), nullable=True),
        sa.Column("target", sa.String(length=128), nullable=False),
        sa.Column("obs_time", sa.DateTime(timezone=False), nullable=False),
        sa.Column("ra_deg", sa.Float, nullable=False),
        sa.Column("dec_deg", sa.Float, nullable=False),
        sa.Column("ra_uncert_arcsec", sa.Float, nullable=True),
        sa.Column("dec_uncert_arcsec", sa.Float, nullable=True),
        sa.Column("magnitude", sa.Float, nullable=True),
        sa.Column("band", sa.String(length=8), nullable=True),
        sa.Column("exposure_seconds", sa.Float, nullable=True),
        sa.Column("tracking_mode", sa.String(length=32), nullable=True),
        sa.Column("station_code", sa.String(length=8), nullable=True),
        sa.Column("observer", sa.String(length=64), nullable=True),
        sa.Column("software", sa.String(length=64), nullable=True),
        sa.Column("flags", sa.Text(), nullable=True),
        sa.Column("reviewed", sa.Boolean, nullable=False, server_default=sa.sql.false()),
        sa.Column("created_at", sa.DateTime(timezone=False), nullable=False),
    )
    op.create_index("ix_measurement_target", "measurement", ["target"])
    op.create_index("ix_measurement_obs_time", "measurement", ["obs_time"])
    op.create_index("ix_measurement_reviewed", "measurement", ["reviewed"])


def downgrade() -> None:
    op.drop_index("ix_measurement_reviewed", table_name="measurement")
    op.drop_index("ix_measurement_obs_time", table_name="measurement")
    op.drop_index("ix_measurement_target", table_name="measurement")
    op.drop_table("measurement")
