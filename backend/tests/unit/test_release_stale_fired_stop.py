"""Legacy exit paths (Intraday, Adaptive Edge) free a fired stop whose sell order rests."""
import pytest

from app.services.kite_engine import protective_stop as pstop


class _State:
    def __init__(self, status):
        self.status = status


def _patch(monkeypatch, status, outcome=None, filled=0):
    async def stop_state(*a, **k):
        return _State(status)

    async def cancel(client, state, *, tradingsymbol):
        return pstop.ChildCancel(outcome, order_id="C1", filled_quantity=filled)

    monkeypatch.setattr(pstop, "stop_state", stop_state)
    monkeypatch.setattr(pstop, "cancel_fired_child", cancel)


async def _call():
    return await pstop.release_stale_fired_stop(
        object(), 7, tradingsymbol="X", exchange="NFO", product="MIS", qty=50)


@pytest.mark.asyncio
async def test_an_absent_stop_is_safe(monkeypatch):
    _patch(monkeypatch, pstop.STOP_ABSENT)
    assert (await _call())[0] is True


@pytest.mark.asyncio
async def test_an_unfilled_stale_child_is_cancelled_and_the_exit_may_sell(monkeypatch):
    _patch(monkeypatch, pstop.STOP_TRIGGERED_STALE, pstop.CANCEL_CLEARED, 0)
    assert (await _call())[0] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome,filled", [
    (pstop.CANCEL_CLEARED, 20),          # part-filled: these paths do not book it, so hold
    (pstop.CANCEL_COMPLETED, 50),        # filled first: already exited
    (pstop.CANCEL_UNCONFIRMED, 0),       # could not confirm: never sell over a working order
])
async def test_anything_else_holds_the_exit(monkeypatch, outcome, filled):
    _patch(monkeypatch, pstop.STOP_TRIGGERED_STALE, outcome, filled)
    safe, why = await _call()
    assert safe is False and why


@pytest.mark.asyncio
async def test_a_live_or_resting_stop_is_not_freed(monkeypatch):
    _patch(monkeypatch, pstop.STOP_ACTIVE if hasattr(pstop, "STOP_ACTIVE") else "active")
    assert (await _call())[0] is False
