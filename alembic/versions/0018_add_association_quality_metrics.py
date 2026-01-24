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
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "candidateassociation" not in table_names:
        op.create_table(
            "candidateassociation",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("capture_id", sa.Integer(), sa.ForeignKey("capturelog.id"), nullable=False),
            sa.Column("ra_deg", sa.Float(), nullable=False),
            sa.Column("dec_deg", sa.Float(), nullable=False),
            sa.Column("predicted_ra_deg", sa.Float(), nullable=True),
            sa.Column("predicted_dec_deg", sa.Float(), nullable=True),
            sa.Column("residual_arcsec", sa.Float(), nullable=True),
            sa.Column("snr", sa.Float(), nullable=True),
            sa.Column("peak_counts", sa.Float(), nullable=True),
            sa.Column("method", sa.String(), server_default="auto", nullable=False),
            sa.Column("stars_subtracted", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        return

    with op.batch_alter_table("candidateassociation") as batch:
        batch.add_column(sa.Column("predicted_ra_deg", sa.Float(), nullable=True))
        batch.add_column(sa.Column("predicted_dec_deg", sa.Float(), nullable=True))
        batch.add_column(sa.Column("residual_arcsec", sa.Float(), nullable=True))
        batch.add_column(sa.Column("snr", sa.Float(), nullable=True))
        batch.add_column(sa.Column("peak_counts", sa.Float(), nullable=True))
        batch.add_column(sa.Column("method", sa.String(), server_default="auto", nullable=False))
        batch.add_column(sa.Column("stars_subtracted", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("updated_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "candidateassociation" not in inspector.get_table_names():
        return

    with op.batch_alter_table("candidateassociation") as batch:
        batch.drop_column("updated_at")
        batch.drop_column("stars_subtracted")
        batch.drop_column("method")
        batch.drop_column("peak_counts")
        batch.drop_column("snr")
        batch.drop_column("residual_arcsec")
        batch.drop_column("predicted_dec_deg")
        batch.drop_column("predicted_ra_deg")
