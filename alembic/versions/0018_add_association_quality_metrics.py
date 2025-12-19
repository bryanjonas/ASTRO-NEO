"""Add association quality metrics

Revision ID: b5c8d9e3f4a1
Revises: a4b8c7d9e2f3
Create Date: 2025-12-16

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b5c8d9e3f4a1'
down_revision = 'a4b8c7d9e2f3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add quality metrics columns to candidateassociation table
    op.add_column('candidateassociation', sa.Column('predicted_ra_deg', sa.Float(), nullable=True))
    op.add_column('candidateassociation', sa.Column('predicted_dec_deg', sa.Float(), nullable=True))
    op.add_column('candidateassociation', sa.Column('residual_arcsec', sa.Float(), nullable=True))
    op.add_column('candidateassociation', sa.Column('snr', sa.Float(), nullable=True))
    op.add_column('candidateassociation', sa.Column('peak_counts', sa.Float(), nullable=True))
    op.add_column('candidateassociation', sa.Column('method', sa.String(), server_default='auto', nullable=False))
    op.add_column('candidateassociation', sa.Column('stars_subtracted', sa.Integer(), nullable=True))
    op.add_column('candidateassociation', sa.Column('updated_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    # Remove added columns
    op.drop_column('candidateassociation', 'updated_at')
    op.drop_column('candidateassociation', 'stars_subtracted')
    op.drop_column('candidateassociation', 'method')
    op.drop_column('candidateassociation', 'peak_counts')
    op.drop_column('candidateassociation', 'snr')
    op.drop_column('candidateassociation', 'residual_arcsec')
    op.drop_column('candidateassociation', 'predicted_dec_deg')
    op.drop_column('candidateassociation', 'predicted_ra_deg')
