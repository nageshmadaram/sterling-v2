"""Real offline journal admission tests for all migrated legacy SELL doors."""
from __future__ import annotations

import pytest

from app.services import db, live_safety
from app.services.canonical_strategy_reduction import submit_reduction
from app.services.kite_engine import order_journal


class PaperBroker:
    _is_paper = True
    _account_id = "acct-1"

    def __init__(self, error=None):
        self.calls = []
        self.error = error

    async def place_order(self, symbol, side, size, **kwargs):
        self.calls.append((symbol, side, size, kwargs))
        if self.error:
            raise self.error
        return {"order_id": "OFFLINE-ORDER-1"}


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_DB_PATH", str(tmp_path / "reduction.sqlite"))
    assert db.init()
    live_safety.reset_all_for_tests()
    yield
    live_safety.reset_all_for_tests()


def args(client=None, **overrides):
    req = {
        "client": client if client is not None else PaperBroker(),
        "uid": "synthetic-user", "strategy_id": "synthetic-legacy",
        "generation_id": "gen-1", "signal_id": "stable-entry-1",
        "exchange": "NFO", "symbol": "SYNTHETIC26NOV25000CE",
        "quantity": 10, "product": "MIS", "order_type": "limit_order",
        "limit_price": 80.0, "kind": "exit", "reason": "offline synthetic stop",
    }
    req.update(overrides)
    return req


@pytest.mark.asyncio
async def test_reduction_requires_real_reconciled_inventory_and_no_implicit_entry(isolated):
    client = PaperBroker()
    request = args(client)
    missing = await submit_reduction(**request)
    assert not missing.success and missing.status == "REJECTED"
    assert client.calls == []
    assert order_journal.get(order_journal.make_key(
        uid="synthetic-user", account_id="acct-1", strategy_id="synthetic-legacy",
        generation_id="gen-1", signal_id="stable-entry-1:reduce:exit",
        exchange="NFO", symbol="SYNTHETIC26NOV25000CE", side="SELL",
    )) is None
    db.set_inventory("acct-1", "synthetic-user", "SYNTHETIC26NOV25000CE", 5, exchange="NFO")
    overshoot = await submit_reduction(**request)
    assert not overshoot.success and client.calls == []
    mismatched = await submit_reduction(**args(client, exchange="BFO"))
    assert not mismatched.success and client.calls == []


@pytest.mark.asyncio
async def test_durable_ack_and_restart_dedup_never_resend(isolated):
    client = PaperBroker()
    db.set_inventory("acct-1", "synthetic-user", "SYNTHETIC26NOV25000CE", 10, exchange="NFO")
    first = await submit_reduction(**args(client))
    assert first.success and first.order_id == "OFFLINE-ORDER-1"
    assert first.intent_key
    saved = order_journal.get(first.intent_key)
    assert saved.order_id == first.order_id
    assert saved.side == "SELL" and saved.quantity == 10
    replay = await submit_reduction(**args(client, generation_id="gen-2"))
    assert replay.success and replay.order_id == first.order_id
    assert len(client.calls) == 1
    assert client.calls[0][1] == "SELL"
    assert client.calls[0][2] == 10


@pytest.mark.asyncio
async def test_unknown_reply_stays_unclaimable_and_never_resends(isolated):
    import httpx

    client = PaperBroker(error=httpx.ReadTimeout("reply lost"))
    db.set_inventory("acct-1", "synthetic-user", "SYNTHETIC26NOV25000CE", 10, exchange="NFO")
    first = await submit_reduction(**args(client))
    assert not first.success and first.status == "UNKNOWN"
    assert first.intent_key
    assert order_journal.get(first.intent_key).state == "UNKNOWN"
    second = await submit_reduction(**args(client))
    assert not second.success
    assert len(client.calls) == 1
    assert order_journal.get(first.intent_key).state == "UNKNOWN"


@pytest.mark.asyncio
async def test_unknown_broker_mode_and_bad_quantity_block_pre_journal(isolated):
    client = PaperBroker()
    client._is_paper = "false"
    assert not (await submit_reduction(**args(client))).success
    assert not (await submit_reduction(**args(client, quantity=1.5))).success
    assert not client.calls
