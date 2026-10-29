"""Phase 10 additive immutable offline health snapshots.

Revision ID: f10a829d2c61
Revises: f08d719bac64
"""
from alembic import op
import sqlalchemy as sa
revision="f10a829d2c61"
down_revision="f08d719bac64"
branch_labels=None
depends_on=None

def upgrade():
    dialect=op.get_bind().dialect.name
    if dialect not in ("postgresql","sqlite"): raise RuntimeError("unsupported")
    op.create_table("platform_offline_health_events",
        sa.Column("id",sa.String(36),primary_key=True),
        sa.Column("strategy_id",sa.String(64),sa.ForeignKey("platform_strategies.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("sequence",sa.BigInteger(),nullable=False),
        sa.Column("actor_id",sa.String(128),nullable=False),
        sa.Column("actor_role",sa.String(24),nullable=False),
        sa.Column("report_json",sa.Text(),nullable=False),
        sa.Column("occurred_at_ms",sa.BigInteger(),nullable=False),
        sa.Column("previous_sha256",sa.String(64),nullable=False),
        sa.Column("content_sha256",sa.String(64),nullable=False),
        sa.Column("execution_authorized",sa.Boolean(),server_default=sa.false(),nullable=False),
        sa.UniqueConstraint("strategy_id","sequence",name="uq_offline_health_sequence"),
        sa.CheckConstraint("sequence>0",name="ck_offline_health_sequence"),
        sa.CheckConstraint("length(previous_sha256)=64 AND length(content_sha256)=64",name="ck_offline_health_hash"),
        sa.CheckConstraint("execution_authorized=false",name="ck_offline_health_no_authority"))
    op.create_index("ix_offline_health_time","platform_offline_health_events",["strategy_id","occurred_at_ms"])
    if dialect=="postgresql":
        op.execute(sa.text("""
            CREATE FUNCTION sterling_reject_offline_health_mutation() RETURNS TRIGGER AS $$
            BEGIN RAISE EXCEPTION 'immutable offline health evidence'; END;
            $$ LANGUAGE plpgsql"""))
        for verb in ("UPDATE","DELETE"):
            op.execute(sa.text(f"CREATE TRIGGER trg_offline_health_{verb.lower()} BEFORE {verb} ON platform_offline_health_events FOR EACH ROW EXECUTE FUNCTION sterling_reject_offline_health_mutation()"))
    else:
        for verb in ("UPDATE","DELETE"):
            op.execute(sa.text(f"CREATE TRIGGER trg_offline_health_{verb.lower()} BEFORE {verb} ON platform_offline_health_events BEGIN SELECT RAISE(ABORT,'immutable offline health evidence'); END"))

def downgrade():
    dialect=op.get_bind().dialect.name
    if dialect=="postgresql":
        for verb in ("update","delete"):
            op.execute(sa.text(f"DROP TRIGGER trg_offline_health_{verb} ON platform_offline_health_events"))
        op.execute(sa.text("DROP FUNCTION sterling_reject_offline_health_mutation()"))
    elif dialect=="sqlite":
        for verb in ("update","delete"):
            op.execute(sa.text(f"DROP TRIGGER trg_offline_health_{verb}"))
    else: raise RuntimeError("unsupported")
    op.drop_index("ix_offline_health_time",table_name="platform_offline_health_events")
    # Disposable CI only; never downgrade a production evidence ledger.
    op.drop_table("platform_offline_health_events")
