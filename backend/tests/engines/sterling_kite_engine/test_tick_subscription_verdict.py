"""An unsuccessful tick subscription is not active risk protection."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.exchanges.kite import ticker_manager
from app.services.kite_engine import execution_lifecycle, positions, protection


@pytest.mark.asyncio
async def test_refused_tick_subscription_cannot_report_position_protected(monkeypatch):
    logs = []

    async def refused(*_args, **_kwargs):
        return {"ok": False, "message": "no connected live ticker"}

    monkeypatch.setattr(ticker_manager, "subscribe", refused)
    monkeypatch.setattr(protection.positions, "get", lambda *_args: None)
    monkeypatch.setattr(protection.positions, "register", lambda p: p)
    monkeypatch.setattr(protection.state, "log", lambda *args: logs.append(args))

    result = await protection.arm_position(
        SimpleNamespace(_is_paper=True), "user", symbol="NIFTY26OCT25000CE",
        exchange="NFO", token=777, qty=75, lot_size=75, entry_premium=100,
        stop_premium=80, order_id="PAPER-1", stop_mode="monitor",
    )

    assert result.subscribed is False
    assert result.protected is False
    assert result.gtt_id == 0
    assert "armed nowhere" in result.describe()


@pytest.mark.asyncio
async def test_canonical_lifecycle_logs_refused_tick_subscription(monkeypatch):
    logs = []

    async def refused(*_args, **_kwargs):
        return {"ok": False, "message": "no connected live ticker"}

    monkeypatch.setattr(ticker_manager, "subscribe", refused)
    monkeypatch.setattr(
        execution_lifecycle.state, "log",
        lambda *args, **kwargs: logs.append(args),
    )
    p = SimpleNamespace(
        qty=75, status=positions.OPEN, token=777, uid="user",
        symbol="NIFTY26OCT25000CE", stop_mode="monitor", stop_premium=80,
    )

    await execution_lifecycle._protect(SimpleNamespace(), p)

    assert any(
        entry[1] == "order_failed" and "tick subscription unavailable" in entry[2]
        for entry in logs
    )
