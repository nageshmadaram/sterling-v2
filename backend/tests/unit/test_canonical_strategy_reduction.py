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


@pytest.mark.asyncio
async def test_canonical_exit_broker_fill_observed_before_strategy_projection_ack(isolated):
    """An ACK is not a fill; a resolved paper fill becomes a durable journal event."""
    from app.services.canonical_fill_reconciliation import (
        resolve_canonical_reduction, mark_reduction_projected,
    )
    client = PaperBroker()
    db.set_inventory("acct-1", "synthetic-user", "SYNTHETIC26NOV25000CE", 10, exchange="NFO")
    ack = await submit_reduction(**args(client))
    assert ack.status == "ACKNOWLEDGED"
    assert order_journal.get(ack.intent_key).state == "SUBMITTED"
    result = await resolve_canonical_reduction(
        uid="synthetic-user", intent_key=ack.intent_key, client=client,
        order_id=ack.order_id, expected_quantity=10, reference_price=80.0,
    )
    assert result.settled and result.filled_quantity == 10
    assert result.intent_key == ack.intent_key and result.projection_version > 0
    before = order_journal.get(ack.intent_key)
    assert before.state == "FILLED" and before.projection_pending
    assert before.filled_quantity == 10
    # A crash before the local position write leaves projection_pending=1.
    assert any(row.intent_key == ack.intent_key
               for row in order_journal.pending_projection("synthetic-user", "acct-1"))
    assert mark_reduction_projected(result)
    assert not order_journal.get(ack.intent_key).projection_pending


@pytest.mark.asyncio
async def test_canonical_exit_order_identity_mismatch_cannot_project_a_fill(isolated):
    from app.services.canonical_fill_reconciliation import (
        resolve_canonical_reduction, mark_reduction_projected,
    )
    client = PaperBroker()
    db.set_inventory("acct-1", "synthetic-user", "SYNTHETIC26NOV25000CE", 10, exchange="NFO")
    ack = await submit_reduction(**args(client))
    result = await resolve_canonical_reduction(
        uid="synthetic-user", intent_key=ack.intent_key, client=client,
        order_id="OTHER-UNRELATED-BROKER-ID", expected_quantity=10,
        reference_price=80.0,
    )
    assert not result.settled
    assert "identity_mismatch" in result.reason
    assert not mark_reduction_projected(result)
    assert order_journal.get(ack.intent_key).state == "SUBMITTED"

# Broker evidence must be authoritative, exact and finite. These tests use a
# synthetic postback client: no network, broker session or live order is created.
@pytest.mark.parametrize("value", [
    "1.5", -2, "-0.5", float("nan"), float("inf"), "-Infinity",
    True, "not-a-number", None,
])
def test_broker_numeric_parser_refuses_nonintegral_or_nonfinite_quantity(value):
    from app.services.canonical_fill_reconciliation import _safe_int
    assert _safe_int(value) == 0


@pytest.mark.parametrize("value, expected", [
    (10, 10), ("10", 10), ("10.0", 10), (0, 0),
])
def test_broker_numeric_parser_preserves_exact_nonnegative_quantity(value, expected):
    from app.services.canonical_fill_reconciliation import _safe_int
    assert _safe_int(value) == expected


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "-Infinity", -1, "NaN"])
def test_broker_numeric_parser_refuses_bad_fill_prices(value):
    from app.services.canonical_fill_reconciliation import _safe_float
    assert _safe_float(value) == 0.0


@pytest.mark.asyncio
@pytest.mark.parametrize("quantity, average, reason", [
    (None, "80.0", "complete_filled_quantity_unverified"),
    ("1.5", "80.0", "complete_filled_quantity_unverified"),
    ("10", "NaN", "filled_price_unverified"),
    ("10", "Infinity", "filled_price_unverified"),
    ("9", "80.0", "complete_quantity_mismatch"),
    ("11", "80.0", "broker_overfill"),  # Overshoot is a stronger explicit refusal.
])
async def test_complete_postback_invalid_evidence_never_settles_exit(
    quantity, average, reason,
):
    from app.services.canonical_fill_reconciliation import resolve_acknowledged_order

    class SyntheticPostback:
        _is_paper = True
        _is_simulated = True

        async def get_order_history(self, order_id):
            assert order_id == "SYNTHETIC-ORDER"
            return [{"status": "COMPLETE", "filled_quantity": quantity,
                     "average_price": average}]

        async def get_order_trades(self, order_id):
            return []

    resolved = await resolve_acknowledged_order(
        client=SyntheticPostback(), order_id="SYNTHETIC-ORDER",
        expected_quantity=10, reference_price=80.0, timeout_s=0,
        cancel_unfilled=False,
    )
    assert not resolved.settled
    assert resolved.reason == reason


