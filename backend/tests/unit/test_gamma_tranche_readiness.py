"""Offline negative lineage evidence; no broker or execution capability."""
from __future__ import annotations

import pytest

from app.services import db
from app.services.gamma_tranche_readiness import (
    inspect_terminal_partial_exit, preview_terminal_child_identity,
)
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


def test_other_user_same_account_unresolved_sell_blocks_bucket(store):
    """Shared account exposure cannot ignore another tenant's pending SELL."""
    intent = terminal_partial()
    book_account_inventory()
    assert order_journal.mark_projected(intent.intent_key, intent.projection_version)
    order_journal.reserve(
        uid="other-account-operator", account_id=ACCOUNT,
        strategy_id="other-strategy", generation_id="gen-2",
        signal_id="pending-other-sell", exchange="NFO", symbol=SYMBOL,
        side="SELL", quantity=1,
        payload={"intent_type": "EXIT", "product": "NRML"},
    )
    result = review()
    assert not result.ready_for_review
    assert result.reason == "other_bucket_intent_unresolved"
    assert not result.execution_authorized


def preview(**overrides):
    return preview_terminal_child_identity(**{**ID, **overrides})


def prepared_terminal_lineage():
    parent = terminal_partial()
    book_account_inventory()
    assert order_journal.mark_projected(parent.intent_key,
                                        parent.projection_version)
    return parent


def test_child_preview_is_deterministic_across_rechecks_and_restart(store):
    """Review identity must not depend on clock, generation or scheduler state."""
    import importlib
    from dataclasses import asdict
    from app.services import gamma_tranche_readiness as module

    parent = prepared_terminal_lineage()
    before = list(order_journal.pending_projection(UID, ACCOUNT))
    first = preview()
    assert first.identifiable_for_review and not first.execution_authorized
    assert first.remaining_quantity == 30
    assert first.parent_intent_key == parent.intent_key
    assert first.reason == "stable_child_identity_preview_only"
    assert first.candidate_intent_key != parent.intent_key
    assert first.candidate_intent_key == order_journal.make_key(
        uid=UID, account_id=ACCOUNT, strategy_id="gamma-unwind",
        generation_id="arbitrary-generation-does-not-change-key",
        signal_id=f"{first.child_signal_id}:reduce:exit",
        exchange="NFO", symbol=SYMBOL, side="SELL",
    )
    assert not order_journal.identity_exists(
        uid=UID, account_id=ACCOUNT, strategy_id="gamma-unwind",
        signal_id=f"{first.child_signal_id}:reduce:exit",
        exchange="NFO", symbol=SYMBOL, side="SELL",
    )
    assert preview() == first
    assert module.preview_terminal_child_identity(**ID) == first
    # Reload creates a new dataclass *type*, but serialized identity remains
    # byte-for-byte stable across processes and code module initialization.
    assert asdict(importlib.reload(module).preview_terminal_child_identity(**ID)) == asdict(first)
    # Preview is read-only and cannot reserve/modify the journal or inventory.
    assert before == list(order_journal.pending_projection(UID, ACCOUNT))
    assert len(order_journal.unresolved(UID, ACCOUNT)) == 0


def test_existing_child_identity_is_never_an_automatic_retry(store):
    prepared_terminal_lineage()
    candidate = preview()
    assert candidate.identifiable_for_review
    reserved = order_journal.reserve(
        uid=UID, account_id=ACCOUNT, strategy_id="gamma-unwind",
        generation_id="review-manual-fixture",
        signal_id=f"{candidate.child_signal_id}:reduce:exit",
        exchange="NFO", symbol=SYMBOL, side="SELL", quantity=30,
        payload={"product": "NRML", "intent_type": "EXIT",
                 "reduction_kind": "exit"},
    )
    assert reserved.intent_key == candidate.candidate_intent_key
    result = preview()
    assert not result.identifiable_for_review
    assert not result.execution_authorized
    # Refusal may be discovered at the earlier account-bucket query.
    assert result.reason in {
        "other_bucket_intent_unresolved", "child_identity_already_recorded"
    }
    assert order_journal.get(reserved.intent_key).state == "RESERVED"


def test_terminal_partial_preview_refuses_other_user_pending_orders(store):
    prepared_terminal_lineage()
    order_journal.reserve(
        uid="another-owner", account_id=ACCOUNT, strategy_id="other-strategy",
        generation_id="gen-2", signal_id="different-entry",
        exchange="NFO", symbol=SYMBOL, side="SELL", quantity=1,
        payload={"product": "NRML"},
    )
    result = preview()
    assert not result.identifiable_for_review
    assert result.reason == "other_bucket_intent_unresolved"
    assert not result.execution_authorized


