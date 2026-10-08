"""A failing scan must not stop the maintenance of positions Sterling already owns."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.kite_engine import service, state


class _Client:
    _is_paper = False
    _account_id = "ACC1"

    async def search_instruments(self, *_a, **_k):
        raise RuntimeError("instruments CSV 503")


@pytest.fixture
def steps(monkeypatch):
    calls = {}
    for name in ("_reconcile_pending_positions", "_reconcile_closed_positions",
                 "_reconcile_orphan_stops", "_square_off_expiring",
                 "_time_stop_positions", "_update_open_position_trails"):
        mock = AsyncMock()
        calls[name] = mock
        monkeypatch.setattr(service, name, mock)
    return calls


def _enable(uid):
    cfg = state.get_config(uid)
    state.set_config(uid, cfg.model_copy(update={"engine_enabled": True}))


@pytest.mark.asyncio
async def test_expiry_square_off_and_time_stop_run_when_the_scan_raises(steps):
    _enable("m1")

    with pytest.raises(RuntimeError, match="503"):
        await service.scan_user(_Client(), "m1")

    for name in ("_reconcile_orphan_stops", "_square_off_expiring", "_time_stop_positions"):
        steps[name].assert_awaited_once()
    # Reconciliation ran before the failure and is not repeated; the trail needs a
    # fresh snapshot, which a failed scan does not have.
    steps["_reconcile_pending_positions"].assert_awaited_once()
    steps["_reconcile_closed_positions"].assert_awaited_once()
    steps["_update_open_position_trails"].assert_not_awaited()
    assert state.status("m1").scanning is False


@pytest.mark.asyncio
async def test_one_failing_maintenance_step_does_not_skip_the_others(steps):
    _enable("m2")
    steps["_square_off_expiring"].side_effect = RuntimeError("quote read failed")

    with pytest.raises(RuntimeError, match="503"):
        await service.scan_user(_Client(), "m2")

    steps["_time_stop_positions"].assert_awaited_once()


@pytest.mark.asyncio
async def test_a_failure_in_the_first_reconcile_still_reaches_the_expiry_square_off(steps):
    _enable("m3")
    steps["_reconcile_pending_positions"].side_effect = RuntimeError("order book 502")

    with pytest.raises(RuntimeError, match="order book 502"):
        await service.scan_user(_Client(), "m3")

    steps["_square_off_expiring"].assert_awaited_once()
    steps["_time_stop_positions"].assert_awaited_once()
    # The reconcile that failed is retried once by the fallback.
    assert steps["_reconcile_pending_positions"].await_count == 2


@pytest.mark.asyncio
async def test_a_trail_failure_after_a_good_scan_does_not_skip_the_expiry_square_off(steps, monkeypatch):
    _enable("m4")

    class _Snap:
        rows: list = []
        diag = type("D", (), {k: 0 for k in (
            "index_fired", "index_evaluated", "indices", "deriv_fired", "deriv_charts",
            "deriv_resolved", "deriv_min_bars", "deriv_max_bars", "deriv_no_data",
            "deriv_no_spot", "confluence_fired", "premium_missing", "premium_ok")})()

    class _GoodClient(_Client):
        async def search_instruments(self, *_a, **_k):
            return []

    monkeypatch.setattr(service.scanner, "scan", AsyncMock())
    monkeypatch.setattr(service.scanner, "snapshot", lambda _uid: _Snap())
    steps["_update_open_position_trails"].side_effect = RuntimeError("trail read failed")

    with pytest.raises(RuntimeError, match="trail read failed"):
        await service.scan_user(_GoodClient(), "m4")

    steps["_square_off_expiring"].assert_awaited_once()
    steps["_time_stop_positions"].assert_awaited_once()
    steps["_reconcile_orphan_stops"].assert_awaited_once()
