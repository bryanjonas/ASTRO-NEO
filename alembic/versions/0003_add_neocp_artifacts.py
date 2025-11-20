"""add tables for neocp snapshots and observation payloads"""

from alembic import op
import sqlalchemy as sa


revision = "0003_add_neocp_artifacts"
down_revision = "0002_add_neocandidate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "neocpsnapshot",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source_url", sa.String(length=512), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("html", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=False),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("checksum", name="uq_neocp_snapshot_checksum"),
    )
    op.create_index("ix_neocpsnapshot_fetched_at", "neocpsnapshot", ["fetched_at"])
    op.create_index("ix_neocpsnapshot_checksum", "neocpsnapshot", ["checksum"])

    op.create_table(
        "neoobservationpayload",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("trksub", sa.String(length=16), nullable=False),
        sa.Column("output_format", sa.String(length=16), nullable=False),
        sa.Column("ades_version", sa.String(length=8), nullable=False),
        sa.Column("payload_json", sa.Text, nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=False),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "trksub",
            "output_format",
            "checksum",
            name="uq_neocp_obs_trksub_format_checksum",
        ),
    )
    op.create_index("ix_neoobservationpayload_trksub", "neoobservationpayload", ["trksub"])
    op.create_index(
        "ix_neoobservationpayload_fetched_at", "neoobservationpayload", ["fetched_at"]
    )
    op.create_index("ix_neoobservationpayload_checksum", "neoobservationpayload", ["checksum"])


def downgrade() -> None:
    op.drop_index("ix_neoobservationpayload_checksum", table_name="neoobservationpayload")
    op.drop_index("ix_neoobservationpayload_fetched_at", table_name="neoobservationpayload")
    op.drop_index("ix_neoobservationpayload_trksub", table_name="neoobservationpayload")
    op.drop_table("neoobservationpayload")

    op.drop_index("ix_neocpsnapshot_checksum", table_name="neocpsnapshot")
    op.drop_index("ix_neocpsnapshot_fetched_at", table_name="neocpsnapshot")
    op.drop_table("neocpsnapshot")
