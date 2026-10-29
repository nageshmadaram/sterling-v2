"""Phase 5 append-only dry-run scheduler records, separate from order execution.

Revision ID: c75f92d1a341
Revises: b64f3c27e190
"""
from alembic import op
import sqlalchemy as sa

revision = "c75f92d1a341"
down_revision = "b64f3c27e190"
branch_labels = None
depends_on = None

def upgrade():
    dialect = op.get_bind().dialect.name
    if dialect not in ("sqlite", "postgresql"):
        raise RuntimeError("offline orchestration immutability requires SQLite or PostgreSQL")
    op.create_table(
        "platform_orchestration_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("stream_id", sa.String(128), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("strategy_id", sa.String(64), sa.ForeignKey("platform_strategies.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version_id", sa.String(64), sa.ForeignKey("platform_strategy_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("run_id", sa.String(64), sa.ForeignKey("platform_strategy_runs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("schedule_id", sa.String(64), nullable=False),
        sa.Column("item_id", sa.String(64)),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("actor_role", sa.String(24), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("reason", sa.String(512), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("previous_sha256", sa.String(64), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("occurred_at_ms", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("stream_id", "sequence", name="uq_orchestration_stream_sequence"),
        sa.CheckConstraint("sequence > 0", name="ck_orchestration_positive_sequence"),
        sa.CheckConstraint("kind IN ('schedule_created','schedule_paused','schedule_cancelled','queue_transition','run_transition','gate_binding','requeue_denied','operator_alert')", name="ck_orchestration_event_kind"),
        sa.CheckConstraint("length(previous_sha256)=64 AND length(content_sha256)=64", name="ck_orchestration_hash_lengths"),
    )
    op.create_index("ix_orchestration_run_time", "platform_orchestration_events", ["run_id", "occurred_at_ms"])
    op.create_index("ix_orchestration_schedule_time", "platform_orchestration_events", ["schedule_id", "occurred_at_ms"])
    op.create_index("ix_orchestration_item_time", "platform_orchestration_events", ["item_id", "occurred_at_ms"])
    if dialect == "sqlite":
        for action in ("UPDATE", "DELETE"):
            op.execute(sa.text(
                f"CREATE TRIGGER trg_orchestration_immutable_{action.lower()} "
                f"BEFORE {action} ON platform_orchestration_events "
                "BEGIN SELECT RAISE(ABORT, 'immutable offline schedule events'); END"
            ))
    else:
        op.execute(sa.text("""
        CREATE FUNCTION sterling_reject_orchestration_mutation() RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'immutable offline schedule events';
        END;
        $$ LANGUAGE plpgsql
        """))
        for action in ("UPDATE", "DELETE"):
            op.execute(sa.text(
                f"CREATE TRIGGER trg_orchestration_immutable_{action.lower()} BEFORE {action} "
                "ON platform_orchestration_events FOR EACH ROW "
                "EXECUTE FUNCTION sterling_reject_orchestration_mutation()"
            ))

def downgrade():
    dialect = op.get_bind().dialect.name
    # DESTRUCTIVE: only allowed on explicitly disposable CI databases.
    if dialect not in ("sqlite", "postgresql"):
        raise RuntimeError("unsupported dialect")
    for action in ("update", "delete"):
        if dialect == "sqlite":
            op.execute(sa.text(f"DROP TRIGGER trg_orchestration_immutable_{action}"))
        else:
            op.execute(sa.text(f"DROP TRIGGER trg_orchestration_immutable_{action} ON platform_orchestration_events"))
    if dialect == "postgresql":
        op.execute(sa.text("DROP FUNCTION sterling_reject_orchestration_mutation()"))
    for index in ("ix_orchestration_item_time", "ix_orchestration_schedule_time", "ix_orchestration_run_time"):
        op.drop_index(index, table_name="platform_orchestration_events")
    op.drop_table("platform_orchestration_events")