@pytest.mark.asyncio
async def test_valid_complete_postback_with_exact_fill_quantity_can_settle_exit():
    from app.services.canonical_fill_reconciliation import resolve_acknowledged_order

    class SyntheticPostback:
        _is_paper = True
        _is_simulated = True

        async def get_order_history(self, order_id):
            return [{"status": "COMPLETE", "filled_quantity": "10",
                     "average_price": "80.0"}]

        async def get_order_trades(self, order_id):
            return []

    result = await resolve_acknowledged_order(
        client=SyntheticPostback(), order_id="SYNTHETIC-ORDER",
        expected_quantity=10, reference_price=80.0, timeout_s=0,
        cancel_unfilled=False,
    )
    assert result.settled and result.terminal
    assert result.filled_quantity == 10 and result.average_price == 80.0


@pytest.mark.asyncio
@pytest.mark.parametrize("status, quantity, price, reason", [
    ("OPEN", "11", "80.0", "broker_overfill"),
    ("CANCELLED", "11", "80.0", "broker_overfill"),
    ("OPEN", "10", "0", "filled_price_unverified"),
    ("OPEN", "10", "NaN", "filled_price_unverified"),
    ("UNRECOGNIZED", "10", "80.0", "broker_status_unverified"),
])
async def test_full_or_overfilled_broker_evidence_never_false_settles(
    status, quantity, price, reason,
):
    from app.services.canonical_fill_reconciliation import resolve_acknowledged_order

    class SyntheticPostback:
        _is_paper = True
        _is_simulated = True

        async def get_order_history(self, order_id):
            return [{"order_id": order_id, "status": status,
                     "filled_quantity": quantity, "average_price": price}]

        async def get_order_trades(self, order_id):
            return []

    result = await resolve_acknowledged_order(
        client=SyntheticPostback(), order_id="SYNTHETIC-ORDER",
        expected_quantity=10, reference_price=80.0,
        timeout_s=0, cancel_unfilled=False,
    )
    assert not result.settled
    assert result.reason == reason


@pytest.mark.asyncio
@pytest.mark.parametrize("status, filled, expected_settled", [
    ("OPEN", 10, True),
    ("CANCELLED", 5, True),
    ("CANCELLED", 0, True),
])
async def test_valid_broker_full_and_terminal_partial_evidence_preserved(
    status, filled, expected_settled,
):
    from app.services.canonical_fill_reconciliation import resolve_acknowledged_order

    class SyntheticPostback:
        _is_paper = True
        _is_simulated = True

        async def get_order_history(self, order_id):
            return [{"status": status, "filled_quantity": filled,
                     "average_price": 80.0 if filled else 0}]

        async def get_order_trades(self, order_id):
            return []

    result = await resolve_acknowledged_order(
        client=SyntheticPostback(), order_id="SYNTHETIC-ORDER",
        expected_quantity=10, reference_price=80.0,
        timeout_s=0, cancel_unfilled=False,
    )
    assert result.settled is expected_settled
    assert result.filled_quantity == filled


