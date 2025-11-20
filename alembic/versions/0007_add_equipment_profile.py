"""add equipment profile column"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0007_add_equipment_profile"
down_revision = "0006_add_weather_snapshot"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.add_column(
        "siteconfig",
        sa.Column("equipment_profile", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("siteconfig", "equipment_profile")
