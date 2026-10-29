"""Append-only OFFLINE execution gate ledger; no broker authority.

Revision ID: b64f3c27e190
Revises: ac19a63cb207
"""
from alembic import op
import sqlalchemy as sa

revision = "b64f3c27e190"
down_revision = "ac19a63cb207"
branch_labels = None
depends_on = None

def upgrade():
    dialect = op.get_bind().dialect.name
    if dialect not in ("sqlite","postgresql"):
        raise RuntimeError("immutable gate events require a supported trigger dialect")
    op.create_table(
        "platform_gate_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("stream_id", sa.String(128), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("strategy_id", sa.String(64), sa.ForeignKey("platform_strategies.id", ondelete="RESTRICT")),
        sa.Column("version_id", sa.String(64), sa.ForeignKey("platform_strategy_versions.id", ondelete="RESTRICT")),
        sa.Column("run_id", sa.String(64), sa.ForeignKey("platform_strategy_runs.id", ondelete="RESTRICT")),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("actor_role", sa.String(24), nullable=False),
        sa.Column("reason", sa.String(512), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("previous_sha256", sa.String(64), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("occurred_at_ms", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("stream_id","sequence",name="uq_gate_stream_sequence"),
        sa.CheckConstraint("sequence > 0", name="ck_gate_positive_sequence"),
        sa.CheckConstraint("kind IN ('state_transition','operator_approval','signal_received','gate_evaluated','risk_decision','approval_decision')", name="ck_gate_event_kind"),
        sa.CheckConstraint("length(previous_sha256) = 64 AND length(content_sha256) = 64",name="ck_gate_hash_lengths"),
    )
    op.create_index("ix_gate_strategy_time","platform_gate_events",["strategy_id","occurred_at_ms"])
    op.create_index("ix_gate_run_time","platform_gate_events",["run_id","occurred_at_ms"])
    if dialect == "sqlite":
        for action in ("UPDATE","DELETE"):
            op.execute(sa.text(f"CREATE TRIGGER trg_gate_immutable_{action.lower()} BEFORE {action} ON platform_gate_events BEGIN SELECT RAISE(ABORT,'immutable offline gate ledger'); END"))
    else:
        op.execute(sa.text("""
        CREATE FUNCTION sterling_reject_gate_mutation() RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'immutable offline gate ledger';
        END;
        $$ LANGUAGE plpgsql
        """))
        for action in ("UPDATE","DELETE"):
            op.execute(sa.text(f"CREATE TRIGGER trg_gate_immutable_{action.lower()} BEFORE {action} ON platform_gate_events FOR EACH ROW EXECUTE FUNCTION sterling_reject_gate_mutation()"))

def downgrade():
    dialect = op.get_bind().dialect.name
    # Downgrade destroys evidence and must ONLY be used in a disposable CI DB.
    for action in ("update","delete"):
        if dialect == "sqlite":
            op.execute(sa.text(f"DROP TRIGGER trg_gate_immutable_{action}"))
        elif dialect == "postgresql":
            op.execute(sa.text(f"DROP TRIGGER trg_gate_immutable_{action} ON platform_gate_events"))
        else:
            raise RuntimeError("unsupported backend")
    if dialect == "postgresql":
        op.execute(sa.text("DROP FUNCTION sterling_reject_gate_mutation()"))
    op.drop_index("ix_gate_run_time",table_name="platform_gate_events")
    op.drop_index("ix_gate_strategy_time",table_name="platform_gate_events")
    op.drop_table("platform_gate_events")