@pytest.mark.asyncio
async def test_trade_aggregation_counts_duplicate_broker_trade_id_once():
    from app.services.canonical_fill_reconciliation import _broker_snapshot

    class DuplicateTradePostback:
        async def get_order_history(self, order_id):
            return [{"status": "OPEN", "filled_quantity": 1,
                     "average_price": 80.0}]

        async def get_order_trades(self, order_id):
            return [
                {"trade_id": "T1", "order_id": order_id, "quantity": 2, "price": 80.0},
                {"trade_id": "T1", "order_id": order_id, "quantity": 2, "price": 80.0},
                {"trade_id": "T2", "order_id": order_id, "quantity": 3, "price": 90.0},
            ]

    status, quantity, average, _ = await _broker_snapshot(
        DuplicateTradePostback(), "SYNTHETIC-ORDER",
    )
    assert status == "OPEN"
    assert quantity == 5  # two unique trades, not 7
    assert average == 86.0


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_trades", [
    [{"trade_id": "T1", "order_id": "OTHER", "quantity": 10, "price": 80}],
    [{"order_id": "SYNTHETIC-ORDER", "quantity": 10, "price": 80}],
    [{"trade_id": "T1", "quantity": 10, "price": float("inf")}],
    [
        {"trade_id": "T1", "quantity": 3, "price": 80},
        {"trade_id": "T1", "quantity": 4, "price": 80},
    ],
])
async def test_unattributable_or_conflicting_broker_trades_never_raise_fill_count(
    invalid_trades,
):
    from app.services.canonical_fill_reconciliation import _broker_snapshot

    class UntrustedTradePostback:
        async def get_order_history(self, order_id):
            return [{"status": "OPEN", "filled_quantity": 2,
                     "average_price": 80.0}]

        async def get_order_trades(self, order_id):
            return invalid_trades

    status, filled, average, _ = await _broker_snapshot(
        UntrustedTradePostback(), "SYNTHETIC-ORDER",
    )
    if any(trade.get("order_id") not in (None, "", "SYNTHETIC-ORDER") for trade in invalid_trades):
        # Cross-order evidence cannot even be attributed to the requested order.
        assert (status, filled, average) == ("IDENTITY MISMATCH", 0, 0.0)
    else:
        assert status == "OPEN"
        assert filled == 2  # Preserve only independently reported order evidence.
        assert average == 80.0


@pytest.mark.asyncio
@pytest.mark.parametrize("history, trades", [
    ([{"order_id": "OTHER", "status": "COMPLETE",
       "filled_quantity": 10, "average_price": 80}], []),
    ([{"order_id": "OTHER", "status": "OPEN", "filled_quantity": 0},
      {"order_id": "SYNTHETIC-ORDER", "status": "COMPLETE",
       "filled_quantity": 10, "average_price": 80}], []),
    ([{"order_id": "SYNTHETIC-ORDER", "status": "COMPLETE",
       "filled_quantity": 10, "average_price": 80}],
     [{"order_id": "OTHER", "trade_id": "T1", "quantity": 10, "price": 80}]),
])
async def test_wrong_order_history_or_trade_cannot_settle_or_cancel_ack(
    history, trades,
):
    from app.services.canonical_fill_reconciliation import resolve_acknowledged_order

    class CrossOrderPostback:
        _is_paper = True
        _is_simulated = True
        cancels = 0

        async def get_order_history(self, order_id):
            return history

        async def get_order_trades(self, order_id):
            return trades

        async def cancel_order(self, order_id):
            self.cancels += 1
            return {"status": "success"}

    client = CrossOrderPostback()
    result = await resolve_acknowledged_order(
        client=client, order_id="SYNTHETIC-ORDER",
        expected_quantity=10, reference_price=80,
        timeout_s=0, cancel_unfilled=True,
    )
    assert not result.settled
    assert result.filled_quantity == 0
    assert result.reason == "broker_order_identity_mismatch"
    assert client.cancels == 0


@pytest.mark.asyncio
async def test_mismatched_entry_broker_order_never_changes_journal_or_cancels(isolated):
    from app.services.canonical_fill_reconciliation import resolve_entry_fill

    client = PaperBroker()
    db.set_inventory("acct-1", "synthetic-user", "SYNTHETIC26NOV25000CE", 10, exchange="NFO")
    ack = await submit_reduction(**args(client))
    assert ack.success and ack.order_id

    client._is_simulated = True
    cancelled = []

    async def wrong_history(_order_id):
        return [{"order_id": "FOREIGN-ORDER", "status": "COMPLETE",
                 "filled_quantity": 10, "average_price": 80}]

    async def no_trades(_order_id):
        return []

    async def cancellation(order_id):
        cancelled.append(order_id)

    client.get_order_history = wrong_history
    client.get_order_trades = no_trades
    client.cancel_order = cancellation
    resolved = await resolve_entry_fill(
        uid="synthetic-user", client=client, order_id=ack.order_id,
        expected_quantity=10, reference_price=80,
        timeout_s=0, cancel_unfilled=True,
    )
    assert not resolved.settled
    assert resolved.reason == "broker_order_identity_mismatch"
    assert not cancelled
    assert order_journal.get(ack.intent_key).state == "SUBMITTED"


