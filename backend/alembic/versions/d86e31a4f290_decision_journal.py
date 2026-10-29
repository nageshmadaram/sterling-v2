"""Offline decision journal, append-only and execution-authority-free.

Revision ID: d86e31a4f290
Revises: c75f92d1a341
"""
from alembic import op
import sqlalchemy as sa

revision="d86e31a4f290"
down_revision="c75f92d1a341"
branch_labels=None
depends_on=None

def upgrade():
    dialect=op.get_bind().dialect.name
    if dialect not in ("sqlite","postgresql"):
        raise RuntimeError("immutable journal requires SQLite or PostgreSQL triggers")
    op.create_table(
        "platform_decision_journal",
        sa.Column("id",sa.String(36),primary_key=True),
        sa.Column("stream_id",sa.String(128),nullable=False),
        sa.Column("sequence",sa.BigInteger(),nullable=False),
        sa.Column("orchestration_event_id",sa.String(36),sa.ForeignKey("platform_orchestration_events.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("strategy_id",sa.String(64),sa.ForeignKey("platform_strategies.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("version_id",sa.String(64),sa.ForeignKey("platform_strategy_versions.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("run_id",sa.String(64),sa.ForeignKey("platform_strategy_runs.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("item_id",sa.String(64)),
        sa.Column("signal_id",sa.String(128),nullable=False),
        sa.Column("decision_type",sa.String(40),nullable=False),
        sa.Column("actor_id",sa.String(128),nullable=False),
        sa.Column("actor_role",sa.String(24),nullable=False),
        sa.Column("reason",sa.String(512),nullable=False),
        sa.Column("risk_state",sa.String(64),nullable=False),
        sa.Column("approval_mode",sa.String(32),nullable=False),
        sa.Column("approval_status",sa.String(32),nullable=False),
        sa.Column("queue_state",sa.String(32),nullable=False),
        sa.Column("run_state",sa.String(32),nullable=False),
        sa.Column("data_quality_json",sa.Text(),nullable=False),
        sa.Column("risk_event_id",sa.String(36)),
        sa.Column("approval_event_id",sa.String(36)),
        sa.Column("risk_policy_sha256",sa.String(64),nullable=False),
        sa.Column("version_sha256",sa.String(64),nullable=False),
        sa.Column("lineage_sha256",sa.String(64),nullable=False),
        sa.Column("payload_json",sa.Text(),nullable=False),
        sa.Column("execution_authorized",sa.Boolean(),nullable=False,server_default=sa.false()),
        sa.Column("previous_sha256",sa.String(64),nullable=False),
        sa.Column("content_sha256",sa.String(64),nullable=False),
        sa.Column("occurred_at_ms",sa.BigInteger(),nullable=False),
        sa.UniqueConstraint("stream_id","sequence",name="uq_decision_journal_sequence"),
        sa.UniqueConstraint("orchestration_event_id",name="uq_decision_journal_orchestration_event"),
        sa.CheckConstraint("sequence > 0",name="ck_decision_journal_positive_seq"),
        sa.CheckConstraint("length(previous_sha256)=64 AND length(content_sha256)=64",name="ck_decision_journal_hash"),
        sa.CheckConstraint("execution_authorized = false",name="ck_decision_journal_offline"),
    )
    op.create_index("ix_decision_journal_run","platform_decision_journal",["run_id","occurred_at_ms"])
    op.create_index("ix_decision_journal_item","platform_decision_journal",["item_id","sequence"])
    if dialect == "sqlite":
        for action in ("UPDATE","DELETE"):
            op.execute(sa.text(f"CREATE TRIGGER trg_decision_journal_immutable_{action.lower()} BEFORE {action} ON platform_decision_journal BEGIN SELECT RAISE(ABORT,'immutable offline decision journal'); END"))
    else:
        op.execute(sa.text("""
        CREATE FUNCTION sterling_reject_decision_journal_mutation() RETURNS TRIGGER AS $$
        BEGIN RAISE EXCEPTION 'immutable offline decision journal'; END;
        $$ LANGUAGE plpgsql
        """))
        for action in ("UPDATE","DELETE"):
            op.execute(sa.text(f"CREATE TRIGGER trg_decision_journal_immutable_{action.lower()} BEFORE {action} ON platform_decision_journal FOR EACH ROW EXECUTE FUNCTION sterling_reject_decision_journal_mutation()"))

def downgrade():
    dialect=op.get_bind().dialect.name
    if dialect not in ("sqlite","postgresql"):
        raise RuntimeError("unsupported dialect")
    # DESTRUCTIVE and authorized only against disposable CI DB.
    for action in ("update","delete"):
        if dialect == "sqlite":
            op.execute(sa.text(f"DROP TRIGGER trg_decision_journal_immutable_{action}"))
        else:
            op.execute(sa.text(f"DROP TRIGGER trg_decision_journal_immutable_{action} ON platform_decision_journal"))
    if dialect == "postgresql":
        op.execute(sa.text("DROP FUNCTION sterling_reject_decision_journal_mutation()"))
    op.drop_index("ix_decision_journal_item",table_name="platform_decision_journal")
    op.drop_index("ix_decision_journal_run",table_name="platform_decision_journal")
    op.drop_table("platform_decision_journal")
