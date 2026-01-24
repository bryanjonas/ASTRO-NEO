"""switch neocandidate ids to text"""

from alembic import op
import sqlalchemy as sa


revision = "0019_neocandidate_id_text"
down_revision = "b5c8d9e3f4a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("neocandidate") as batch:
        batch.alter_column(
            "id",
            existing_type=sa.Integer(),
            type_=sa.String(),
            existing_nullable=False,
        )

    with op.batch_alter_table("neoephemeris") as batch:
        batch.alter_column(
            "candidate_id",
            existing_type=sa.Integer(),
            type_=sa.String(),
            existing_nullable=False,
        )

    with op.batch_alter_table("neoobservability") as batch:
        batch.alter_column(
            "candidate_id",
            existing_type=sa.Integer(),
            type_=sa.String(),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("neoobservability") as batch:
        batch.alter_column(
            "candidate_id",
            existing_type=sa.String(),
            type_=sa.Integer(),
            existing_nullable=False,
        )

    with op.batch_alter_table("neoephemeris") as batch:
        batch.alter_column(
            "candidate_id",
            existing_type=sa.String(),
            type_=sa.Integer(),
            existing_nullable=False,
        )

    with op.batch_alter_table("neocandidate") as batch:
        batch.alter_column(
            "id",
            existing_type=sa.String(),
            type_=sa.Integer(),
            existing_nullable=False,
        )
