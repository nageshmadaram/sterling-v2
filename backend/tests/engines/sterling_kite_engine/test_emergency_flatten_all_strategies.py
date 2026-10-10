"""Square-Off All and Kill Switch & Halt reach Snapback and Gamma positions too."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import gamma_move_positions, gamma_move_runner
from app.services import snapback_live_exits, snapback_live_positions
from app.services import live_safety
from app.services.kite_engine import positions, service


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(service.positions, "open_positions", lambda _uid: [])
    monkeypatch.setattr(service.state, "log", lambda *a, **k: None)
    monkeypatch.setattr(snapback_live_positions, "open_positions", lambda _uid: [])
    monkeypatch.setattr(gamma_move_positions, "open_positions", lambda _uid: [])
    monkeypatch.setattr(service.state, "get_config",
                        lambda _uid: SimpleNamespace(auto_execute=False))
    monkeypatch.setattr(live_safety, "set_kill_switch", lambda *a, **k: None)


def _flat(closed=0, failed=(), skipped=()):
    return {"closed": closed, "failed": list(failed), "skipped": list(skipped)}


@pytest.mark.asyncio
async def test_square_off_all_flattens_snapback_and_gamma_through_their_own_exits(monkeypatch):
    snap = AsyncMock(return_value=_flat(closed=2))
    gamma = AsyncMock(return_value=_flat(closed=1))
    monkeypatch.setattr(snapback_live_exits, "flatten_all", snap, raising=False)
    monkeypatch.setattr(gamma_move_runner, "flatten_all", gamma, raising=False)
    client = object()

    result = await service.emergency_square_off_all(client, "u1")

    snap.assert_awaited_once_with("u1", client, reason="emergency_square_off")
    gamma.assert_awaited_once_with("u1", client, reason="emergency_square_off")
    assert result["squared_off"] == 3
    assert result["failed"] == 0
    assert result["status"] == "ok"
    assert {d["strategy"] for d in result["details"]} == {"snapback", "gamma"}


@pytest.mark.asyncio
async def test_a_failed_leg_or_a_raising_strategy_is_reported_and_does_not_stop_the_rest(monkeypatch):
    monkeypatch.setattr(snapback_live_exits, "flatten_all",
                        AsyncMock(side_effect=RuntimeError("dispatcher down")), raising=False)
    gamma = AsyncMock(return_value=_flat(closed=1, failed=["NIFTY25000CE"],
                                         skipped=["BANKNIFTY25000CE: already closed"]))
    monkeypatch.setattr(gamma_move_runner, "flatten_all", gamma, raising=False)

    result = await service.emergency_square_off_all(object(), "u1")

    gamma.assert_awaited_once()
    assert result["status"] == "EXIT_RECONCILIATION_REQUIRED"
    assert result["squared_off"] == 1
    # one leg failed in Gamma, and Snapback itself raised: both are operator work
    assert result["failed"] == 2
    assert "Snapback" in result["message"] and "dispatcher down" in result["message"]
    assert "NIFTY25000CE" in result["message"]


@pytest.mark.asyncio
async def test_a_strategy_without_flatten_all_is_named_when_it_holds_positions(monkeypatch):
    monkeypatch.delattr(snapback_live_exits, "flatten_all", raising=False)
    monkeypatch.delattr(gamma_move_runner, "flatten_all", raising=False)
    monkeypatch.setattr(gamma_move_positions, "open_positions",
                        lambda _uid: [SimpleNamespace(symbol="NIFTY25000CE")])

    result = await service.emergency_square_off_all(object(), "u1")

    assert result["status"] == "EXIT_RECONCILIATION_REQUIRED"
    assert result["failed"] == 1
    assert "Gamma" in result["message"] and "NIFTY25000CE" in result["message"]
    assert "close it from the Gamma panel" in result["message"]


@pytest.mark.asyncio
async def test_a_strategy_without_flatten_all_and_without_positions_is_silent(monkeypatch):
    monkeypatch.delattr(snapback_live_exits, "flatten_all", raising=False)
    monkeypatch.delattr(gamma_move_runner, "flatten_all", raising=False)

    result = await service.emergency_square_off_all(object(), "u1")

    assert result["status"] == "ok"
    assert result["failed"] == 0 and result["positions_count"] == 0


@pytest.mark.asyncio
async def test_emergency_halt_engages_the_switch_then_flattens_every_strategy(monkeypatch):
    engaged = []
    monkeypatch.setattr(live_safety, "set_kill_switch",
                        lambda enabled, **k: engaged.append(enabled))
    snap = AsyncMock(return_value=_flat(closed=1))
    gamma = AsyncMock(return_value=_flat(closed=1, failed=["G1"]))
    monkeypatch.setattr(snapback_live_exits, "flatten_all", snap, raising=False)
    monkeypatch.setattr(gamma_move_runner, "flatten_all", gamma, raising=False)

    result = await service.emergency_halt(object(), "u1")

    assert engaged == [True]
    snap.assert_awaited_once()
    assert snap.await_args.kwargs["reason"] == "emergency_halt"
    assert gamma.await_args.kwargs["reason"] == "emergency_halt"
    assert result["status"] == "EXIT_RECONCILIATION_REQUIRED"
    assert result["square_off"]["squared_off"] == 2
    assert result["square_off"]["failed"] == 1


# ── a skipped position is NOT a flattened one ────────────────────────────────

@pytest.mark.asyncio
async def test_a_skipped_position_is_reported_as_unresolved_not_as_flat(monkeypatch):
    snap = AsyncMock(return_value=_flat(skipped=["NIFTY25000CE (p1): market_closed"]))
    gamma = AsyncMock(return_value=_flat())
    monkeypatch.setattr(snapback_live_exits, "flatten_all", snap, raising=False)
    monkeypatch.setattr(gamma_move_runner, "flatten_all", gamma, raising=False)

    result = await service.emergency_square_off_all(object(), "u1")

    assert result["status"] == "EXIT_RECONCILIATION_REQUIRED", "an open position must not read 'ok'"
    assert result["squared_off"] == 0
    assert result["failed"] == 1 and result["skipped_unresolved"] == 1
    assert result["positions_count"] == 1
    assert "market_closed" in result["message"]
    detail = next(d for d in result["details"] if d["strategy"] == "snapback")
    assert detail["status"] == "failed_or_unknown"


@pytest.mark.asyncio
async def test_a_position_whose_exit_is_already_in_progress_is_awaiting_confirmation(monkeypatch):
    snap = AsyncMock(return_value=_flat())
    gamma = AsyncMock(return_value=_flat(skipped=["NIFTY25000CE: an exit is already in progress"]))
    monkeypatch.setattr(snapback_live_exits, "flatten_all", snap, raising=False)
    monkeypatch.setattr(gamma_move_runner, "flatten_all", gamma, raising=False)

    result = await service.emergency_square_off_all(object(), "u1")

    assert result["status"] == "PENDING_BROKER_CONFIRMATION"
    assert result["submitted_awaiting_fill"] == 1 and result["failed"] == 0
    assert result["positions_count"] == 1


@pytest.mark.asyncio
async def test_an_already_closed_skip_is_not_counted_at_all(monkeypatch):
    snap = AsyncMock(return_value=_flat())
    gamma = AsyncMock(return_value=_flat(skipped=["NIFTY25000CE: already closed"]))
    monkeypatch.setattr(snapback_live_exits, "flatten_all", snap, raising=False)
    monkeypatch.setattr(gamma_move_runner, "flatten_all", gamma, raising=False)

    result = await service.emergency_square_off_all(object(), "u1")

    assert result["status"] == "ok"
    assert result["failed"] == 0 and result["positions_count"] == 0


@pytest.mark.asyncio
async def test_emergency_halt_does_not_report_ok_over_a_skipped_position(monkeypatch):
    snap = AsyncMock(return_value=_flat(skipped=["NIFTY25000CE (p1): other_account"]))
    gamma = AsyncMock(return_value=_flat())
    monkeypatch.setattr(snapback_live_exits, "flatten_all", snap, raising=False)
    monkeypatch.setattr(gamma_move_runner, "flatten_all", gamma, raising=False)

    result = await service.emergency_halt(object(), "u1")

    assert result["status"] == "EXIT_RECONCILIATION_REQUIRED"
    assert result["square_off"]["failed"] == 1
