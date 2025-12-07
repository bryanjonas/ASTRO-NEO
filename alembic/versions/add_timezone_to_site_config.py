"""add_timezone_to_site_config

Revision ID: add_timezone
Revises: 3011bfd99ca7
Create Date: 2025-11-29 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel

# revision identifiers, used by Alembic.
revision = 'add_timezone'
down_revision = '3011bfd99ca7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('siteconfig', sa.Column('timezone', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False, server_default='UTC'))


def downgrade() -> None:
    op.drop_column('siteconfig', 'timezone')
