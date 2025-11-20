"""add weather snapshot table"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0006_add_weather_snapshot"
down_revision = "0005_add_ephemeris_table"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "weathersnapshot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("sensor_name", sa.String(length=128), nullable=False),
        sa.Column("endpoint", sa.String(length=512), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("temperature_c", sa.Float(), nullable=True),
        sa.Column("wind_speed_mps", sa.Float(), nullable=True),
        sa.Column("relative_humidity_pct", sa.Float(), nullable=True),
        sa.Column("precipitation_probability_pct", sa.Float(), nullable=True),
        sa.Column("precipitation_mm", sa.Float(), nullable=True),
        sa.Column("cloud_cover_pct", sa.Float(), nullable=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_weathersnapshot_provider"),
        "weathersnapshot",
        ["provider"],
        unique=False,
    )
    op.create_index(
        op.f("ix_weathersnapshot_sensor_name"),
        "weathersnapshot",
        ["sensor_name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_weathersnapshot_fetched_at"),
        "weathersnapshot",
        ["fetched_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_weathersnapshot_created_at"),
        "weathersnapshot",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_weathersnapshot_created_at"), table_name="weathersnapshot")
    op.drop_index(op.f("ix_weathersnapshot_fetched_at"), table_name="weathersnapshot")
    op.drop_index(op.f("ix_weathersnapshot_sensor_name"), table_name="weathersnapshot")
    op.drop_index(op.f("ix_weathersnapshot_provider"), table_name="weathersnapshot")
    op.drop_table("weathersnapshot")
