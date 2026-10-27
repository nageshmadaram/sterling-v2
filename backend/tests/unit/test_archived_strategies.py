from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from fastapi import HTTPException
from app.services import archived_strategies as archive, db
from app.core.release_scope import may_originate
from app.api.v1.endpoints import config

@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("STERLING_DB_PATH", str(tmp_path / "archive.db"))
    monkeypatch.setattr(db, "_DB_PATH", str(tmp_path / "archive.db"))
    monkeypatch.setattr(db, "_available", True)
    db.init()

@pytest.mark.parametrize("sid", archive.CATALOG)
def test_default_off_persisted_and_user_isolated(sid):
    assert not archive.enabled("alice", sid)
    archive.set_enabled("alice", sid, True)
    assert archive.enabled("alice", sid)
    assert not archive.enabled("bob", sid)
    assert not may_originate(sid, "alice")
    archive.set_enabled("alice", sid, False)
    assert not archive.enabled("alice", sid)
    assert not may_originate(sid, "alice")

@pytest.mark.parametrize("sid", ["adaptive_edge", "intraday"])
def test_restoring_does_not_restore_old_auto(sid):
    import importlib
    module = importlib.import_module(f"app.services.{sid}")
    db.set_config_strict(f"{sid}_config:alice", '{"enabled":true,"auto_execute":true}')
    archive.set_enabled("alice", sid, True)
    assert module.get_config("alice").enabled
    assert not module.get_config("alice").auto_execute
    archive.set_enabled("alice", sid, False)
    assert not module.get_config("alice").enabled

@pytest.mark.parametrize("raw", ['TRUE', '1', '"true"', '{"enabled":true}'])
def test_malformed_permission_never_grants_entry(raw):
    db.set_config_strict("archived_strategy_enabled:alice:intraday", raw)
    assert not archive.enabled("alice", "intraday")

def test_unavailable_store_denies_entry_and_save(monkeypatch):
    monkeypatch.setattr(db, "_available", False)
    assert not archive.enabled("alice", "intraday")
    with pytest.raises(db.ControlPlaneUnavailableError):
        archive.set_enabled("alice", "intraday", True)

def test_failed_native_write_keeps_permission_revoked(monkeypatch):
    archive.set_enabled("alice", "intraday", True)
    original = db.set_config_strict
    def write(key, value):
        if key.startswith("intraday_config"):
            raise db.ControlPlaneUnavailableError("unavailable")
        original(key, value)
    monkeypatch.setattr(db, "set_config_strict", write)
    with pytest.raises(db.ControlPlaneUnavailableError):
        archive.set_enabled("alice", "intraday", False)
    assert not archive.enabled("alice", "intraday")

def test_unknown_strategy_and_non_boolean_rejected():
    with pytest.raises(ValueError):
        archive.set_enabled("alice", "arbitrary", True)
    with pytest.raises(ValidationError):
        config.ArchivedStrategyUpdate(enabled="true")

@pytest.mark.asyncio
async def test_enabled_scan_uses_authenticated_owner(monkeypatch):
    archive.set_enabled("alice", "intraday", True)
    import app.services.intraday as intraday
    seen = []
    async def scan(uid):
        seen.append(uid)
        return {"rows": []}
    monkeypatch.setattr(intraday, "scan_once", scan)
    assert await config.intraday_scan(SimpleNamespace(user_id="alice")) == {"rows": []}
    assert seen == ["alice"]
    with pytest.raises(HTTPException) as exc:
        await config.intraday_scan(SimpleNamespace(user_id="bob"))
    assert exc.value.status_code == 410

@pytest.mark.asyncio
async def test_storage_failure_route_is_503(monkeypatch):
    monkeypatch.setattr(db, "_available", False)
    with pytest.raises(HTTPException) as exc:
        await config.archived_strategies(SimpleNamespace(user_id="alice"))
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_enabled_archive_still_cannot_originate_live_risk(monkeypatch):
    from app.services.execution_service import CanonicalExecutionService, ExecutionRequest
    monkeypatch.setenv("ENVIRONMENT", "production")
    archive.set_enabled("alice", "intraday", True)
    result = await CanonicalExecutionService().submit_order(ExecutionRequest(
        uid="alice", account_id="acct", strategy_id="intraday", generation_id="g",
        signal_id="s", exchange="NFO", symbol="OPTION", side="BUY", quantity=1))
    assert not result.success
    assert result.status == "REJECTED_OUTSIDE_RELEASE_SCOPE"
    assert "observation/management only" in result.error

@pytest.mark.asyncio
async def test_settings_require_owner_identity():
    with pytest.raises(HTTPException) as exc:
        await config.archived_strategies(SimpleNamespace())
    assert exc.value.status_code == 401
