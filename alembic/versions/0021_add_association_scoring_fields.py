"""add quality_grade and z_score to candidateassociation"""

from alembic import op
import sqlalchemy as sa


revision = "0021_add_association_scoring_fields"
down_revision = "0020_add_capturelog_solve_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "candidateassociation", sa.Column("quality_grade", sa.String(length=1), nullable=True)
    )
    op.add_column(
        "candidateassociation", sa.Column("z_score", sa.Float(), nullable=True)
    )


def downgrade() -> None:
    with op.batch_alter_table("candidateassociation") as batch:
        batch.drop_column("z_score")
        batch.drop_column("quality_grade")
