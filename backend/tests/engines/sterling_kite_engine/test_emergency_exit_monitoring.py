"""Emergency exits must retain monitoring until broker-flat/fill confirmation."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.kite_engine import positions, service
from app.services.exchanges.kite import ticker_manager


def _held():
    return SimpleNamespace(
        symbol="NIFTY26OCT25000CE",
        exchange="NFO",
        status=positions.OPEN,
        stop_premium=80.0,
        fill_price=100.0,
        token=777,
        guard_key="guard-777",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected_status", "expected_pending", "expected_failed"),
    [
        (True, "PENDING_BROKER_CONFIRMATION", 1, 0),
        (False, "EXIT_RECONCILIATION_REQUIRED", 0, 1),
    ],
)
async def test_emergency_exit_keeps_monitor_and_guard_until_fill(
    monkeypatch, outcome, expected_status, expected_pending, expected_failed,
):
    p = _held()
    client = SimpleNamespace(
        get_ltp=AsyncMock(return_value={
            "NFO:NIFTY26OCT25000CE": {"last_price": 95.0},
        })
    )
    send_exit = AsyncMock(return_value=outcome)
    unsubscribe = AsyncMock()
    guard_clears = []

    monkeypatch.setattr(service.positions, "open_positions", lambda _uid: [p])
    monkeypatch.setattr(service.positions, "get", lambda _uid, _symbol: p)
    monkeypatch.setattr(service.monitor, "_exit_position", send_exit)
    monkeypatch.setattr(service.state, "clear_auto_open", lambda *args: guard_clears.append(args))
    monkeypatch.setattr(service.state, "log", lambda *args, **kwargs: None)
    monkeypatch.setattr(ticker_manager, "unsubscribe", unsubscribe)

    result = await service.emergency_square_off_all(client, "user-1")

    assert result["status"] == expected_status
    assert result["squared_off"] == 0, "broker ACK is not fill evidence"
    assert result["submitted_awaiting_fill"] == expected_pending
    assert result["failed"] == expected_failed
    assert result["positions_count"] == 1
    assert p.status == positions.OPEN
    assert not guard_clears, "an open position must retain its origination guard"
    unsubscribe.assert_not_awaited()
    send_exit.assert_awaited_once()


@pytest.mark.asyncio
async def test_emergency_exit_can_count_confirmed_close_without_clearing_monitor_twice(monkeypatch):
    p = _held()
    client = SimpleNamespace(get_ltp=AsyncMock(return_value={}))
    unsubscribe = AsyncMock()
    monkeypatch.setattr(service.positions, "open_positions", lambda _uid: [p])
    monkeypatch.setattr(
        service.positions,
        "get",
        lambda _uid, _symbol: SimpleNamespace(status=positions.CLOSED),
    )
    monkeypatch.setattr(service.monitor, "_exit_position", AsyncMock(return_value=False))
    monkeypatch.setattr(service.state, "log", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        service.state,
        "clear_auto_open",
        lambda *args: (_ for _ in ()).throw(AssertionError("duplicate guard release")),
    )
    monkeypatch.setattr(ticker_manager, "unsubscribe", unsubscribe)

    result = await service.emergency_square_off_all(client, "user-1")

    assert result["status"] == "ok"
    assert result["squared_off"] == 1
    assert result["submitted_awaiting_fill"] == 0
    assert result["failed"] == 0
    unsubscribe.assert_not_awaited()


@pytest.mark.asyncio
async def test_emergency_halt_reports_pending_exits_instead_of_claiming_flat(monkeypatch):
    from app.services import live_safety

    monkeypatch.setattr(live_safety, "set_kill_switch", lambda *a, **kw: None)
    monkeypatch.setattr(live_safety, "kill_switch_state", lambda: {"enabled": True})
    monkeypatch.setattr(
        service.state,
        "get_config",
        lambda _uid: SimpleNamespace(auto_execute=False),
    )
    monkeypatch.setattr(service.state, "log", lambda *a, **kw: None)
    monkeypatch.setattr(
        service,
        "emergency_square_off_all",
        AsyncMock(return_value={
            "status": "PENDING_BROKER_CONFIRMATION",
            "squared_off": 0,
            "submitted_awaiting_fill": 1,
            "failed": 0,
        }),
    )

    result = await service.emergency_halt(object(), "user-1")

    assert result["status"] == "EXIT_RECONCILIATION_REQUIRED"
    assert result["kill_switch"]["enabled"] is True
    assert "awaiting fill" in result["message"]
    assert "All positions squared off" not in result["message"]


def test_emergency_action_schema_exposes_pending_fill_count():
    from app.engines.sterling_kite_engine.schemas import EmergencyActionResponse

    response = EmergencyActionResponse(
        status="PENDING_BROKER_CONFIRMATION",
        message="one exit acknowledged, awaiting fill",
        positions_count=1,
        squared_off=0,
        submitted_awaiting_fill=1,
        failed=0,
        details=[{"symbol": "NIFTY26OCT25000CE", "status": "submitted_awaiting_fill"}],
    )
    payload = response.model_dump()

    assert payload["squared_off"] == 0
    assert payload["submitted_awaiting_fill"] == 1
    assert payload["status"] != "ok"
