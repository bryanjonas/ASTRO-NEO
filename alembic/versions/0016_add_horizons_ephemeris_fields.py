"""Add Horizons ephemeris fields"""

revision = 'f3a2b9c8d5e1'
down_revision = 'add_timezone'
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
import sqlmodel


def upgrade() -> None:
    # Add Horizons-specific fields to NeoEphemeris
    op.add_column('neoephemeris', sa.Column('ra_rate_arcsec_min', sa.Float(), nullable=True))
    op.add_column('neoephemeris', sa.Column('dec_rate_arcsec_min', sa.Float(), nullable=True))
    op.add_column('neoephemeris', sa.Column('azimuth_deg', sa.Float(), nullable=True))
    op.add_column('neoephemeris', sa.Column('elevation_deg', sa.Float(), nullable=True))
    op.add_column('neoephemeris', sa.Column('airmass', sa.Float(), nullable=True))
    op.add_column('neoephemeris', sa.Column('solar_elongation_deg', sa.Float(), nullable=True))
    op.add_column('neoephemeris', sa.Column('lunar_elongation_deg', sa.Float(), nullable=True))
    op.add_column('neoephemeris', sa.Column('v_mag_predicted', sa.Float(), nullable=True))
    op.add_column('neoephemeris', sa.Column('uncertainty_3sigma_arcsec', sa.Float(), nullable=True))
    op.add_column('neoephemeris', sa.Column('source', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False, server_default='MPC'))

    # Add last observation time to NeoCandidate
    op.add_column('neocandidate', sa.Column('last_obs_utc', sa.DateTime(), nullable=True))


def downgrade() -> None:
    # Remove Horizons fields from NeoEphemeris
    op.drop_column('neoephemeris', 'uncertainty_3sigma_arcsec')
    op.drop_column('neoephemeris', 'v_mag_predicted')
    op.drop_column('neoephemeris', 'lunar_elongation_deg')
    op.drop_column('neoephemeris', 'solar_elongation_deg')
    op.drop_column('neoephemeris', 'airmass')
    op.drop_column('neoephemeris', 'elevation_deg')
    op.drop_column('neoephemeris', 'azimuth_deg')
    op.drop_column('neoephemeris', 'dec_rate_arcsec_min')
    op.drop_column('neoephemeris', 'ra_rate_arcsec_min')
    op.drop_column('neoephemeris', 'source')

    # Remove last observation time from NeoCandidate
    op.drop_column('neocandidate', 'last_obs_utc')
