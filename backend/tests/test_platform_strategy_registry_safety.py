"""Registry safety tests. Uses only disposable SQLite + synthetic identities."""
from types import SimpleNamespace
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, DBAPIError

from app.persistence.base import make_engine
from app.persistence.session import make_session_factory
from app.persistence import strategy_registry_service as registry


@pytest.fixture()
def store(tmp_path, monkeypatch):
    from app.core.config import settings
    db_url = f"sqlite:///{tmp_path / 'strategy_safety.db'}"
    monkeypatch.setattr(settings, "database_url", db_url)
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    command.upgrade(config, "head")
    engine = make_engine(db_url)
    try:
        yield (engine, registry.StrategyRegistryService(make_session_factory(engine)), config)
    finally:
        engine.dispose()


@pytest.fixture()
def authorized(monkeypatch):
    from app.core.auth import UserContext
    # Verify service-side access control even if a token has been authenticated.
    # JWT signature/expiry/revocation integration uses Sterling's existing
    # app.core.auth._context_from_access_token; no dev header auth is accepted.
    def verified_actor(token):
        if token == "signed-admin":
            return UserContext(user_id="owner", role="admin")
        raise PermissionError("invalid or non-privileged token")
    monkeypatch.setattr(registry, "_identity", verified_actor)


def _count(engine, table):
    with engine.connect() as c:
        return c.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()


def test_auth_refuses_anonymous_and_viewer():
    with pytest.raises(PermissionError):
        registry._identity("")
    from app.core.auth import UserContext
    from app.core import auth
    from unittest.mock import patch
    with patch.object(auth, "_context_from_access_token", return_value=UserContext(user_id="u", role="user")):
        with pytest.raises(PermissionError):
            registry._identity("synthetic-nonprivileged")
    with patch.object(auth, "_context_from_access_token", return_value=UserContext(user_id="o", role="operator")):
        assert registry._identity("synthetic-verified-operator").user_id == "o"
    # This tests policy decisions, not operator provisioning or actual JWT issuance.


def test_atomic_strategy_version_and_audit(store, authorized):
    engine, svc, _ = store
    strategy = svc.create_strategy(access_token="signed-admin", slug="snapback",
                                   display_name="Snapback", reason="initial registration")
    version = svc.publish_version(access_token="signed-admin", strategy_id=strategy,
                                  version="v1", parameters={"b": 2, "a": 1},
                                  reason="offline baseline")
    run = svc.create_offline_run(access_token="signed-admin", version_id=version,
                                 reason="simulation test")
    assert strategy and version and run
    assert _count(engine, "platform_strategy_versions") == 1
    assert _count(engine, "platform_strategy_runs") == 1
    assert _count(engine, "platform_strategy_audit") == 3
    with engine.connect() as c:
        rec = c.execute(text("SELECT parameters_json, content_sha256 FROM platform_strategy_versions")).one()
        from hashlib import sha256
        assert rec[0] == '{"a":1,"b":2}'
        assert rec[1] == sha256(rec[0].encode()).hexdigest()
        assert c.execute(text("SELECT COUNT(*) FROM platform_strategy_audit WHERE actor_id='owner' AND length(reason)>0")).scalar_one() == 3


def test_denies_viewer_and_live(store, authorized):
    _, svc, _ = store
    with pytest.raises(PermissionError):
        svc.create_strategy(access_token="signed-viewer", slug="x", display_name="X", reason="test")
    with pytest.raises(PermissionError):
        svc.create_strategy(access_token="", slug="x", display_name="X", reason="test")
    strategy = svc.create_strategy(access_token="signed-admin", slug="s",
                                   display_name="S", reason="test")
    version = svc.publish_version(access_token="signed-admin", strategy_id=strategy,
                                  version="1", parameters={}, reason="test")
    with pytest.raises(ValidationError):
        svc.create_offline_run(access_token="signed-admin", version_id=version,
                               mode="live", reason="test")


def test_version_rows_and_audit_are_db_immutable(store, authorized):
    engine, svc, _ = store
    key = svc.create_strategy(access_token="signed-admin", slug="s", display_name="S", reason="create")
    version = svc.publish_version(access_token="signed-admin", strategy_id=key,
                                  version="1", parameters={}, reason="publish")
    for query, values in [
        ("UPDATE platform_strategy_versions SET parameters_json='{}' WHERE id=:id", {"id": version}),
        ("DELETE FROM platform_strategy_versions WHERE id=:id", {"id": version}),
        ("UPDATE platform_strategy_audit SET reason='changed'", {}),
        ("DELETE FROM platform_strategy_audit", {}),
        ("DELETE FROM platform_strategies WHERE id=:id", {"id": key}),
    ]:
        with pytest.raises(DBAPIError):
            with engine.begin() as c:
                c.execute(text(query), values)


