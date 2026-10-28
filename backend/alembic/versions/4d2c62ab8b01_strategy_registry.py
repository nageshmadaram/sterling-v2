"""Offline-only versioned strategy registry.

Revision ID: 4d2c62ab8b01
Revises: efe2cc0f2c21
"""
from alembic import op
import sqlalchemy as sa

revision = "4d2c62ab8b01"
down_revision = "efe2cc0f2c21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_strategies",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("slug", sa.String(96), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
        sa.Column("updated_at_ms", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("owner_id", "slug", name="uq_platform_strategy_owner_slug"),
        sa.CheckConstraint("state IN ('draft','validated','armed','active','paused','failed','disabled','retired')", name="ck_platform_strategy_state"),
    )
    op.create_table(
        "platform_strategy_versions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("strategy_id", sa.String(64), sa.ForeignKey("platform_strategies.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version", sa.String(80), nullable=False),
        sa.Column("parameters_json", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("strategy_id", "version", name="uq_platform_version_strategy_version"),
    )
    op.create_index("ix_platform_version_strategy_created", "platform_strategy_versions", ["strategy_id", "created_at_ms"])
    op.create_table(
        "platform_strategy_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("version_id", sa.String(64), sa.ForeignKey("platform_strategy_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("requested_by", sa.String(128), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False, server_default="validation"),
        sa.Column("state", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
        sa.Column("updated_at_ms", sa.BigInteger(), nullable=False),
        sa.Column("last_heartbeat_ms", sa.BigInteger(), nullable=True),
        sa.Column("failure_code", sa.String(128), nullable=True),
        sa.CheckConstraint("mode IN ('validation','simulation')", name="ck_platform_run_offline_only"),
        sa.CheckConstraint("state IN ('idle','queued','active','paused','failed','stopped')", name="ck_platform_run_state"),
    )
    op.create_index("ix_platform_run_version_created", "platform_strategy_runs", ["version_id", "created_at_ms"])


def downgrade() -> None:
    op.drop_index("ix_platform_run_version_created", table_name="platform_strategy_runs")
    op.drop_table("platform_strategy_runs")
    op.drop_index("ix_platform_version_strategy_created", table_name="platform_strategy_versions")
    op.drop_table("platform_strategy_versions")
    op.drop_table("platform_strategies")
