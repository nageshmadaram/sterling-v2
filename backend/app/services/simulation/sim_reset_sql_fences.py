"""Fail-closed SQLite triggers for ordinary raw DML to virtual tenant tables.

These preserve per-owner reset admission across independent Python processes.
Only the verified reset coordinator may install a connection-local write permit
while holding SQLite's writer lock; there is deliberately no global unlock.
"""
from __future__ import annotations

TENANT_TABLES = (
    "sim_accounts", "sim_settings", "sim_orders", "sim_order_history",
    "sim_trades", "sim_positions", "sim_gtts", "sim_charges", "sim_equity",
)


def install(conn) -> None:
    """Idempotently create triggers for existing virtual tables on this database.

    Called at database init (covering already-existing books) and after the
    SimLedger creates virtual tables (covering the first boot). The SQL is built
    only from constants, not user identifiers.
    """
    existing = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'sim_%'"
    )}
    for table in TENANT_TABLES:
        if table not in existing:
            continue
        for operation in ("INSERT", "UPDATE", "DELETE"):
            record = "OLD" if operation == "DELETE" else "NEW"
            conn.execute(f"""
                CREATE TRIGGER IF NOT EXISTS sim_reset_guard_{table}_{operation.lower()}
                BEFORE {operation} ON {table}
                FOR EACH ROW
                WHEN EXISTS (
                    SELECT 1 FROM system_config
                    WHERE key = 'simulation_reset_fence_v1:sim:' || {record}.owner
                )
                BEGIN
                    SELECT CASE WHEN sterling_sim_reset_write_allowed({record}.owner) = 1
                         THEN 0 ELSE RAISE(ABORT, 'simulation_reset_fenced') END;
                END
            """)


__all__ = ["TENANT_TABLES", "install"]