@pytest.mark.asyncio
async def test_stale_filled_journal_cannot_authorize_failed_reconciliation(isolated):
    from app.services.canonical_fill_reconciliation import _resolution

    client = PaperBroker()
    db.set_inventory("acct-1", "synthetic-user", "SYNTHETIC26NOV25000CE", 10, exchange="NFO")
    ack = await submit_reduction(**args(client))
    assert ack.success and ack.intent_key

    valid = order_journal.observe_order(
        ack.intent_key, status="COMPLETE", order_id=ack.order_id,
        filled_quantity=10, average_price=80,
    )
    assert valid.intent.state == "FILLED"
    assert _resolution(valid.intent, ack.order_id, status="COMPLETE").settled
    unknown = _resolution(
        order_journal.get(ack.intent_key), ack.order_id,
        status="UNKNOWN", reason="broker_order_identity_mismatch",
    )
    assert not unknown.settled

    conflict = order_journal.observe_order(
        ack.intent_key, status="COMPLETE", order_id=ack.order_id,
        filled_quantity=11, average_price=80,
    )
    assert conflict.intent.reconciliation_required
    replay = _resolution(conflict.intent, ack.order_id, status="COMPLETE")
    assert replay.reconciliation_required
    assert not replay.settled
    assert replay.filled_quantity == 10


@pytest.mark.parametrize("value", [
    "1e100000000", "9" * 1000, 10 ** 30, "1" * 128,
])
def test_broker_quantity_parser_rejects_bounded_resource_exhaustion(value):
    from app.services.canonical_fill_reconciliation import _safe_int
    assert _safe_int(value) == 0


@pytest.mark.parametrize("value", [True, False])
def test_broker_price_parser_rejects_boolean_execution_prices(value):
    from app.services.canonical_fill_reconciliation import _safe_float
    assert _safe_float(value) == 0.0


@pytest.mark.asyncio
@pytest.mark.parametrize("status, quantity, price, reason", [
    ("COMPLETE", 9, 80.0, "complete_quantity_mismatch"),
    ("COMPLETE", 0, 80.0, "complete_filled_quantity_unverified"),
    ("OPEN", 11, 80.0, "broker_overfill"),
    ("OPEN", 10, 0.0, "filled_price_unverified"),
])
async def test_ambiguous_live_exit_evidence_persists_new_entry_halt(
    isolated, status, quantity, price, reason,
):
    """No real broker: explicit real-mode synthetic client tests the halt."""
    from app.services.canonical_fill_reconciliation import resolve_acknowledged_order

    class SyntheticLiveExit:
        _is_paper = False
        _account_id = "acct-1"

        async def get_order_history(self, order_id):
            return [{"order_id": order_id, "status": status,
                     "filled_quantity": quantity, "average_price": price}]

        async def get_order_trades(self, order_id):
            return []

    result = await resolve_acknowledged_order(
        client=SyntheticLiveExit(), order_id="OFFLINE-EXIT",
        expected_quantity=10, reference_price=80.0,
        timeout_s=0.0, cancel_unfilled=False,
    )
    assert not result.settled
    assert result.reason == reason
    assert live_safety._KILL_SWITCH["enabled"] is True
    assert db.get_execution_control()["operator_state"] == "HALTED"


@pytest.mark.asyncio
async def test_unresolved_live_exit_halts_without_retrying_a_broker_send(isolated):
    from app.services.canonical_fill_reconciliation import resolve_acknowledged_order

    class SyntheticUnresolved:
        _is_paper = False
        _account_id = "acct-1"
        sends = 0

        async def get_order_history(self, order_id):
            return [{"order_id": order_id, "status": "OPEN",
                     "filled_quantity": 0, "average_price": 0}]

        async def get_order_trades(self, order_id):
            return []

    client = SyntheticUnresolved()
    outcome = await resolve_acknowledged_order(
        client=client, order_id="OFFLINE-EXIT", expected_quantity=10,
        reference_price=80, timeout_s=0, cancel_unfilled=False,
    )
    assert outcome.reason == "broker_order_unresolved_after_bounded_reconciliation"
    assert not outcome.settled
    assert client.sends == 0
    assert db.get_execution_control()["operator_state"] == "HALTED"


@pytest.mark.asyncio
async def test_valid_complete_live_exit_does_not_raise_global_halt(isolated):
    from app.services.canonical_fill_reconciliation import resolve_acknowledged_order

    class SyntheticConfirmed:
        _is_paper = False
        _account_id = "acct-1"

        async def get_order_history(self, order_id):
            return [{"order_id": order_id, "status": "COMPLETE",
                     "filled_quantity": 10, "average_price": 80}]

        async def get_order_trades(self, order_id):
            return []

    completed = await resolve_acknowledged_order(
        client=SyntheticConfirmed(), order_id="OFFLINE-EXIT",
        expected_quantity=10, reference_price=80, timeout_s=0,
    )
    assert completed.settled
    assert completed.filled_quantity == 10
    assert not live_safety._KILL_SWITCH["enabled"]
    assert db.get_execution_control()["operator_state"] == "RUNNING"