def test_child_preview_rechecks_account_bucket_after_initial_audit(store, monkeypatch):
    prepared_terminal_lineage()
    original_identity_exists = order_journal.identity_exists

    def concurrent_other_user_reservation(**kwargs):
        order_journal.reserve(
            uid="racing-other-user", account_id=ACCOUNT,
            strategy_id="other-strategy", generation_id="gen-race",
            signal_id="racing-exit", exchange="NFO", symbol=SYMBOL,
            side="SELL", quantity=1, payload={"product": "NRML"},
        )
        return original_identity_exists(**kwargs)

    monkeypatch.setattr(order_journal, "identity_exists",
                        concurrent_other_user_reservation)
    result = preview()
    assert not result.identifiable_for_review
    assert result.reason == "other_bucket_intent_unresolved"
    assert not result.execution_authorized


def test_child_preview_fails_closed_when_journal_preflight_unavailable(store, monkeypatch):
    prepared_terminal_lineage()
    monkeypatch.setattr(order_journal, "identity_exists", lambda **_kwargs:
                        (_ for _ in ()).throw(RuntimeError("internal token secret")))
    result = preview()
    assert not result.identifiable_for_review
    assert result.reason == "child_identity_evidence_unavailable"
    assert not result.execution_authorized
    assert "secret" not in repr(result)


def test_candidate_requires_terminal_parent_not_cancelled_before_send(store):
    assert preview().reason == "parent_intent_missing"
    intent = original()
    assert preview().reason == "parent_not_terminal_partial"
    assert order_journal.claim_submission(intent.intent_key)
    order_journal.submission_uncertain(intent.intent_key, "ack_unknown")
    assert preview().reason == "parent_not_terminal_partial"


def test_account_bucket_lookup_checks_other_owner_and_failed_database(store, monkeypatch):
    assert order_journal.account_bucket_has_unresolved(
        account_id=ACCOUNT, exchange="NFO", symbol=SYMBOL,
    ) is False
    order_journal.reserve(
        uid="other-owner", account_id=ACCOUNT, strategy_id="different",
        generation_id="gen-1", signal_id="pending",
        exchange="NFO", symbol=SYMBOL, side="SELL", quantity=1,
    )
    assert order_journal.account_bucket_has_unresolved(
        account_id=ACCOUNT, exchange="NFO", symbol=SYMBOL,
    ) is True
    assert order_journal.account_bucket_has_unresolved(
        account_id="other-account", exchange="NFO", symbol=SYMBOL,
    ) is False
    assert order_journal.account_bucket_has_unresolved(
        account_id=ACCOUNT, exchange="BFO", symbol=SYMBOL,
    ) is False
    monkeypatch.setattr(db, "is_available", lambda: False)
    with pytest.raises(RuntimeError, match="durable_order_journal_unavailable"):
        order_journal.account_bucket_has_unresolved(
            account_id=ACCOUNT, exchange="NFO", symbol=SYMBOL,
        )


def test_terminal_zero_fill_child_still_locks_same_preview_identity(store):
    """A rejected/cancelled child cannot get a newly minted replay identity."""
    prepared_terminal_lineage()
    proposal = preview()
    child = order_journal.reserve(
        uid=UID, account_id=ACCOUNT, strategy_id="gamma-unwind",
        generation_id="offline-child-gen", signal_id=f"{proposal.child_signal_id}:reduce:exit",
        exchange="NFO", symbol=SYMBOL, side="SELL", quantity=30,
        payload={"product": "NRML", "intent_type": "EXIT", "reduction_kind": "exit"},
    )
    assert order_journal.transition(child.intent_key, "CANCELLED").state == "CANCELLED"
    assert not order_journal.account_bucket_has_unresolved(
        account_id=ACCOUNT, exchange="NFO", symbol=SYMBOL,
    )
    outcome = preview()
    assert outcome.reason == "child_identity_already_recorded"
    assert not outcome.identifiable_for_review and not outcome.execution_authorized


def test_other_user_terminal_unprojected_order_is_account_bucket_blocker(store):
    prepared_terminal_lineage()
    other = order_journal.reserve(
        uid="other-operator", account_id=ACCOUNT, strategy_id="other",
        generation_id="gen-b", signal_id="other-exit",
        exchange="NFO", symbol=SYMBOL, side="SELL", quantity=5,
    )
    assert order_journal.claim_submission(other.intent_key)
    order_journal.acknowledge(other.intent_key, "EX-OTHER")
    observed = order_journal.observe_order(
        other.intent_key, status="CANCELLED", order_id="EX-OTHER",
        filled_quantity=0, average_price=0.0,
    )
    assert observed.accepted and observed.intent.projection_pending
    assert order_journal.account_bucket_has_unresolved(
        account_id=ACCOUNT, exchange="NFO", symbol=SYMBOL,
    )
    outcome = preview()
    assert outcome.reason == "other_bucket_intent_unresolved"
    assert not outcome.execution_authorized