def test_audit_failure_rolls_back_create_and_version(store, authorized):
    engine, svc, _ = store
    with engine.begin() as c:
        c.execute(text("CREATE TRIGGER reject_test_audit BEFORE INSERT ON platform_strategy_audit "
                       "BEGIN SELECT RAISE(ABORT, 'synthetic audit outage'); END"))
    with pytest.raises(DBAPIError):
        svc.create_strategy(access_token="signed-admin", slug="no-audit",
                            display_name="Blocked", reason="must rollback")
    assert _count(engine, "platform_strategies") == 0
    with engine.begin() as c:
        c.execute(text("DROP TRIGGER reject_test_audit"))
    key = svc.create_strategy(access_token="signed-admin", slug="s",
                              display_name="S", reason="works")
    with engine.begin() as c:
        c.execute(text("CREATE TRIGGER reject_test_audit BEFORE INSERT ON platform_strategy_audit "
                       "BEGIN SELECT RAISE(ABORT, 'synthetic audit outage'); END"))
    with pytest.raises(DBAPIError):
        svc.publish_version(access_token="signed-admin", strategy_id=key,
                            version="1", parameters={}, reason="must rollback")
    assert _count(engine, "platform_strategy_versions") == 0


def test_duplicate_version_and_lock_are_fail_closed(store, authorized):
    engine, svc, _ = store
    key = svc.create_strategy(access_token="signed-admin", slug="s",
                              display_name="S", reason="create")
    svc.publish_version(access_token="signed-admin", strategy_id=key,
                        version="1", parameters={"x": 1}, reason="publish")
    with pytest.raises(IntegrityError):
        svc.publish_version(access_token="signed-admin", strategy_id=key,
                            version="1", parameters={"x": 2}, reason="duplicate")
    assert _count(engine, "platform_strategy_audit") == 2
    svc.lock_strategy(access_token="signed-admin", strategy_id=key, reason="freeze")
    with pytest.raises(PermissionError):
        svc.publish_version(access_token="signed-admin", strategy_id=key,
                            version="2", parameters={}, reason="blocked")
    with pytest.raises(PermissionError):
        svc.update_strategy(access_token="signed-admin", strategy_id=key,
                            display_name="new", reason="blocked")
    assert _count(engine, "platform_strategy_audit") == 3
    with pytest.raises(DBAPIError):
        with engine.begin() as conn:
            conn.execute(text("UPDATE platform_strategies SET is_locked=0 WHERE id=:id"), {"id": key})


def test_metadata_update_and_soft_retirement_are_audited(store, authorized):
    engine, svc, _ = store
    key = svc.create_strategy(access_token="signed-admin", slug="s", display_name="S", reason="create")
    svc.update_strategy(access_token="signed-admin", strategy_id=key,
                        display_name="S v2", reason="rename")
    with engine.begin() as conn:
        # Simulate a future *separately authorized* lifecycle transition;
        # this test does not claim a public lifecycle-state API exists.
        conn.execute(text("UPDATE platform_strategies SET state='disabled' WHERE id=:id"), {"id": key})
    svc.retire_strategy(access_token="signed-admin", strategy_id=key, reason="retirement")
    assert _count(engine, "platform_strategy_audit") == 3
    with engine.connect() as c:
        assert c.execute(text("SELECT state FROM platform_strategies WHERE id=:id"),
                         {"id": key}).scalar_one() == "retired"
    with pytest.raises(DBAPIError):
        with engine.begin() as conn:
            conn.execute(text("UPDATE platform_strategies SET state='draft' WHERE id=:id"), {"id": key})


def test_safety_migration_downgrade_requires_disposable_db(store):
    engine, _, config = store
    engine.dispose()
    command.downgrade(config, "4d2c62ab8b01")
    from sqlalchemy import inspect
    re_engine = make_engine()
    try:
        tables = set(inspect(re_engine).get_table_names())
        assert "platform_strategy_audit" not in tables
        assert "platform_strategy_versions" in tables
    finally:
        re_engine.dispose()
    command.upgrade(config, "head")
