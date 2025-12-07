"""Add session persistence"""

revision = '3011bfd99ca7'
down_revision = '0015_add_is_active_to_site'
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
import sqlmodel

def upgrade() -> None:
    # Create observing_sessions table
    op.create_table('observing_sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('start_time', sa.DateTime(), nullable=False),
        sa.Column('end_time', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('target_mode', sa.String(), nullable=False),
        sa.Column('selected_target', sa.String(), nullable=True),
        sa.Column('window_start', sa.String(), nullable=True),
        sa.Column('window_end', sa.String(), nullable=True),
        sa.Column('config_snapshot', sa.JSON(), nullable=True),
        sa.Column('stats', sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    # Create system_events table
    op.create_table('system_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('level', sa.String(), nullable=False),
        sa.Column('message', sa.String(), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['session_id'], ['observing_sessions.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

def downgrade() -> None:
    op.drop_table('system_events')
    op.drop_table('observing_sessions')
