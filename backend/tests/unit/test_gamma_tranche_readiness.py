"""Offline negative lineage evidence; no broker or execution capability."""
from __future__ import annotations

import pytest

from app.services import db
from app.services.gamma_tranche_readiness import inspect_terminal_partial_exit
from app.services.kite_engine import fill_ledger, order_journal

UID, ACCOUNT, SYMBOL = "tranche-review-user", "acct-tranche", "SYNTHETIC26NOV25000CE"
ID = dict(uid=UID, account_id=ACCOUNT, strategy_id="gamma-unwind",
          signal_id="gamma:partial", exchange="NFO", symbol=SYMBOL,
          product="NRML")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_DB_PATH", str(tmp_path / "tranche.sqlite"))
    assert db.init()
    yield


def review(**overrides):
    return inspect_terminal_partial_exit(**{**ID, **overrides})


def original():
    return order_journal.reserve(
        uid=UID, account_id=ACCOUNT, strategy_id="gamma-unwind",
        generation_id="gen-1", signal_id="gamma:partial:reduce:exit",
        exchange="NFO", symbol=SYMBOL, side="SELL", quantity=50,
        payload={"intent_type": "EXIT", "product": "NRML", "reduction_kind": "exit"},
    )


def terminal_partial():
    intent = original()
    assert order_journal.claim_submission(intent.intent_key)
    order_journal.acknowledge(intent.intent_key, "EXIT-TRANCHE-1")
    observation = order_journal.observe_order(
        intent.intent_key, status="CANCELLED", order_id="EXIT-TRANCHE-1",
        filled_quantity=20, average_price=88.0,
        raw={"source": "offline_test", "confirmed_terminal": True},
    )
    assert observation.accepted and observation.intent.state == "CANCELLED"
    return observation.intent


def book_account_inventory(*, record_parent=True):
    # Real replay semantics in a disposable SQLite database. No invented
    # accounting state via db.set_inventory(): use the signed fill ledger.
    assert fill_ledger.record(
        account_id=ACCOUNT, uid=UID, symbol=SYMBOL, exchange="NFO",
        side="BUY", order_id="ENTRY-TRANCHE-1", cumulative_quantity=50,
        cumulative_value=5000.0, source="entry", lot_size=10,
    ).accepted
    if record_parent:
        assert fill_ledger.record(
            account_id=ACCOUNT, uid=UID, symbol=SYMBOL, exchange="NFO",
            side="SELL", order_id="EXIT-TRANCHE-1", cumulative_quantity=20,
            cumulative_value=1760.0, source="exit", lot_size=10,
        ).accepted


def test_no_parent_is_not_permission(store):
    result = review()
    assert result.reason == "parent_intent_missing"
    assert not result.ready_for_review and not result.execution_authorized


def test_reserved_or_unknown_is_never_a_completed_partial(store):
    intent = original()
    assert review().reason == "parent_not_terminal_partial"
    assert order_journal.claim_submission(intent.intent_key)
    order_journal.submission_uncertain(intent.intent_key, "lost broker reply")
    assert review().reason == "parent_not_terminal_partial"


def test_parent_projection_must_be_acknowledged(store):
    terminal_partial()
    book_account_inventory()
    assert review().reason == "parent_reconciliation_or_projection_pending"


def test_ledger_must_account_for_signed_parent_fill_not_only_strategy_row(store):
    intent = terminal_partial()
    assert order_journal.mark_projected(intent.intent_key, intent.projection_version)
    db.set_inventory(ACCOUNT, UID, SYMBOL, 30, exchange="NFO")
    assert review().reason == "account_inventory_not_projected_exactly"
    book_account_inventory(record_parent=False)
    db.set_inventory(ACCOUNT, UID, SYMBOL, 30, exchange="NFO")
    assert review().reason == "parent_signed_execution_missing"


