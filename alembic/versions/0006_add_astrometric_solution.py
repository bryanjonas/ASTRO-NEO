"""add astrometric solutions table"""

from alembic import op
import sqlalchemy as sa


revision = "0006_add_astrometric_solution"
down_revision = "0005a_add_capturelog_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "astrometricsolution",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("capture_id", sa.Integer, sa.ForeignKey("capturelog.id"), nullable=True),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("ra_deg", sa.Float, nullable=True),
        sa.Column("dec_deg", sa.Float, nullable=True),
        sa.Column("orientation_deg", sa.Float, nullable=True),
        sa.Column("pixel_scale_arcsec", sa.Float, nullable=True),
        sa.Column("uncertainty_arcsec", sa.Float, nullable=True),
        sa.Column("success", sa.Boolean, nullable=False, server_default=sa.sql.false()),
        sa.Column("solver_info", sa.Text, nullable=True),
        sa.Column("solved_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column("duration_seconds", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=False), nullable=False),
    )
    op.create_index("ix_astrometry_capture_id", "astrometricsolution", ["capture_id"])
    op.create_index("ix_astrometry_path", "astrometricsolution", ["path"])
    op.create_index("ix_astrometry_ra", "astrometricsolution", ["ra_deg"])
    op.create_index("ix_astrometry_dec", "astrometricsolution", ["dec_deg"])
    op.create_index("ix_astrometry_success", "astrometricsolution", ["success"])
    op.create_index("ix_astrometry_solved_at", "astrometricsolution", ["solved_at"])


def downgrade() -> None:
    op.drop_index("ix_astrometry_solved_at", table_name="astrometricsolution")
    op.drop_index("ix_astrometry_success", table_name="astrometricsolution")
    op.drop_index("ix_astrometry_dec", table_name="astrometricsolution")
    op.drop_index("ix_astrometry_ra", table_name="astrometricsolution")
    op.drop_index("ix_astrometry_path", table_name="astrometricsolution")
    op.drop_index("ix_astrometry_capture_id", table_name="astrometricsolution")
    op.drop_table("astrometricsolution")
