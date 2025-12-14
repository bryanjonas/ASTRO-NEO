"""Add composite_score and peak_altitude_deg to NeoObservability"""

revision = 'a4b8c7d9e2f3'
down_revision = 'f3a2b9c8d5e1'
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    # Add dynamic scoring fields to NeoObservability
    op.add_column('neoobservability', sa.Column('composite_score', sa.Float(), nullable=True))
    op.add_column('neoobservability', sa.Column('peak_altitude_deg', sa.Float(), nullable=True))


def downgrade() -> None:
    # Remove scoring fields from NeoObservability
    op.drop_column('neoobservability', 'peak_altitude_deg')
    op.drop_column('neoobservability', 'composite_score')
