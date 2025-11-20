"""create site config table"""

from alembic import op
import sqlalchemy as sa

revision = "0001_create_site_config"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "siteconfig",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False, unique=True),
        sa.Column("latitude", sa.Float, nullable=False),
        sa.Column("longitude", sa.Float, nullable=False),
        sa.Column("altitude_m", sa.Float, nullable=False),
        sa.Column("bortle", sa.Integer, nullable=True),
        sa.Column("horizon_mask_path", sa.String(length=512), nullable=True),
        sa.Column("weather_sensors", sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("siteconfig")
