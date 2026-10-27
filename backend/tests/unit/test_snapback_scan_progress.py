"""Live Snapback scan visibility must never grant permission to trade."""
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest

from app.services import snapback
from app.services import snapback_dispatcher as dispatcher


def test_partial_scan_rows_are_visible_but_not_executable():
    original = [
        {"signal_id": "s1", "state": "armed", "reason": None,
         "execution_eligible": True, "symbol": "NIFTY"},
        {"signal_id": "s2", "state": "watching", "reason": "no quote",
         "execution_eligible": False, "symbol": "RELIANCE"},
    ]
    preview = snapback._scan_preview(original)
    assert len(preview) == 2
    assert preview[0]["state"] == "watching"
    assert preview[0]["execution_eligible"] is False
    assert preview[0]["scan_in_progress"] is True
    assert "Scan in progress" in preview[0]["reason"]
    assert preview[1]["reason"] == "no quote"
    assert preview[0]["signal_id"] == original[0]["signal_id"]
    assert original[0]["state"] == "armed"
    assert original[0]["execution_eligible"] is True


def test_progress_rows_keep_original_data_immutable():
    first = {"signal_id": "x", "state": "armed", "quote": {"ask": 100}}
    items = snapback._scan_preview([first])
    items[0]["state"] = "error"
    assert first["state"] == "armed"


@pytest.mark.asyncio
async def test_auto_off_cannot_suppress_advisory_signal_refresh(monkeypatch):
    cfg = NS(enabled=True, auto_execute=False, trading_mode="scalp")
    monkeypatch.setattr(snapback, "get_effective_config", lambda uid: cfg)
    monkeypatch.setattr(dispatcher, "auto_execute", lambda uid, config: False)

    engine = dispatcher.SnapbackDispatcher(
        positions=NS(open_positions=Mock(return_value=[])),
        exit_manager=NS(tick=AsyncMock(return_value={"managed": 0})),
    )
    engine._warn_calendar_runway = Mock()
    engine._client = AsyncMock(return_value=NS(_account_id="ACCT"))
    engine._recover = AsyncMock()
    engine._refresh_bound_reconciliation = AsyncMock()
    engine._rescan_if_stale = AsyncMock()
    engine._record = Mock(return_value={"state": "auto_off"})

    assert await engine._cycle_locked("test-owner") == {"state": "auto_off"}
    engine._rescan_if_stale.assert_awaited_once_with("test-owner", cfg)
    engine.exit_manager.tick.assert_awaited_once()


@pytest.mark.asyncio
async def test_disabled_strategy_does_not_refresh_or_originate(monkeypatch):
    cfg = NS(enabled=False, auto_execute=False, trading_mode="scalp")
    monkeypatch.setattr(snapback, "get_effective_config", lambda uid: cfg)
    engine = dispatcher.SnapbackDispatcher(
        positions=NS(open_positions=Mock(return_value=[])),
        exit_manager=NS(tick=AsyncMock(return_value={"managed": 0})),
    )
    engine._warn_calendar_runway = Mock()
    engine._client = AsyncMock(return_value=NS(_account_id="ACCT"))
    engine._recover = AsyncMock()
    engine._rescan_if_stale = AsyncMock()
    engine._record = Mock(return_value={"state": "engine_disabled"})
    assert await engine._cycle_locked("test-owner") == {"state": "engine_disabled"}
    engine._rescan_if_stale.assert_not_awaited()
