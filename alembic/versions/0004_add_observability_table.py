"""add neoobservability table"""

from alembic import op
import sqlalchemy as sa


revision = "0004_add_observability_table"
down_revision = "0003_add_neocp_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "neoobservability",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("candidate_id", sa.Integer, sa.ForeignKey("neocandidate.id"), nullable=False),
        sa.Column("trksub", sa.String(length=16), nullable=False),
        sa.Column("night_key", sa.Date, nullable=False),
        sa.Column("night_start", sa.DateTime(timezone=False), nullable=False),
        sa.Column("night_end", sa.DateTime(timezone=False), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=False), nullable=True),
        sa.Column("window_end", sa.DateTime(timezone=False), nullable=True),
        sa.Column("duration_minutes", sa.Float, nullable=True),
        sa.Column("max_altitude_deg", sa.Float, nullable=True),
        sa.Column("min_moon_separation_deg", sa.Float, nullable=True),
        sa.Column("max_sun_altitude_deg", sa.Float, nullable=True),
        sa.Column("score", sa.Float, nullable=False, server_default="0"),
        sa.Column("score_breakdown", sa.Text, nullable=True),
        sa.Column("is_observable", sa.Boolean, nullable=False, server_default=sa.sql.false()),
        sa.Column("limiting_factors", sa.Text, nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=False), nullable=False),
        sa.UniqueConstraint(
            "candidate_id",
            "night_key",
            name="uq_neocandidate_observability_night",
        ),
    )
    op.create_index("ix_neoobservability_candidate_id", "neoobservability", ["candidate_id"])
    op.create_index("ix_neoobservability_trksub", "neoobservability", ["trksub"])
    op.create_index("ix_neoobservability_night_key", "neoobservability", ["night_key"])
    op.create_index("ix_neoobservability_computed_at", "neoobservability", ["computed_at"])


def downgrade() -> None:
    op.drop_index("ix_neoobservability_computed_at", table_name="neoobservability")
    op.drop_index("ix_neoobservability_night_key", table_name="neoobservability")
    op.drop_index("ix_neoobservability_trksub", table_name="neoobservability")
    op.drop_index("ix_neoobservability_candidate_id", table_name="neoobservability")
    op.drop_table("neoobservability")
