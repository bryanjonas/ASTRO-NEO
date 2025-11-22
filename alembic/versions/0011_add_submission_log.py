"""add submission log"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0011_submission_log"
down_revision = "0010_measurements"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "submissionlog",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("response", sa.Text(), nullable=True),
        sa.Column("report_path", sa.String(length=512), nullable=True),
        sa.Column("measurement_ids", sa.Text(), nullable=True),
        sa.Column("notes", sa.String(length=255), nullable=True),
    )
    op.create_index("ix_submissionlog_created_at", "submissionlog", ["created_at"])
    op.create_index("ix_submissionlog_status", "submissionlog", ["status"])


def downgrade() -> None:
    op.drop_index("ix_submissionlog_status", table_name="submissionlog")
    op.drop_index("ix_submissionlog_created_at", table_name="submissionlog")
    op.drop_table("submissionlog")
