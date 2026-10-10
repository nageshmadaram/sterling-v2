"""Legacy exit paths (Intraday, Adaptive Edge) free a fired stop whose sell order rests."""
import pytest

from app.services.kite_engine import protective_stop as pstop


def _patch(monkeypatch, verdict, outcome=None, filled=0):
    async def stop_state(*a, **k):
        # A real StopState, so the test cannot drift from the dataclass's field names.
        return pstop.StopState(verdict)

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


class _Client:
    """Broker whose stale child cancels cleanly: the order history ends CANCELLED, nothing filled."""

    def __init__(self):
        self.cancelled = []

    async def cancel_order(self, order_id):
        self.cancelled.append(order_id)

    async def get_order_history(self, order_id):
        return [{"order_id": order_id, "status": "CANCELLED", "filled_quantity": 0}]


@pytest.mark.asyncio
async def test_a_real_stale_stop_state_has_its_resting_child_cancelled_and_frees_the_exit(monkeypatch):
    async def stop_state(*a, **k):
        return pstop.StopState(pstop.STOP_TRIGGERED_STALE, pstop.CHILD_RESTING,
                               child_order_id="C1", child_age_s=120.0)

    monkeypatch.setattr(pstop, "stop_state", stop_state)
    client = _Client()
    safe, note = await pstop.release_stale_fired_stop(
        client, 7, tradingsymbol="X", exchange="NFO", product="MIS", qty=50)
    assert safe is True and note
    assert client.cancelled == ["C1"]
