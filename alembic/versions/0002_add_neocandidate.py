"""add neocandidate table"""

from alembic import op
import sqlalchemy as sa


revision = "0002_add_neocandidate"
down_revision = "0001_create_site_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "neocandidate",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("trksub", sa.String(length=16), nullable=False, unique=True),
        sa.Column("score", sa.Integer, nullable=True),
        sa.Column("observations", sa.Integer, nullable=True),
        sa.Column("observed_ut", sa.String(length=64), nullable=True),
        sa.Column("ra_deg", sa.Float, nullable=True),
        sa.Column("dec_deg", sa.Float, nullable=True),
        sa.Column("vmag", sa.Float, nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("status_ut", sa.String(length=64), nullable=True),
        sa.Column("raw_entry", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=False),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=False),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_neocandidate_trksub", "neocandidate", ["trksub"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_neocandidate_trksub", table_name="neocandidate")
    op.drop_table("neocandidate")
