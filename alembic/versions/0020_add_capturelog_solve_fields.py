"""add capturelog solve metadata fields"""

from alembic import op
import sqlalchemy as sa


revision = "0020_add_capturelog_solve_fields"
down_revision = "0019_neocandidate_id_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Clean up leftover temp table from a previously failed batch migration.
    bind = op.get_bind()
    bind.execute(sa.text("DROP TABLE IF EXISTS _alembic_tmp_capturelog"))
    op.add_column("capturelog", sa.Column("predicted_ra_deg", sa.Float(), nullable=True))
    op.add_column("capturelog", sa.Column("predicted_dec_deg", sa.Float(), nullable=True))
    op.add_column("capturelog", sa.Column("filter_name", sa.String(length=32), nullable=True))
    op.add_column("capturelog", sa.Column("binning", sa.Integer(), nullable=True))
    op.add_column("capturelog", sa.Column("exposure_seconds", sa.Float(), nullable=True))
    op.add_column(
        "capturelog",
        sa.Column("has_wcs", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column("capturelog", sa.Column("solved_ra_deg", sa.Float(), nullable=True))
    op.add_column("capturelog", sa.Column("solved_dec_deg", sa.Float(), nullable=True))
    op.add_column("capturelog", sa.Column("error_message", sa.String(length=1024), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("capturelog") as batch:
        batch.drop_column("error_message")
        batch.drop_column("solved_dec_deg")
        batch.drop_column("solved_ra_deg")
        batch.drop_column("has_wcs")
        batch.drop_column("exposure_seconds")
        batch.drop_column("binning")
        batch.drop_column("filter_name")
        batch.drop_column("predicted_dec_deg")
        batch.drop_column("predicted_ra_deg")
