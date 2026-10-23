"""Board scan CPU work must leave the order/protection event loop responsive."""
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.engines.snapback.config import SnapbackConfig
from app.services import snapback


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,filtered", [("swing", False), ("swing", True), ("scalp", False)])
async def test_scan_calculations_run_off_the_event_loop(monkeypatch, mode, filtered):
    from app.services.exchanges.kite import accounts
    from app.engines.snapback import regime

    loop_thread = threading.get_ident()
    cfg = SnapbackConfig(enabled=True, trading_mode=mode, market_filter="above_ema" if filtered else "off")
    item = SimpleNamespace(name="NIFTY", token=1, tradingsymbol="NIFTY", option_exchange="NFO")
    client = SimpleNamespace(search_instruments=AsyncMock(return_value=[]))
    monkeypatch.setattr(snapback, "get_effective_config", lambda uid: cfg)
    monkeypatch.setattr(accounts, "get_active", lambda uid: object())
    monkeypatch.setattr(accounts, "acquire_client", AsyncMock(return_value=client))
    monkeypatch.setattr(snapback, "resolve_universe", lambda *a, **k: [item])
    tape = [{"time": 1}]
    monkeypatch.setattr(snapback, "_candles", AsyncMock(return_value=tape))
    monkeypatch.setattr(snapback, "_intraday_candles", AsyncMock(return_value=tape))
    calls = []

    def closed(raw):
        assert threading.get_ident() != loop_thread, "daily filtering blocked the event loop"
        calls.append("closed")
        return raw

    def bars(raw):
        assert threading.get_ident() != loop_thread, "market conversion blocked the event loop"
        calls.append("bars")
        return raw

    def gate(*args, **kwargs):
        assert threading.get_ident() != loop_thread, "market features blocked the event loop"
        calls.append("gate")
        return {"ok": True}

    def evaluate(raw, config, symbol, *, market_gate):
        assert threading.get_ident() != loop_thread, "signal features blocked the event loop"
        assert raw is tape and config == cfg and symbol == "NIFTY"
        assert market_gate == ({"ok": True} if filtered else None)
        calls.append("evaluate")
        return []

    monkeypatch.setattr(snapback, "_drop_forming", closed)
    monkeypatch.setattr(snapback, "to_bars", bars)
    monkeypatch.setattr(regime, "gate_for", gate)
    monkeypatch.setattr(snapback, "evaluate_symbol", evaluate)
    monkeypatch.setattr(snapback, "snapshot", lambda uid: {"scanned": snapback.status(uid).scanned, "failures": snapback.status(uid).failures, "last_error": snapback.status(uid).last_error})
    uid = f"offload-{mode}-{filtered}"
    monkeypatch.setitem(snapback._state, uid, snapback.ScanState())
    result = await snapback.scan_once(uid)
    assert result == {"scanned": 1, "failures": [], "last_error": None}
    assert calls == (["closed", "bars", "gate", "closed", "evaluate"] if filtered else ["closed", "evaluate"] if mode == "swing" else ["evaluate"])
