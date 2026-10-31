from __future__ import annotations

import pytest

from app.services import db, live_safety
from app.services import canonical_fill_reconciliation as fills
from app.services.kite_engine import order_journal


@pytest.fixture(autouse=True)
def _journal():
    prior = db._available
    db.init()
    # Uncertain synthetic real-mode orders now set the durable global
    # entry halt. Isolate each test so state never bleeds into later suites.
    live_safety.reset_all_for_tests()
    for uid in ("fill-paper", "fill-partial", "fill-unknown", "fill-mismatch"):
        order_journal.clear_for_tests(uid)
    yield
    for uid in ("fill-paper", "fill-partial", "fill-unknown"):
        order_journal.clear_for_tests(uid)
    live_safety.reset_all_for_tests()
    db._available = prior


def _intent(uid: str, account: str, order_id: str, *, quantity: int = 100):
    intent = order_journal.reserve(
        uid=uid,
        account_id=account,
        strategy_id="intraday",
        generation_id="v1",
        signal_id="sig",
        exchange="NFO",
        symbol="OPT",
        side="BUY",
        quantity=quantity,
        capital_required=10_000,
        available_capital=100_000,
        payload={"product": "MIS"},
    )
    assert order_journal.claim_submission(intent.intent_key)
    return order_journal.transition(
        intent.intent_key,
        "SUBMITTED",
        order_id=order_id,
    )


@pytest.mark.asyncio
async def test_paper_fill_becomes_terminal_and_projection_is_acknowledged():
    _intent("fill-paper", "paper-account", "PAPER-1")

    class Client:
        _is_paper = True
        _account_id = "paper-account"

    resolution = await fills.resolve_entry_fill(
        uid="fill-paper",
        client=Client(),
        order_id="PAPER-1",
        expected_quantity=100,
        reference_price=123.5,
    )

    assert resolution.status == "COMPLETE"
    assert resolution.filled_quantity == 100
    assert resolution.average_price == pytest.approx(123.5)
    assert resolution.terminal is True
    assert resolution.settled is True
    assert order_journal.unresolved("fill-paper") == []
    assert order_journal.pending_projection("fill-paper", "paper-account")

    assert fills.mark_projected(resolution)
    assert order_journal.pending_projection("fill-paper", "paper-account") == []


@pytest.mark.asyncio
async def test_partial_live_fill_cancels_remainder_and_returns_confirmed_quantity():
    _intent("fill-partial", "live-account", "LIVE-1")

    class Client:
        _is_paper = False
        _account_id = "live-account"

        def __init__(self):
            self.cancelled = False
            self.cancel_calls = 0

        async def get_order_history(self, order_id):
            assert order_id == "LIVE-1"
            return [{
                "status": "CANCELLED" if self.cancelled else "OPEN",
                "filled_quantity": 40,
                "average_price": 101.25,
            }]

        async def get_order_trades(self, order_id):
            return []

        async def cancel_order(self, order_id):
            self.cancel_calls += 1
            self.cancelled = True
            return {"order_id": order_id}

    client = Client()
    resolution = await fills.resolve_entry_fill(
        uid="fill-partial",
        client=client,
        order_id="LIVE-1",
        expected_quantity=100,
        reference_price=101.0,
        timeout_s=0,
    )

    assert client.cancel_calls == 1
    assert resolution.status == "CANCELLED"
    assert resolution.filled_quantity == 40
    assert resolution.average_price == pytest.approx(101.25)
    assert resolution.terminal is True
    assert resolution.settled is True
    assert resolution.reconciliation_required is False


@pytest.mark.asyncio
async def test_ambiguous_live_remainder_is_not_resubmitted_or_invented_as_fill():
    _intent("fill-unknown", "live-account", "LIVE-2")

    class Client:
        _is_paper = False
        _account_id = "live-account"

        def __init__(self):
            self.cancel_calls = 0

        async def get_order_history(self, order_id):
            return []

        async def get_order_trades(self, order_id):
            return []

        async def cancel_order(self, order_id):
            self.cancel_calls += 1
            raise TimeoutError("cancel outcome unknown")

    client = Client()
    resolution = await fills.resolve_entry_fill(
        uid="fill-unknown",
        client=client,
        order_id="LIVE-2",
        expected_quantity=100,
        reference_price=100.0,
        timeout_s=0,
    )

    assert client.cancel_calls == 1
    assert resolution.filled_quantity == 0
    assert resolution.settled is False
    assert resolution.status in {"SUBMITTED", "UNKNOWN"}
    assert "unresolved" in resolution.reason
    pending = order_journal.unresolved("fill-unknown", "live-account")
    assert len(pending) == 1
    assert pending[0].order_id == "LIVE-2"


@pytest.mark.asyncio
async def test_complete_with_partial_quantity_is_not_treated_as_settled_reduction():
    class Client:
        _is_paper = False
        _account_id = "live-account"

        async def get_order_history(self, order_id):
            return [{
                "status": "COMPLETE",
                "filled_quantity": 40,
                "average_price": 99.0,
            }]

        async def get_order_trades(self, order_id):
            return []

    resolution = await fills.resolve_acknowledged_order(
        client=Client(),
        order_id="EXIT-MISMATCH",
        expected_quantity=100,
        reference_price=100.0,
        timeout_s=0,
        cancel_unfilled=False,
    )

    assert resolution.status == "COMPLETE"
    assert resolution.filled_quantity == 40
    assert resolution.terminal is True
    assert resolution.settled is False
    assert resolution.reason == "complete_quantity_mismatch"
