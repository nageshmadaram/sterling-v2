"""Destructive ONLY against the explicitly named disposable CI database.

Validates PostgreSQL migration on actual Sterling metadata and the registry's
transaction/immutability/ownership invariants without touching broker code.
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.persistence.base import make_engine
from app.persistence.session import make_session_factory
from app.persistence import strategy_registry_service as registry


def main():
    url = os.environ.get("DATABASE_URL", "")
    if os.environ.get("STERLING_REGISTRY_DISPOSABLE_DB") != "yes":
        raise RuntimeError("Explicit disposable-database permission required")
    if not url.startswith("postgresql+") or not url.split("?", 1)[0].endswith("/sterling_registry_ci"):
        raise RuntimeError("Refuse any database except sterling_registry_ci")
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    command.upgrade(cfg, "head")
    engine = make_engine(url)
    assert {"platform_strategies", "platform_strategy_versions", "platform_strategy_runs",
            "platform_strategy_audit"} <= set(inspect(engine).get_table_names())
    from app.core.auth import UserContext
    original_identity = registry._identity
    registry._identity = lambda token: (UserContext(user_id="owner", role="admin")
                                         if token == "synthetic-admin" else
                                         (_ for _ in ()).throw(PermissionError("denied")))
    try:
        svc = registry.StrategyRegistryService(make_session_factory(engine))
        strategy = svc.create_strategy(access_token="synthetic-admin",
                                       slug="pg-smoke", display_name="Postgres smoke",
                                       reason="isolated database validation")
        ver = svc.publish_version(access_token="synthetic-admin", strategy_id=strategy,
                                  version="v1", parameters={"a": 1},
                                  reason="validate append-only")
        assert ver
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM platform_strategy_audit")).scalar_one() == 2
        for sql in (
            "UPDATE platform_strategy_versions SET parameters_json='{}'",
            "DELETE FROM platform_strategy_versions",
            "UPDATE platform_strategy_audit SET reason='tampered'",
            "DELETE FROM platform_strategy_audit",
        ):
            try:
                with engine.begin() as conn:
                    conn.execute(text(sql))
            except DBAPIError:
                pass
            else:
                raise AssertionError("mutable append-only record: " + sql)
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE OR REPLACE FUNCTION sterling_ci_reject_audit() RETURNS trigger AS $$
                BEGIN RAISE EXCEPTION 'synthetic audit failure'; END;
                $$ LANGUAGE plpgsql
            """))
            conn.execute(text("""
                CREATE TRIGGER sterling_ci_reject_audit BEFORE INSERT ON platform_strategy_audit
                FOR EACH ROW EXECUTE FUNCTION sterling_ci_reject_audit()
            """))
        try:
            try:
                svc.publish_version(access_token="synthetic-admin", strategy_id=strategy,
                                    version="v2", parameters={"a": 2}, reason="forced rollback")
            except DBAPIError:
                pass
            else:
                raise AssertionError("audit outage did not roll back version")
            with engine.connect() as conn:
                assert conn.execute(text("SELECT count(*) FROM platform_strategy_versions")).scalar_one() == 1
        finally:
            with engine.begin() as conn:
                conn.execute(text("DROP TRIGGER sterling_ci_reject_audit ON platform_strategy_audit"))
                conn.execute(text("DROP FUNCTION sterling_ci_reject_audit()"))
        svc.lock_strategy(access_token="synthetic-admin", strategy_id=strategy,
                          reason="one-way lock")
        try:
            with engine.begin() as conn:
                conn.execute(text("UPDATE platform_strategies SET is_locked=false WHERE id=:id"), {"id": strategy})
        except DBAPIError:
            pass
        else:
            raise AssertionError("locked state can be cleared by SQL")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM platform_strategy_audit")).scalar_one() == 3
        print("PASS: PostgreSQL Alembic + immutability + transactional audit rollback + lock")
    finally:
        registry._identity = original_identity
        engine.dispose()


if __name__ == "__main__":
    main()