def test_parent_signed_cumulative_value_must_match_journal(store):
    intent = terminal_partial()
    assert order_journal.mark_projected(intent.intent_key, intent.projection_version)
    assert fill_ledger.record(
        account_id=ACCOUNT, uid=UID, symbol=SYMBOL, exchange="NFO",
        side="BUY", order_id="ENTRY-TRANCHE-1", cumulative_quantity=50,
        cumulative_value=5000.0, source="entry", lot_size=10,
    ).accepted
    assert fill_ledger.record(
        account_id=ACCOUNT, uid=UID, symbol=SYMBOL, exchange="NFO",
        side="SELL", order_id="EXIT-TRANCHE-1", cumulative_quantity=20,
        cumulative_value=1740.0, source="exit", lot_size=10,
    ).accepted
    assert review().reason == "parent_signed_execution_disagrees"


def test_consistent_lineage_is_not_an_order_approval(store):
    intent = terminal_partial()
    book_account_inventory()
    assert order_journal.mark_projected(intent.intent_key, intent.projection_version)
    ready = review()
    assert ready.ready_for_review is True
    assert ready.execution_authorized is False
    assert ready.reason == "lineage_evidence_consistent_review_only"
    assert ready.remaining_quantity == 30
    assert ready.parent_order_id == "EXIT-TRANCHE-1"
    assert ready.parent_intent_key == intent.intent_key
    assert len(order_journal.unresolved(UID, ACCOUNT)) == 0
    assert len(order_journal.pending_projection(UID, ACCOUNT)) == 0


@pytest.mark.parametrize("altered", [
    {"uid": "wrong-user"}, {"account_id": "other-account"},
    {"strategy_id": "other-strategy"}, {"signal_id": "other-signal"},
    {"symbol": "OTHER"}, {"exchange": "BFO"}, {"product": "MIS"},
])
def test_cross_scope_or_product_mismatch_never_qualifies(store, altered):
    intent = terminal_partial()
    book_account_inventory()
    order_journal.mark_projected(intent.intent_key, intent.projection_version)
    result = review(**altered)
    assert not result.ready_for_review
    assert result.execution_authorized is False


def test_inventory_disagreement_fails_closed(store):
    intent = terminal_partial()
    book_account_inventory()
    order_journal.mark_projected(intent.intent_key, intent.projection_version)
    db.set_inventory(ACCOUNT, UID, SYMBOL, 50, exchange="NFO")
    assert review().reason == "account_inventory_not_projected_exactly"


def test_unresolved_sibling_intent_fails_closed(store):
    intent = terminal_partial()
    book_account_inventory()
    order_journal.mark_projected(intent.intent_key, intent.projection_version)
    order_journal.reserve(
        uid=UID, account_id=ACCOUNT, strategy_id="other-strategy",
        generation_id="gen-2", signal_id="other-entry",
        exchange="NFO", symbol=SYMBOL, side="BUY", quantity=1,
        payload={"intent_type": "ENTRY"},
    )
    assert review().reason == "other_bucket_intent_unresolved"


def test_reconciliation_flag_fails_closed(store):
    intent = terminal_partial()
    book_account_inventory()
    order_journal.mark_projected(intent.intent_key, intent.projection_version)
    with db._conn() as conn:
        conn.execute(
            "UPDATE kite_inventory SET reconciliation_required=1 "
            "WHERE uid=? AND account_id=? AND symbol=?",
            (UID, ACCOUNT, SYMBOL),
        )
    assert review().reason == "account_inventory_not_projected_exactly"


def test_unavailable_ledger_evidence_fails_closed(store, monkeypatch):
    intent = terminal_partial()
    book_account_inventory()
    order_journal.mark_projected(intent.intent_key, intent.projection_version)
    monkeypatch.setattr(fill_ledger, "increments", lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("secret details")))
    result = review()
    assert result.reason == "partial_lineage_evidence_unavailable"
    assert not result.execution_authorized
    assert "secret" not in str(result)
