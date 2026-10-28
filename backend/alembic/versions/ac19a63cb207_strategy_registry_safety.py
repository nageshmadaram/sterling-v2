"""Registry write safety: append-only versions and same-transaction audit.

Revision ID: ac19a63cb207
Revises: 4d2c62ab8b01
"""
from alembic import op
import sqlalchemy as sa

revision = "ac19a63cb207"
down_revision = "4d2c62ab8b01"
branch_labels = None
depends_on = None

_ACTIONS = ("create_strategy", "update_strategy", "retire_strategy", "lock_strategy",
            "publish_version", "create_run")
_ACTION_SQL = ",".join("'" + x + "'" for x in _ACTIONS)


def _dialect():
    value = op.get_bind().dialect.name
    if value not in ("sqlite", "postgresql"):
        raise RuntimeError("Registry immutability requires SQLite or PostgreSQL triggers")
    return value


def _deny_mutation(dialect, table, prefix):
    if dialect == "sqlite":
        for action in ("UPDATE", "DELETE"):
            op.execute(sa.text(
                f"CREATE TRIGGER {prefix}_{action.lower()} BEFORE {action} ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'append-only registry record'); END"
            ))
    else:
        # Named per-table trigger, single shared function.
        for action in ("UPDATE", "DELETE"):
            op.execute(sa.text(
                f"CREATE TRIGGER {prefix}_{action.lower()} BEFORE {action} ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION sterling_reject_registry_mutation()"
            ))


def upgrade():
    dialect = _dialect()
    op.add_column("platform_strategies",
                  sa.Column("is_locked", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "platform_strategy_audit",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("actor_role", sa.String(24), nullable=False),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("resource_type", sa.String(32), nullable=False),
        sa.Column("resource_id", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(512), nullable=False),
        sa.Column("before_sha256", sa.String(64), nullable=True),
        sa.Column("after_sha256", sa.String(64), nullable=True),
        sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(f"action IN ({_ACTION_SQL})", name="ck_strategy_audit_action"),
        sa.CheckConstraint("length(reason) > 0", name="ck_strategy_audit_reason"),
    )
    op.create_index("ix_strategy_audit_resource", "platform_strategy_audit",
                    ["resource_type", "resource_id", "created_at_ms"])
    op.create_index("ix_strategy_audit_actor", "platform_strategy_audit",
                    ["actor_id", "created_at_ms"])
    if dialect == "postgresql":
        op.execute(sa.text("""
        CREATE FUNCTION sterling_reject_registry_mutation() RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'append-only registry record';
        END;
        $$ LANGUAGE plpgsql
        """))
    _deny_mutation(dialect, "platform_strategy_versions", "trg_strategy_versions_immutable")
    _deny_mutation(dialect, "platform_strategy_audit", "trg_strategy_audit_immutable")
    _deny_mutation(dialect, "platform_strategies", "trg_strategy_no_hard_delete")
    # UPDATE is allowed for strategy metadata; only DELETE must be prohibited.
    if dialect == "sqlite":
        op.execute(sa.text("DROP TRIGGER trg_strategy_no_hard_delete_update"))
    else:
        op.execute(sa.text("DROP TRIGGER trg_strategy_no_hard_delete_update ON platform_strategies"))
    # Once locked or retired, even direct UPDATE cannot re-open the registry.
    if dialect == "sqlite":
        op.execute(sa.text("""
        CREATE TRIGGER trg_strategy_state_monotone BEFORE UPDATE ON platform_strategies
        WHEN (OLD.is_locked = 1 AND NEW.is_locked <> 1)
          OR (OLD.state = 'retired' AND NEW.state <> 'retired')
        BEGIN SELECT RAISE(ABORT, 'locked or retired strategy cannot be reopened'); END
        """))
    else:
        op.execute(sa.text("""
        CREATE FUNCTION sterling_guard_strategy_state() RETURNS TRIGGER AS $
        BEGIN
            IF (OLD.is_locked AND NOT NEW.is_locked)
               OR (OLD.state = 'retired' AND NEW.state <> 'retired') THEN
                RAISE EXCEPTION 'locked or retired strategy cannot be reopened';
            END IF;
            RETURN NEW;
        END;
        $ LANGUAGE plpgsql
        """))
        op.execute(sa.text("""
        CREATE TRIGGER trg_strategy_state_monotone BEFORE UPDATE ON platform_strategies
        FOR EACH ROW EXECUTE FUNCTION sterling_guard_strategy_state()
        """))


def downgrade():
    dialect = _dialect()
    if dialect == "sqlite":
        op.execute(sa.text("DROP TRIGGER trg_strategy_state_monotone"))
    else:
        op.execute(sa.text("DROP TRIGGER trg_strategy_state_monotone ON platform_strategies"))
        op.execute(sa.text("DROP FUNCTION sterling_guard_strategy_state()"))
    for prefix, table, actions in (
        ("trg_strategy_versions_immutable", "platform_strategy_versions", ("update", "delete")),
        ("trg_strategy_audit_immutable", "platform_strategy_audit", ("update", "delete")),
        ("trg_strategy_no_hard_delete", "platform_strategies", ("delete",)),
    ):
        for action in actions:
            if dialect == "sqlite":
                op.execute(sa.text(f"DROP TRIGGER {prefix}_{action}"))
            else:
                op.execute(sa.text(f"DROP TRIGGER {prefix}_{action} ON {table}"))
    if dialect == "postgresql":
        op.execute(sa.text("DROP FUNCTION sterling_reject_registry_mutation()"))
    op.drop_index("ix_strategy_audit_actor", table_name="platform_strategy_audit")
    op.drop_index("ix_strategy_audit_resource", table_name="platform_strategy_audit")
    op.drop_table("platform_strategy_audit")
    op.drop_column("platform_strategies", "is_locked")
