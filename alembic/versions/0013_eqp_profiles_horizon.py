"""add equipment profile table and horizon json column

Revision ID: 0013_eqp_profiles_horizon
Revises: 0012_add_mag_sigma
Create Date: 2025-01-01 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0013_eqp_profiles_horizon"
down_revision = "0012_add_mag_sigma"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("equipmentprofilerecord"):
        op.create_table(
            "equipmentprofilerecord",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=64), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("name"),
        )
        op.create_index(
            "ix_equipmentprofilerecord_is_active",
            "equipmentprofilerecord",
            ["is_active"],
            unique=False,
        )
        op.create_index(
            "ix_equipmentprofilerecord_name",
            "equipmentprofilerecord",
            ["name"],
            unique=False,
        )

    columns = [col["name"] for col in inspector.get_columns("siteconfig")]
    if "horizon_mask_json" not in columns:
        op.add_column(
            "siteconfig",
            sa.Column("horizon_mask_json", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col["name"] for col in inspector.get_columns("siteconfig")]
    if "horizon_mask_json" in columns:
        op.drop_column("siteconfig", "horizon_mask_json")

    if inspector.has_table("equipmentprofilerecord"):
        op.drop_index("ix_equipmentprofilerecord_name", table_name="equipmentprofilerecord")
        op.drop_index("ix_equipmentprofilerecord_is_active", table_name="equipmentprofilerecord")
        op.drop_table("equipmentprofilerecord")
