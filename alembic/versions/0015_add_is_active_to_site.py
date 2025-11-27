"""add is_active to site

Revision ID: 0015_add_is_active_to_site
Revises: 0014_ades_compliance
Create Date: 2025-11-27 19:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0015_add_is_active_to_site"
down_revision = "0014_ades_compliance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    
    # Update SiteConfig
    columns = [col["name"] for col in inspector.get_columns("siteconfig")]
    if "is_active" not in columns:
        op.add_column("siteconfig", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()))
        op.create_index(op.f("ix_siteconfig_is_active"), "siteconfig", ["is_active"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    
    # Downgrade SiteConfig
    columns = [col["name"] for col in inspector.get_columns("siteconfig")]
    if "is_active" in columns:
        op.drop_index(op.f("ix_siteconfig_is_active"), table_name="siteconfig")
        op.drop_column("siteconfig", "is_active")
