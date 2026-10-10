from types import SimpleNamespace

import pytest

from app.services import opening_volume_execution, opening_volume_leaders
from app.services import opening_volume_runner as runner
from app.services.kite_engine import state as engine_state


@pytest.mark.asyncio
async def test_runner_scans_for_advisory_alerts_but_never_executes_with_auto_off(
    monkeypatch,
):
    monkeypatch.setattr(
        opening_volume_execution,
        "get_config",
        lambda _uid: opening_volume_execution.OpeningExecutionConfig(enabled=True),
    )
    monkeypatch.setattr(
        engine_state,
        "get_config",
        lambda _uid: SimpleNamespace(auto_execute=False),
    )
    monkeypatch.setattr(runner, "_market_open", lambda: True)

    scan = {"leaders": [{"signal_key": "opening-volume:test"}]}
    calls = []

    async def scan_once(uid):
        calls.append(("scan", uid))
        return scan

    async def forbidden_execute(*_args, **_kwargs):
        raise AssertionError("manual mode must never submit an order")

    monkeypatch.setattr(opening_volume_leaders, "scan_kite_leaders", scan_once)
    monkeypatch.setattr(
        opening_volume_execution,
        "execute_opening_scan",
        forbidden_execute,
    )

    out = await runner.run_user("tenant-a")

    assert calls == [("scan", "tenant-a")]
    assert out["status"] == "manual"
    assert out["scan"] == scan
    assert out["execution"] == {"status": "manual", "executed": []}


@pytest.fixture(autouse=True)
def archive_opt_in(monkeypatch):
    # These tests exercise enabled-engine behavior; archive denial has its own tests.
    monkeypatch.setattr("app.services.archived_strategies.enabled", lambda uid, sid: True)
