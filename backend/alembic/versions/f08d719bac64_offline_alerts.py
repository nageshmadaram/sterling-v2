"""Phase 9 offline durable informational alert events; append-only.

Revision ID: f08d719bac64
Revises: e97c602ab458
"""
from alembic import op
import sqlalchemy as sa
revision="f08d719bac64"
down_revision="e97c602ab458"
branch_labels=None
depends_on=None

def upgrade():
    dialect=op.get_bind().dialect.name
    if dialect not in ("sqlite","postgresql"): raise RuntimeError("immutable audit dialect unsupported")
    op.create_table("platform_offline_alert_events",
        sa.Column("id",sa.String(36),primary_key=True),
        sa.Column("alert_id",sa.String(64),nullable=False),
        sa.Column("strategy_id",sa.String(64),sa.ForeignKey("platform_strategies.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("sequence",sa.BigInteger(),nullable=False),
        sa.Column("alert_sequence",sa.BigInteger(),nullable=False),
        sa.Column("event_kind",sa.String(32),nullable=False),
        sa.Column("actor_id",sa.String(128),nullable=False),
        sa.Column("actor_role",sa.String(32),nullable=False),
        sa.Column("reason",sa.String(512),nullable=False),
        sa.Column("payload_json",sa.Text(),nullable=False),
        sa.Column("occurred_at_ms",sa.BigInteger(),nullable=False),
        sa.Column("previous_sha256",sa.String(64),nullable=False),
        sa.Column("content_sha256",sa.String(64),nullable=False),
        sa.Column("execution_authorized",sa.Boolean(),nullable=False,server_default=sa.false()),
        sa.UniqueConstraint("strategy_id","sequence",name="uq_offline_alert_sequence"),
        sa.UniqueConstraint("alert_id","alert_sequence",name="uq_offline_alert_revision"),
        sa.CheckConstraint("sequence > 0 AND alert_sequence > 0",name="ck_offline_alert_sequences"),
        sa.CheckConstraint("execution_authorized = false",name="ck_offline_alert_no_execution"),
        sa.CheckConstraint("length(previous_sha256)=64 AND length(content_sha256)=64",name="ck_offline_alert_hashes"),
        sa.CheckConstraint("event_kind IN ('created','acknowledged','resolved','expired','delivery_pending','delivery_sent','delivery_failed','delivery_acknowledged','delivery_expired','escalated')",name="ck_offline_alert_kind"))
    op.create_index("ix_offline_alert_owner_time","platform_offline_alert_events",["strategy_id","occurred_at_ms"])
    op.create_index("ix_offline_alert_id","platform_offline_alert_events",["alert_id"])
    if dialect=="sqlite":
        for verb in ("update","delete"):
            op.execute(sa.text(f"CREATE TRIGGER trg_offline_alert_{verb}_deny BEFORE {verb.upper()} ON platform_offline_alert_events BEGIN SELECT RAISE(ABORT, 'immutable offline alert'); END"))
    else:
        op.execute(sa.text("""
        CREATE FUNCTION sterling_reject_offline_alert_mutation() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'immutable offline alert'; END;
        $$ LANGUAGE plpgsql
        """))
        for verb in ("update","delete"):
            op.execute(sa.text(f"CREATE TRIGGER trg_offline_alert_{verb}_deny BEFORE {verb.upper()} ON platform_offline_alert_events FOR EACH ROW EXECUTE FUNCTION sterling_reject_offline_alert_mutation()"))

def downgrade():
    if op.get_bind().dialect.name not in ("sqlite","postgresql"): raise RuntimeError("unsupported")
    if op.get_bind().dialect.name=="postgresql":
        for verb in ("update","delete"):
            op.execute(sa.text(f"DROP TRIGGER trg_offline_alert_{verb}_deny ON platform_offline_alert_events"))
        op.execute(sa.text("DROP FUNCTION sterling_reject_offline_alert_mutation()"))
    else:
        for verb in ("update","delete"):
            op.execute(sa.text(f"DROP TRIGGER trg_offline_alert_{verb}_deny"))
    op.drop_index("ix_offline_alert_id",table_name="platform_offline_alert_events")
    op.drop_index("ix_offline_alert_owner_time",table_name="platform_offline_alert_events")
    # Destructive; only disposable test database.
    op.drop_table("platform_offline_alert_events")
