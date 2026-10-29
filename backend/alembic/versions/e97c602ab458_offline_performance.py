"""Append-only offline hypothetical analytics records; not broker fills.

Revision ID: e97c602ab458
Revises: d86e31a4f290
"""
from alembic import op
import sqlalchemy as sa
revision="e97c602ab458"
down_revision="d86e31a4f290"
branch_labels=None
depends_on=None

def upgrade():
    dialect=op.get_bind().dialect.name
    if dialect not in ("postgresql","sqlite"):
        raise RuntimeError("unsupported immutable analytics dialect")
    op.create_table(
        "platform_hypothetical_trade_events",
        sa.Column("id",sa.String(36),primary_key=True),
        sa.Column("stream_id",sa.String(128),nullable=False),
        sa.Column("sequence",sa.BigInteger(),nullable=False),
        sa.Column("strategy_id",sa.String(64),sa.ForeignKey("platform_strategies.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("version_id",sa.String(64),sa.ForeignKey("platform_strategy_versions.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("entry_run_id",sa.String(64),sa.ForeignKey("platform_strategy_runs.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("exit_run_id",sa.String(64),sa.ForeignKey("platform_strategy_runs.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("entry_decision_id",sa.String(36),sa.ForeignKey("platform_decision_journal.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("exit_decision_id",sa.String(36),sa.ForeignKey("platform_decision_journal.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("entry_at_ms",sa.BigInteger(),nullable=False),
        sa.Column("exit_at_ms",sa.BigInteger(),nullable=False),
        sa.Column("actor_id",sa.String(128),nullable=False),
        sa.Column("actor_role",sa.String(24),nullable=False),
        sa.Column("reason",sa.String(512),nullable=False),
        sa.Column("evidence_kind",sa.String(32),nullable=False),
        sa.Column("execution_authorized",sa.Boolean(),nullable=False,server_default=sa.false()),
        sa.Column("payload_json",sa.Text(),nullable=False),
        sa.Column("source_sha256",sa.String(64),nullable=False),
        sa.Column("previous_sha256",sa.String(64),nullable=False),
        sa.Column("content_sha256",sa.String(64),nullable=False),
        sa.Column("occurred_at_ms",sa.BigInteger(),nullable=False),
        sa.UniqueConstraint("stream_id","sequence",name="uq_hyp_trade_sequence"),
        sa.UniqueConstraint("entry_decision_id","exit_decision_id",name="uq_hyp_trade_pair"),
        sa.UniqueConstraint("exit_decision_id",name="uq_hyp_unique_exit_decision"),
        sa.CheckConstraint("sequence > 0",name="ck_hyp_trade_sequence"),
        sa.CheckConstraint("execution_authorized = false",name="ck_hyp_offline_only"),
        sa.CheckConstraint("evidence_kind = 'offline_hypothetical'",name="ck_hyp_evidence_kind"),
        sa.CheckConstraint("length(previous_sha256)=64 AND length(content_sha256)=64",name="ck_hyp_hashes"),
    )
    op.create_index("ix_hyp_trade_strategy_time","platform_hypothetical_trade_events",["strategy_id","exit_at_ms"])
    op.create_index("ix_hyp_trade_version_time","platform_hypothetical_trade_events",["version_id","exit_at_ms"])
    if dialect=="sqlite":
        for action in ("UPDATE","DELETE"):
            op.execute(sa.text(f"CREATE TRIGGER trg_hyp_trade_immutable_{action.lower()} BEFORE {action} ON platform_hypothetical_trade_events BEGIN SELECT RAISE(ABORT,'immutable offline analytical evidence'); END"))
    else:
        op.execute(sa.text("""
        CREATE FUNCTION sterling_reject_hyp_trade_mutation() RETURNS TRIGGER AS $$
        BEGIN RAISE EXCEPTION 'immutable offline analytical evidence'; END;
        $$ LANGUAGE plpgsql
        """))
        for action in ("UPDATE","DELETE"):
            op.execute(sa.text(f"CREATE TRIGGER trg_hyp_trade_immutable_{action.lower()} BEFORE {action} ON platform_hypothetical_trade_events FOR EACH ROW EXECUTE FUNCTION sterling_reject_hyp_trade_mutation()"))

def downgrade():
    dialect=op.get_bind().dialect.name
    if dialect not in ("postgresql","sqlite"):
        raise RuntimeError("unsupported dialect")
    # DESTRUCTIVE — disposable CI DB ONLY.
    for action in ("update","delete"):
        if dialect=="sqlite":
            op.execute(sa.text(f"DROP TRIGGER trg_hyp_trade_immutable_{action}"))
        else:
            op.execute(sa.text(f"DROP TRIGGER trg_hyp_trade_immutable_{action} ON platform_hypothetical_trade_events"))
    if dialect=="postgresql":
        op.execute(sa.text("DROP FUNCTION sterling_reject_hyp_trade_mutation()"))
    op.drop_index("ix_hyp_trade_version_time",table_name="platform_hypothetical_trade_events")
    op.drop_index("ix_hyp_trade_strategy_time",table_name="platform_hypothetical_trade_events")
    op.drop_table("platform_hypothetical_trade_events")
