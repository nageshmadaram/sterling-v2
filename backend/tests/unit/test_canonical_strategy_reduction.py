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
    db.set_inventory("acct-1", "synthetic-user", "SYNTHETIC26NOV25000CE", 10, exchange="NFO")
    with db._conn() as conn:
        conn.execute(
            "UPDATE kite_inventory SET reconciliation_required=1 "
            "WHERE account_id=? AND uid=? AND symbol=?",
            ("acct-1", "synthetic-user", "SYNTHETIC26NOV25000CE"),
        )
    unreconciled = await submit_reduction(**request)
    assert unreconciled.status == "REJECTED" and client.calls == []


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
    replay = await submit_reduction(**args(
        client, generation_id="gen-2", reason="EOD sweep discovered same exit",
    ))
    assert replay.success and replay.order_id == first.order_id
    assert len(client.calls) == 1
    assert client.calls[0][1] == "SELL"
    assert client.calls[0][2] == 10
    # A later config generation must not mutate the same signal's quantity.
    db.set_inventory("acct-1", "synthetic-user", "SYNTHETIC26NOV25000CE", 20, exchange="NFO")
    mutated = await submit_reduction(**args(client, quantity=11, generation_id="gen-3"))
    assert not mutated.success and mutated.status == "REJECTED"
    assert "immutable_intent_conflict" in mutated.error
    assert len(client.calls) == 1


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


@pytest.mark.asyncio
async def test_restarted_sqlite_canonical_reduction_never_sends_an_acked_exit_twice(
    isolated, tmp_path, monkeypatch,
):
    """Real SQLite backup/reopen with a fresh broker double and unchanged signal."""
    import sqlite3
    from pathlib import Path

    original_path = Path(db._DB_PATH)
    broker = PaperBroker()
    db.set_inventory("acct-1", "synthetic-user", "SYNTHETIC26NOV25000CE", 10, exchange="NFO")
    first = await submit_reduction(**args(broker))
    assert first.success and len(broker.calls) == 1

    restored_path = tmp_path / "restarted.sqlite"
    with sqlite3.connect(original_path) as src:
        with sqlite3.connect(restored_path) as dest:
            src.backup(dest)
            assert dest.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    monkeypatch.setattr(db, "_DB_PATH", str(restored_path))
    assert db.init()
    assert order_journal.get(first.intent_key).order_id == first.order_id
    after_restart_broker = PaperBroker()
    replay = await submit_reduction(**args(after_restart_broker, generation_id="new-config"))
    assert replay.success and replay.order_id == first.order_id
    assert not after_restart_broker.calls


@pytest.mark.asyncio
async def test_restarted_unknown_exit_stays_unclaimable_and_requires_broker_truth(
    isolated, tmp_path, monkeypatch,
):
    """A socket timeout's UNKNOWN survives an isolated database restart."""
    import sqlite3
    from pathlib import Path
    import httpx

    db.set_inventory("acct-1", "synthetic-user", "SYNTHETIC26NOV25000CE", 10, exchange="NFO")
    before = await submit_reduction(**args(PaperBroker(error=httpx.ReadTimeout("no ACK"))))
    assert before.status == "UNKNOWN"
    original_path = Path(db._DB_PATH)
    restored = tmp_path / "unknown-restored.sqlite"
    with sqlite3.connect(original_path) as src:
        with sqlite3.connect(restored) as dest:
            src.backup(dest)
    monkeypatch.setattr(db, "_DB_PATH", str(restored))
    assert db.init()
    broker = PaperBroker()
    replay = await submit_reduction(**args(broker))
    assert not replay.success
    assert order_journal.get(before.intent_key).state == "UNKNOWN"
    assert broker.calls == []


@pytest.mark.asyncio
async def test_synthetic_live_transport_requires_exact_fresh_broker_product_bucket(isolated):
    """No network: _is_paper=False deliberately exercises real reduction gates."""
    class OfflineLiveLikeBroker(PaperBroker):
        _is_paper = False

        def __init__(self, product, quantity):
            super().__init__()
            self.product = product
            self.quantity = quantity

        async def get_positions_raw(self):
            return {"net": [{
                "tradingsymbol": "SYNTHETIC26NOV25000CE",
                "exchange": "NFO",
                "product": self.product,
                "quantity": self.quantity,
            }], "day": []}

    db.set_inventory("acct-1", "synthetic-user", "SYNTHETIC26NOV25000CE", 10, exchange="NFO")
    wrong_bucket = OfflineLiveLikeBroker("NRML", 10)
    denied = await submit_reduction(**args(wrong_bucket))
    assert not denied.success and denied.status == "REJECTED"
    assert "Broker has no" in denied.error
    assert wrong_bucket.calls == []

    # A previously refused RESERVED journal intent may be claimed only after
    # the exact broker bucket has been reverified. It may not be invented.
    exact = OfflineLiveLikeBroker("MIS", 10)
    accepted = await submit_reduction(**args(exact))
    assert accepted.success and accepted.order_id == "OFFLINE-ORDER-1"
    assert len(exact.calls) == 1
    assert exact.calls[0][3]["product"] == "MIS"


@pytest.mark.asyncio
async def test_short_cover_buy_is_canonical_reduction_not_a_new_long(isolated):
    """A short futures position may be reduced by BUY, never via a SELL-only shim."""
    client = PaperBroker()
    db.set_inventory("acct-1", "synthetic-user", "SYNTHETIC26NOV25000CE", -10, exchange="NFO")
    result = await submit_reduction(**args(client, side="BUY", kind="short-cover"))
    assert result.success and result.status == "ACKNOWLEDGED"
    assert len(client.calls) == 1 and client.calls[0][1] == "BUY"
    intent = order_journal.get(result.intent_key)
    assert intent.side == "BUY" and intent.quantity == 10
    assert intent.payload["reduction_kind"] == "short-cover"


@pytest.mark.asyncio
async def test_pre_disarm_inventory_refuses_missing_and_wrong_signed_side(isolated):
    from app.services.canonical_strategy_reduction import reduction_inventory_refusal
    client = PaperBroker()
    symbol = "SYNTHETIC26NOV25000CE"
    args = dict(client=client, uid="synthetic-user", symbol=symbol,
                exchange="NFO", quantity=10, side="SELL")
    assert reduction_inventory_refusal(**args)
    db.set_inventory("acct-1", "synthetic-user", symbol, -10, exchange="NFO")
    assert reduction_inventory_refusal(**args)
    assert reduction_inventory_refusal(**{**args, "side": "BUY"}) is None
    assert reduction_inventory_refusal(**{**args, "exchange": "BFO"})
