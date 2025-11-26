"""ades compliance fields

Revision ID: 0014_ades_compliance
Revises: 0013_eqp_profiles_horizon
Create Date: 2025-11-25 20:40:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0014_ades_compliance"
down_revision = "0013_eqp_profiles_horizon"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    
    # Update SiteConfig
    columns = [col["name"] for col in inspector.get_columns("siteconfig")]
    if "telescope_design" not in columns:
        op.add_column("siteconfig", sa.Column("telescope_design", sa.String(), nullable=False, server_default="Reflector"))
    if "telescope_aperture" not in columns:
        op.add_column("siteconfig", sa.Column("telescope_aperture", sa.Float(), nullable=False, server_default="0.0"))
    if "telescope_detector" not in columns:
        op.add_column("siteconfig", sa.Column("telescope_detector", sa.String(), nullable=False, server_default="CCD"))

    # Update Measurement
    m_columns = [col["name"] for col in inspector.get_columns("measurement")]
    if "ast_cat" not in columns:
        op.add_column("measurement", sa.Column("ast_cat", sa.String(length=32), nullable=True, server_default="Gaia2"))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    
    # Downgrade SiteConfig
    columns = [col["name"] for col in inspector.get_columns("siteconfig")]
    if "telescope_design" in columns:
        op.drop_column("siteconfig", "telescope_design")
    if "telescope_aperture" in columns:
        op.drop_column("siteconfig", "telescope_aperture")
    if "telescope_detector" in columns:
        op.drop_column("siteconfig", "telescope_detector")

    # Downgrade Measurement
    m_columns = [col["name"] for col in inspector.get_columns("measurement")]
    if "ast_cat" in m_columns:
        op.drop_column("measurement", "ast_cat")
