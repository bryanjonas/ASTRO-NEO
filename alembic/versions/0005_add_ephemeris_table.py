"""add neoephemeris cache table"""

from alembic import op
import sqlalchemy as sa


revision = "0005_add_ephemeris_table"
down_revision = "0004_add_observability_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "neoephemeris",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("candidate_id", sa.Integer, sa.ForeignKey("neocandidate.id"), nullable=False),
        sa.Column("trksub", sa.String(length=16), nullable=False),
        sa.Column("epoch", sa.DateTime(timezone=False), nullable=False),
        sa.Column("ra_deg", sa.Float, nullable=False),
        sa.Column("dec_deg", sa.Float, nullable=False),
        sa.Column("delta_au", sa.Float, nullable=True),
        sa.Column("r_au", sa.Float, nullable=True),
        sa.Column("rate_arcsec_per_min", sa.Float, nullable=True),
        sa.Column("position_angle_deg", sa.Float, nullable=True),
        sa.Column("magnitude", sa.Float, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=False),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "candidate_id",
            "epoch",
            name="uq_neoeph_candidate_epoch",
        ),
    )
    op.create_index("ix_neoephemeris_candidate_id", "neoephemeris", ["candidate_id"])
    op.create_index("ix_neoephemeris_trksub", "neoephemeris", ["trksub"])
    op.create_index("ix_neoephemeris_epoch", "neoephemeris", ["epoch"])
    op.create_index("ix_neoephemeris_created_at", "neoephemeris", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_neoephemeris_created_at", table_name="neoephemeris")
    op.drop_index("ix_neoephemeris_epoch", table_name="neoephemeris")
    op.drop_index("ix_neoephemeris_trksub", table_name="neoephemeris")
    op.drop_index("ix_neoephemeris_candidate_id", table_name="neoephemeris")
    op.drop_table("neoephemeris")
