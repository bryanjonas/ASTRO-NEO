"""merge astrometry and weather heads"""

from __future__ import annotations

from alembic import op


revision = "0008_merge_astrometry_weather"
down_revision = ("0006_add_astrometric_solution", "0006_add_weather_snapshot")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # This is a merge point; no schema changes.
    pass


def downgrade() -> None:
    # Cannot unmerge heads without data loss.
    pass
