"""add capturelog table

Revision ID: 0005a_add_capturelog_table
Revises: 0005_add_ephemeris_table
Create Date: 2023-10-27 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0005a_add_capturelog_table'
down_revision = '0005_add_ephemeris_table'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('capturelog',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('target', sa.String(length=128), nullable=False),
    sa.Column('sequence', sa.String(length=128), nullable=True),
    sa.Column('index', sa.Integer(), nullable=True),
    sa.Column('path', sa.String(length=512), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_capturelog_created_at'), 'capturelog', ['created_at'], unique=False)
    op.create_index(op.f('ix_capturelog_index'), 'capturelog', ['index'], unique=False)
    op.create_index(op.f('ix_capturelog_kind'), 'capturelog', ['kind'], unique=False)
    op.create_index(op.f('ix_capturelog_sequence'), 'capturelog', ['sequence'], unique=False)
    op.create_index(op.f('ix_capturelog_started_at'), 'capturelog', ['started_at'], unique=False)
    op.create_index(op.f('ix_capturelog_target'), 'capturelog', ['target'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_capturelog_target'), table_name='capturelog')
    op.drop_index(op.f('ix_capturelog_started_at'), table_name='capturelog')
    op.drop_index(op.f('ix_capturelog_sequence'), table_name='capturelog')
    op.drop_index(op.f('ix_capturelog_kind'), table_name='capturelog')
    op.drop_index(op.f('ix_capturelog_index'), table_name='capturelog')
    op.drop_index(op.f('ix_capturelog_created_at'), table_name='capturelog')
    op.drop_table('capturelog')