@pytest.mark.asyncio
async def test_ambiguous_simulated_exit_never_mutates_live_kill_switch(isolated):
    from app.services.canonical_fill_reconciliation import resolve_acknowledged_order

    class SyntheticSimulation:
        _is_paper = True
        _is_simulated = True

        async def get_order_history(self, order_id):
            return [{"order_id": order_id, "status": "COMPLETE",
                     "filled_quantity": 3, "average_price": 80}]

        async def get_order_trades(self, order_id):
            return []

    resolved = await resolve_acknowledged_order(
        client=SyntheticSimulation(), order_id="SIM-EXIT",
        expected_quantity=10, reference_price=80, timeout_s=0,
    )
    assert not resolved.settled
    assert resolved.reason == "complete_quantity_mismatch"
    assert not live_safety._KILL_SWITCH["enabled"]
    assert db.get_execution_control()["operator_state"] == "RUNNING"


@pytest.mark.asyncio
@pytest.mark.parametrize("quantity", [True, False, 0, -1, 1.9, 3.0, "10"])
async def test_reduction_fill_resolution_refuses_implicit_quantity_coercion(
    quantity,
):
    from app.services.canonical_fill_reconciliation import resolve_acknowledged_order

    with pytest.raises(ValueError, match="expected_quantity_must_be_positive_integral"):
        await resolve_acknowledged_order(
            client=PaperBroker(), order_id="OFFLINE-ORDER",
            expected_quantity=quantity, reference_price=80.0,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("price", [0, -10, True, False, float("nan"), float("inf")])
async def test_instant_paper_fill_never_settles_at_invalid_reference_price(price):
    from app.services.canonical_fill_reconciliation import resolve_acknowledged_order

    with pytest.raises(ValueError, match="reference_price_must_be_positive"):
        await resolve_acknowledged_order(
            client=PaperBroker(), order_id="OFFLINE-ORDER",
            expected_quantity=10, reference_price=price,
        )


@pytest.mark.asyncio
async def test_canonical_entry_rejects_invalid_reference_before_journal_observation(isolated):
    from app.services.canonical_fill_reconciliation import resolve_entry_fill

    client = PaperBroker()
    db.set_inventory("acct-1", "synthetic-user", "SYNTHETIC26NOV25000CE", 10, exchange="NFO")
    ack = await submit_reduction(**args(client))
    assert ack.success
    with pytest.raises(ValueError, match="reference_price_must_be_positive"):
        await resolve_entry_fill(
            uid="synthetic-user", client=client, order_id=ack.order_id,
            expected_quantity=10, reference_price=float("nan"),
        )
    assert order_journal.get(ack.intent_key).state == "SUBMITTED"


@pytest.mark.asyncio
async def test_canonical_exit_journal_identity_conflict_halts_new_real_entries(isolated):
    from app.services.canonical_fill_reconciliation import resolve_canonical_reduction

    paper = PaperBroker()
    db.set_inventory("acct-1", "synthetic-user", "SYNTHETIC26NOV25000CE", 10, exchange="NFO")
    ack = await submit_reduction(**args(paper))
    assert ack.success

    class SyntheticBrokerDifferentOrder:
        _is_paper = False
        _account_id = "acct-1"

        async def get_order_history(self, order_id):
            return [{"order_id": order_id, "status": "COMPLETE",
                     "filled_quantity": 10, "average_price": 80.0}]

        async def get_order_trades(self, order_id):
            return []

    unresolved = await resolve_canonical_reduction(
        uid="synthetic-user", intent_key=ack.intent_key,
        client=SyntheticBrokerDifferentOrder(), order_id="OTHER-ORDER",
        expected_quantity=10, reference_price=80.0, timeout_s=0,
    )
    assert not unresolved.settled
    assert "canonical_reduction_journal_unresolved" in unresolved.reason
    assert order_journal.get(ack.intent_key).order_id == ack.order_id
    assert live_safety._KILL_SWITCH["enabled"] is True
    assert db.get_execution_control()["operator_state"] == "HALTED"
