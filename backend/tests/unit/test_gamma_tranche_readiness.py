"""Offline negative lineage evidence; no broker or execution capability."""
from __future__ import annotations

import pytest

from app.services import db
from app.services.gamma_tranche_readiness import (
    inspect_terminal_partial_exit, preview_terminal_child_identity,
)
from app.services.kite_engine import fill_ledger, order_journal

@pytest.fixture(autouse=True)
def isolated_gamma_review_mode(monkeypatch):
    """This module tests a non-executable offline review claim only.

    The general backend conftest tests live-mode safety by default. A review
    hold must instead be denied under that setting; explicitly opt the tests
    into the distinct offline-only mode, and test the denial separately.
    """
    monkeypatch.setenv("STERLING_LIVE_EXECUTION", "0")


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


def _hold(**overrides):
    from app.services.gamma_tranche_review_claims import hold_terminal_child_review
    return hold_terminal_child_review(**{**ID, **overrides})


def test_atomic_review_claim_persists_without_creating_canonical_order(store):
    parent = prepared_terminal_lineage()
    proposed = preview()
    first = _hold()
    assert first.held_for_review and not first.execution_authorized
    assert first.reason == "review_held_offline_only" and not first.replay
    assert first.candidate_intent_key == proposed.candidate_intent_key
    assert first.parent_intent_key == parent.intent_key
    assert first.remaining_quantity == 30
    assert order_journal.get(proposed.candidate_intent_key) is None
    with db._conn() as conn:
        rows = conn.execute("SELECT * FROM gamma_terminal_review_holds").fetchall()
        assert len(rows) == 1 and rows[0]["status"] == "REVIEW_HELD"
        assert rows[0]["parent_order_id"] == "EXIT-TRANCHE-1"
        assert rows[0]["inventory_version"] > 0
    assert len(order_journal.unresolved(UID, ACCOUNT)) == 0


def test_atomic_review_claim_replays_same_immutable_row_after_restart(store):
    prepared_terminal_lineage()
    first = _hold()
    assert first.held_for_review
    # Reinitialization simulates a fresh process opening the existing DB file.
    assert db.init()
    second = _hold()
    assert second.held_for_review and second.replay
    assert second.reason == "review_hold_replayed"
    assert second.candidate_intent_key == first.candidate_intent_key
    assert not second.execution_authorized
    with db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM gamma_terminal_review_holds").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM kite_order_intents").fetchone()[0] == 1


def test_review_claim_refuses_hardlinked_temp_alias(store, tmp_path, monkeypatch):
    """A temp-looking alias may share an inode with an existing database."""
    import os
    prepared_terminal_lineage()
    source = db._DB_PATH
    alias = tmp_path / "hardlink-test.sqlite"
    os.link(source, alias)
    assert os.stat(source).st_nlink == 2
    monkeypatch.setattr(db, "_DB_PATH", str(alias))
    outcome = _hold()
    assert outcome.reason == "offline_disposable_test_database_required"
    assert not outcome.held_for_review and not outcome.execution_authorized
    monkeypatch.setattr(db, "_DB_PATH", source)
    with db._conn() as conn:
        assert conn.execute("SELECT name FROM sqlite_master "
                            "WHERE name='gamma_terminal_review_holds'").fetchone() is None


def test_review_claim_serializes_parallel_same_candidate_without_duplicates(store):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    prepared_terminal_lineage()
    barrier = Barrier(2)

    def concurrent():
        barrier.wait(timeout=10)
        return _hold()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: concurrent(), (1, 2)))
    assert first.held_for_review and second.held_for_review
    assert not first.execution_authorized and not second.execution_authorized
    assert sorted([first.replay, second.replay]) == [False, True]
    with db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM gamma_terminal_review_holds").fetchone()[0] == 1


def test_review_claim_requires_offline_disposable_db_even_if_lineage_valid(store, monkeypatch):
    prepared_terminal_lineage()
    monkeypatch.setattr(db, "under_test", lambda: False)
    result = _hold()
    assert result.reason == "offline_disposable_test_database_required"
    assert not result.held_for_review and not result.execution_authorized
    with db._conn() as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='gamma_terminal_review_holds'").fetchone() is None


def test_review_claim_cannot_ignore_new_other_user_intent_at_transaction_boundary(store, monkeypatch):
    from app.services import gamma_tranche_review_claims as claims

    prepared_terminal_lineage()
    initial_preview = claims.preview_terminal_child_identity

    def racing_preview(**kwargs):
        proposal = initial_preview(**kwargs)
        assert proposal.identifiable_for_review
        order_journal.reserve(
            uid="new-cross-owner", account_id=ACCOUNT, strategy_id="competitor",
            generation_id="gen-1", signal_id="incoming-order",
            exchange="NFO", symbol=SYMBOL, side="SELL", quantity=1,
        )
        return proposal

    monkeypatch.setattr(claims, "preview_terminal_child_identity", racing_preview)
    outcome = _hold()
    assert outcome.reason == "account_bucket_changed_during_review"
    assert not outcome.held_for_review and not outcome.execution_authorized
    with db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM gamma_terminal_review_holds").fetchone()[0] == 0


def test_review_claim_rechecks_account_inventory_in_same_write_transaction(store, monkeypatch):
    from app.services import gamma_tranche_review_claims as claims
    prepared_terminal_lineage()
    prior = claims.preview_terminal_child_identity

    def stale_preview(**kwargs):
        result = prior(**kwargs)
        with db._conn() as conn:
            conn.execute("UPDATE kite_inventory SET net_quantity=50 "
                         "WHERE uid=? AND account_id=? AND symbol=?",
                         (UID, ACCOUNT, SYMBOL))
        return result

    monkeypatch.setattr(claims, "preview_terminal_child_identity", stale_preview)
    result = _hold()
    assert result.reason == "inventory_changed_during_review"
    assert not result.held_for_review and not result.execution_authorized


def test_review_claim_refuses_parent_order_id_changed_after_preview(store, monkeypatch):
    from app.services import gamma_tranche_review_claims as claims
    prepared_terminal_lineage()
    prior = claims.preview_terminal_child_identity

    def stale_preview(**kwargs):
        result = prior(**kwargs)
        with db._conn() as conn:
            conn.execute("UPDATE kite_order_intents SET order_id='CHANGED-ORDER' "
                         "WHERE intent_key=?", (result.parent_intent_key,))
        return result

    monkeypatch.setattr(claims, "preview_terminal_child_identity", stale_preview)
    result = _hold()
    assert result.reason == "parent_changed_during_review"
    assert not result.held_for_review and not result.execution_authorized


def test_review_claim_cannot_steal_an_existing_canonical_child(store, monkeypatch):
    from app.services import gamma_tranche_review_claims as claims
    prepared_terminal_lineage()
    prior = claims.preview_terminal_child_identity

    def racing_preview(**kwargs):
        result = prior(**kwargs)
        order_journal.reserve(
            uid=UID, account_id=ACCOUNT, strategy_id="gamma-unwind",
            generation_id="gen-child", signal_id=f"{result.child_signal_id}:reduce:exit",
            exchange="NFO", symbol=SYMBOL, side="SELL", quantity=30,
            payload={"intent_type": "EXIT", "product": "NRML", "reduction_kind": "exit"},
        )
        return result

    monkeypatch.setattr(claims, "preview_terminal_child_identity", racing_preview)
    result = _hold()
    assert result.reason == "account_bucket_changed_during_review"
    assert not result.held_for_review and not result.execution_authorized


def test_review_claim_storage_errors_are_fail_closed(store, monkeypatch):
    from app.services import gamma_tranche_review_claims as claims
    prepared_terminal_lineage()
    first = preview()
    assert first.identifiable_for_review
    prior = claims.db._conn
    from contextlib import contextmanager

    @contextmanager
    def broken_conn():
        raise RuntimeError("sqlite internal secret")
        yield

    monkeypatch.setattr(claims.db, "_conn", broken_conn)
    result = _hold()
    assert not result.held_for_review and not result.execution_authorized
    assert "secret" not in repr(result)
    monkeypatch.setattr(claims.db, "_conn", prior)
    assert order_journal.get(first.candidate_intent_key) is None


def test_account_wide_review_claim_excludes_separate_user_same_contract(store):
    prepared_terminal_lineage()
    initial = _hold()
    assert initial.held_for_review
    second_uid = "other-review-user"
    second_signal = "different-eligible-partial"
    other = order_journal.reserve(
        uid=second_uid, account_id=ACCOUNT, strategy_id="gamma-unwind",
        generation_id="other-gen", signal_id=f"{second_signal}:reduce:exit",
        exchange="NFO", symbol=SYMBOL, side="SELL", quantity=50,
        payload={"intent_type": "EXIT", "product": "NRML", "reduction_kind": "exit"},
    )
    assert order_journal.claim_submission(other.intent_key)
    order_journal.acknowledge(other.intent_key, "OTHER-EXIT")
    observed = order_journal.observe_order(
        other.intent_key, status="CANCELLED", order_id="OTHER-EXIT",
        filled_quantity=20, average_price=88.0,
    )
    assert observed.accepted
    assert fill_ledger.record(
        account_id=ACCOUNT, uid=second_uid, symbol=SYMBOL, exchange="NFO",
        side="BUY", order_id="OTHER-ENTRY", cumulative_quantity=50,
        cumulative_value=5000, source="entry", lot_size=10,
    ).accepted
    assert fill_ledger.record(
        account_id=ACCOUNT, uid=second_uid, symbol=SYMBOL, exchange="NFO",
        side="SELL", order_id="OTHER-EXIT", cumulative_quantity=20,
        cumulative_value=1760, source="exit", lot_size=10,
    ).accepted
    assert order_journal.mark_projected(other.intent_key, observed.intent.projection_version)
    alternative = {**ID, "uid": second_uid, "signal_id": second_signal}
    assert preview_terminal_child_identity(**alternative).reason == "other_owner_inventory_present"
    contender = _hold(uid=second_uid, signal_id=second_signal)
    assert contender.reason == "other_owner_inventory_present"
    assert not contender.held_for_review and not contender.execution_authorized
    with db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM gamma_terminal_review_holds").fetchone()[0] == 1


def test_failed_review_claim_insert_rolls_back_entire_transaction(store, monkeypatch):
    from app.services import gamma_tranche_review_claims as claims

    prepared_terminal_lineage()
    original_schema = claims._schema

    def rejecting_schema(conn):
        original_schema(conn)
        conn.execute("""CREATE TEMP TRIGGER forbid_gamma_claim
            BEFORE INSERT ON gamma_terminal_review_holds BEGIN
                SELECT RAISE(ABORT, 'synthetic database failure');
            END""")

    monkeypatch.setattr(claims, "_schema", rejecting_schema)
    refused = _hold()
    assert refused.reason == "review_claim_storage_or_consistency_unavailable"
    assert not refused.held_for_review and not refused.execution_authorized
    with db._conn() as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='gamma_terminal_review_holds'").fetchone() is None
    monkeypatch.setattr(claims, "_schema", original_schema)
    assert _hold().held_for_review


def test_other_owner_positive_inventory_blocks_review_even_without_pending_intents(store):
    """No journal pending does not imply the shared broker bucket is ours."""
    prepared_terminal_lineage()
    db.set_inventory(ACCOUNT, "other-uid", SYMBOL, 5, exchange="NFO")
    readiness = review()
    assert not readiness.ready_for_review
    assert readiness.reason == "other_owner_inventory_present"
    result = _hold()
    assert result.reason == "other_owner_inventory_present"
    assert not result.held_for_review and not result.execution_authorized


def test_review_claim_rechecks_other_owner_inventory_inside_transaction(store, monkeypatch):
    from app.services import gamma_tranche_review_claims as claims
    prepared_terminal_lineage()
    original_preview = claims.preview_terminal_child_identity

    def concurrent_other_owner_holding(**kwargs):
        proposed = original_preview(**kwargs)
        assert proposed.identifiable_for_review
        db.set_inventory(ACCOUNT, "unrelated-bucket-owner", SYMBOL, 1, exchange="NFO")
        return proposed

    monkeypatch.setattr(claims, "preview_terminal_child_identity", concurrent_other_owner_holding)
    outcome = _hold()
    assert outcome.reason == "other_owner_inventory_changed_during_review"
    assert not outcome.held_for_review and not outcome.execution_authorized
    with db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM gamma_terminal_review_holds").fetchone()[0] == 0


def test_review_hold_schema_enforces_one_immutable_row_per_account_bucket(store):
    import sqlite3
    prepared_terminal_lineage()
    first = _hold()
    assert first.held_for_review
    with pytest.raises(sqlite3.IntegrityError):
        with db._conn() as conn:
            conn.execute("""INSERT INTO gamma_terminal_review_holds
                SELECT 'OTHER-CANDIDATE', 'OTHER-PARENT', uid, account_id,
                  strategy_id, 'OTHER-SIGNAL', exchange, symbol, product,
                  'OTHER-ORDER', parent_filled_quantity, remaining_quantity,
                  inventory_version, parent_projection_version,
                  parent_observed_value, parent_increment_digest,
                  status, created_ms
                FROM gamma_terminal_review_holds""")
    with db._conn() as conn:
        rows = conn.execute("SELECT * FROM gamma_terminal_review_holds").fetchall()
        assert len(rows) == 1 and rows[0]["candidate_intent_key"] == first.candidate_intent_key


@pytest.mark.parametrize("setting", ["1", "true", "yes", "ON", "unexpected"])
def test_review_claim_denies_process_with_live_execution_enabled(store, monkeypatch, setting):
    prepared_terminal_lineage()
    monkeypatch.setenv("STERLING_LIVE_EXECUTION", setting)
    outcome = _hold()
    assert outcome.reason == "live_execution_environment_refused"
    assert not outcome.held_for_review and not outcome.execution_authorized
    with db._conn() as conn:
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name='gamma_terminal_review_holds'"
        ).fetchone() is None


@pytest.mark.parametrize("path_kind", ["outside_temp", "symlink", "uri", "memory", "relative"])
def test_review_claim_rejects_disguised_nontemporary_database_paths(
    store, monkeypatch, tmp_path, path_kind,
):
    from app.services.gamma_tranche_review_claims import _strictly_disposable_review_database
    prepared_terminal_lineage()
    if path_kind == "outside_temp":
        fake = str(__import__('pathlib').Path(__file__).resolve().parent / "operator_test.db")
    elif path_kind == "symlink":
        fake = str(tmp_path / "operator_test.sqlite")
        __import__('pathlib').Path(fake).symlink_to(
            __import__('pathlib').Path(__file__).resolve().parent / "operator_database.db"
        )
    elif path_kind == "uri":
        fake = f"file:{tmp_path / 'operator_test.sqlite'}"
    elif path_kind == "memory":
        fake = ":memory:"
    else:
        # A relative-path rejection test must not leave SQLite WAL sidecars in
        # the source checkout when another fixture executes cleanup.
        monkeypatch.chdir(tmp_path)
        fake = "relative_operator_test.sqlite"
    # The shared heuristic permits filenames with 'test'; this strict local
    # guard must not be fooled by names, URIs or symlink targets.
    assert not _strictly_disposable_review_database(fake)
    original_path = db._DB_PATH
    monkeypatch.setattr(db, "_DB_PATH", fake)
    try:
        result = _hold()
        assert result.reason == "offline_disposable_test_database_required"
        assert not result.held_for_review and not result.execution_authorized
    finally:
        # Other pytest cleanup hooks may still open the active test store.
        # Restore it *before* fixture teardown, not only via monkeypatch.
        db._DB_PATH = original_path


def test_offline_review_accepts_real_temp_path_and_denies_live_flag_after_restart(store):
    from app.services.gamma_tranche_review_claims import _strictly_disposable_review_database
    assert _strictly_disposable_review_database(db._DB_PATH)
    prepared_terminal_lineage()
    before = _hold()
    assert before.held_for_review and not before.execution_authorized
    import os
    original = os.environ.get("STERLING_LIVE_EXECUTION", "")
    os.environ["STERLING_LIVE_EXECUTION"] = "true"
    try:
        assert db.init()
        after = _hold()
        assert after.reason == "live_execution_environment_refused"
        assert not after.held_for_review and not after.execution_authorized
    finally:
        os.environ["STERLING_LIVE_EXECUTION"] = original
    replay = _hold()
    assert replay.held_for_review and replay.replay


def _child_review_command(script, path):
    """Spawn an independent Python interpreter with zero broker credentials."""
    import os
    import sys
    from pathlib import Path

    backend = str(Path(__file__).resolve().parents[2])
    env = {**os.environ, "PYTHONPATH": backend,
           "STERLING_UNDER_TEST": "1", "STERLING_LIVE_EXECUTION": "0"}
    # No broker environment secrets are needed for these synthetic reviews.
    for name in list(env):
        if any(part in name.upper() for part in ("KITE_API_KEY", "KITE_API_SECRET", "KITE_ACCESS_TOKEN")):
            env.pop(name, None)
    return [sys.executable, "-c", script, path], env, backend


def test_review_hold_is_idempotent_across_separate_python_processes(store):
    import json
    import subprocess

    prepared_terminal_lineage()
    script = '''
import json, sys
from app.services import db
from app.services.gamma_tranche_review_claims import hold_terminal_child_review
db._DB_PATH = sys.argv[1]
assert db.init()
result = hold_terminal_child_review(
    uid="tranche-review-user", account_id="acct-tranche", strategy_id="gamma-unwind",
    signal_id="gamma:partial", exchange="NFO", symbol="SYNTHETIC26NOV25000CE",
    product="NRML",
)
print(json.dumps({"ok": result.held_for_review, "replay": result.replay,
                  "reason": result.reason, "authorized": result.execution_authorized}))
'''
    args, env, cwd = _child_review_command(script, db._DB_PATH)
    first = subprocess.Popen(args, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    second = subprocess.Popen(args, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out1, err1 = first.communicate(timeout=30)
    out2, err2 = second.communicate(timeout=30)
    assert first.returncode == second.returncode == 0, (err1[-500:], err2[-500:])
    rows = [json.loads(out1.strip().splitlines()[-1]), json.loads(out2.strip().splitlines()[-1])]
    assert all(row["ok"] and not row["authorized"] for row in rows)
    assert sorted(row["replay"] for row in rows) == [False, True]
    with db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM gamma_terminal_review_holds").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM kite_order_intents").fetchone()[0] == 1


def test_os_crash_in_review_insert_rolls_back_claim_and_can_restart(store):
    import subprocess

    prepared_terminal_lineage()
    script = '''
import os, sys
from app.services import db
from app.services import gamma_tranche_review_claims as claims
db._DB_PATH = sys.argv[1]
assert db.init()
original = claims._schema
def crash_schema(conn):
    original(conn)
    conn.create_function("terminate_process", 0, lambda: os._exit(79))
    conn.execute("""CREATE TEMP TRIGGER crash_before_commit
        AFTER INSERT ON gamma_terminal_review_holds
        BEGIN SELECT terminate_process(); END""")
claims._schema = crash_schema
claims.hold_terminal_child_review(
    uid="tranche-review-user", account_id="acct-tranche", strategy_id="gamma-unwind",
    signal_id="gamma:partial", exchange="NFO", symbol="SYNTHETIC26NOV25000CE",
    product="NRML",
)
'''
    args, env, cwd = _child_review_command(script, db._DB_PATH)
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 79, result.stderr[-800:]
    with db._conn() as conn:
        row = conn.execute("SELECT name FROM sqlite_master WHERE name='gamma_terminal_review_holds'").fetchone()
        assert row is None  # DDL and INSERT were one interrupted transaction.
        assert conn.execute("SELECT COUNT(*) FROM kite_order_intents").fetchone()[0] == 1
    restarted = _hold()
    assert restarted.held_for_review and not restarted.replay and not restarted.execution_authorized
    with db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM gamma_terminal_review_holds").fetchone()[0] == 1


def test_review_hold_replay_refuses_reprojected_inventory_version(store):
    """Same net quantity at a newer inventory version is NOT the held proof."""
    prepared_terminal_lineage()
    first = _hold()
    assert first.held_for_review and not first.replay
    with db._conn() as conn:
        conn.execute(
            "UPDATE kite_inventory SET version=version+1 "
            "WHERE account_id=? AND uid=? AND symbol=?",
            (ACCOUNT, UID, SYMBOL),
        )
    result = _hold()
    assert result.reason == "existing_review_claim_evidence_changed"
    assert not result.held_for_review and not result.execution_authorized
    with db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM gamma_terminal_review_holds").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM kite_order_intents").fetchone()[0] == 1


def test_review_hold_replay_refuses_parent_projection_revision_drift(store):
    """A parent projection revision cannot silently renew a persisted claim."""
    parent = prepared_terminal_lineage()
    first = _hold()
    assert first.held_for_review and not first.replay
    with db._conn() as conn:
        conn.execute(
            "UPDATE kite_order_intents SET projection_version=projection_version+1 "
            "WHERE intent_key=?", (parent.intent_key,),
        )
    result = _hold()
    assert result.reason == "existing_review_claim_evidence_changed"
    assert not result.held_for_review and not result.execution_authorized
    with db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM gamma_terminal_review_holds").fetchone()[0] == 1


def test_review_hold_replay_refuses_reconciled_parent_value_correction(store):
    """Same filled quantity/order cannot silently revise a held price proof."""
    parent = prepared_terminal_lineage()
    first = _hold()
    assert first.held_for_review and not first.replay
    # Model a later corroborated correction of cumulative SELL value: the
    # journal and signed-fill row agree again, but do not match the held proof.
    with db._conn() as conn:
        conn.execute(
            "UPDATE kite_order_intents SET observed_value=observed_value+10 "
            "WHERE intent_key=?", (parent.intent_key,),
        )
        conn.execute(
            "UPDATE kite_execution_increments SET cumulative_value=cumulative_value+10, "
            "price=price+0.5 "
            "WHERE account_id=? AND uid=? AND symbol=? AND order_id=?",
            (ACCOUNT, UID, SYMBOL, "EXIT-TRANCHE-1"),
        )
    result = _hold()
    assert result.reason == "existing_review_claim_evidence_changed"
    assert not result.held_for_review and not result.execution_authorized
    with db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM gamma_terminal_review_holds").fetchone()[0] == 1


def test_legacy_disposable_review_schema_is_not_silently_upgraded(store):
    """A prior test schema lacks proof revisions; do not infer them on replay."""
    prepared_terminal_lineage()
    with db._conn() as conn:
        conn.execute("""CREATE TABLE gamma_terminal_review_holds (
            candidate_intent_key TEXT PRIMARY KEY,
            parent_intent_key TEXT NOT NULL UNIQUE,
            uid TEXT NOT NULL,
            account_id TEXT NOT NULL,
            strategy_id TEXT NOT NULL,
            signal_id TEXT NOT NULL,
            exchange TEXT NOT NULL,
            symbol TEXT NOT NULL,
            product TEXT NOT NULL,
            parent_order_id TEXT NOT NULL,
            parent_filled_quantity INTEGER NOT NULL,
            remaining_quantity INTEGER NOT NULL,
            inventory_version INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'REVIEW_HELD',
            created_ms INTEGER NOT NULL,
            UNIQUE(account_id, exchange, symbol)
        )""")
    outcome = _hold()
    assert outcome.reason == "review_claim_storage_or_consistency_unavailable"
    assert not outcome.held_for_review and not outcome.execution_authorized
    with db._conn() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(gamma_terminal_review_holds)")}
        assert "parent_projection_version" not in cols
        assert "parent_observed_value" not in cols
        assert conn.execute("SELECT COUNT(*) FROM gamma_terminal_review_holds").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM kite_order_intents").fetchone()[0] == 1


@pytest.mark.parametrize("field,value", [
    ("signed_quantity", -1),
    ("price", 1.0),
])
def test_signed_parent_increment_rejects_inconsistent_delta_or_price(store, field, value):
    """A matching final cumulative SELL is not proof of each signed fill."""
    prepared_terminal_lineage()
    with db._conn() as conn:
        conn.execute(
            f"UPDATE kite_execution_increments SET {field}=? "
            "WHERE account_id=? AND uid=? AND symbol=? AND order_id=?",
            (value, ACCOUNT, UID, SYMBOL, "EXIT-TRANCHE-1"),
        )
    assert review().reason == "parent_signed_execution_disagrees"
    claim = _hold()
    assert claim.reason == "parent_signed_execution_disagrees"
    assert not claim.held_for_review and not claim.execution_authorized
    with db._conn() as conn:
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name='gamma_terminal_review_holds'"
        ).fetchone() is None


def _two_increment_terminal_parent():
    """20 filled over two real ledger increments, still leaving 30 inventory."""
    parent = terminal_partial()
    assert fill_ledger.record(
        account_id=ACCOUNT, uid=UID, symbol=SYMBOL, exchange="NFO",
        side="BUY", order_id="ENTRY-TRANCHE-1", cumulative_quantity=50,
        cumulative_value=5000.0, source="entry", lot_size=10,
    ).accepted
    assert fill_ledger.record(
        account_id=ACCOUNT, uid=UID, symbol=SYMBOL, exchange="NFO",
        side="SELL", order_id="EXIT-TRANCHE-1", cumulative_quantity=10,
        cumulative_value=900.0, source="exit", lot_size=10,
    ).accepted
    assert fill_ledger.record(
        account_id=ACCOUNT, uid=UID, symbol=SYMBOL, exchange="NFO",
        side="SELL", order_id="EXIT-TRANCHE-1", cumulative_quantity=20,
        cumulative_value=1760.0, source="exit", lot_size=10,
    ).accepted
    assert order_journal.mark_projected(parent.intent_key, parent.projection_version)
    return parent


def test_valid_two_increment_parent_is_reviewable_but_cannot_authorize(store):
    _two_increment_terminal_parent()
    result = review()
    assert result.ready_for_review and not result.execution_authorized
    claim = _hold()
    assert claim.held_for_review and not claim.execution_authorized
    with db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM kite_order_intents").fetchone()[0] == 1


@pytest.mark.parametrize("field,value", [
    ("signed_quantity", -1),
    ("price", 10.0),
    ("cumulative_value", 990.0),
])
def test_two_increment_parent_requires_every_consistent_delta(store, field, value):
    _two_increment_terminal_parent()
    with db._conn() as conn:
        conn.execute(
            f"UPDATE kite_execution_increments SET {field}=? "
            "WHERE account_id=? AND order_id=? AND cumulative_quantity=10",
            (value, ACCOUNT, "EXIT-TRANCHE-1"),
        )
    assert review().reason == "parent_signed_execution_disagrees"
    claim = _hold()
    assert not claim.held_for_review and not claim.execution_authorized


def test_same_transaction_rechecks_historical_signed_fill_after_preview(store, monkeypatch):
    from app.services import gamma_tranche_review_claims as claims

    _two_increment_terminal_parent()
    original_preview = claims.preview_terminal_child_identity

    def changed_during_preflight(**kwargs):
        result = original_preview(**kwargs)
        assert result.identifiable_for_review
        with db._conn() as conn:
            conn.execute(
                "UPDATE kite_execution_increments SET signed_quantity=-1 "
                "WHERE account_id=? AND order_id=? AND cumulative_quantity=10",
                (ACCOUNT, "EXIT-TRANCHE-1"),
            )
        return result

    monkeypatch.setattr(claims, "preview_terminal_child_identity", changed_during_preflight)
    outcome = _hold()
    assert outcome.reason == "signed_execution_changed_during_review"
    assert not outcome.held_for_review and not outcome.execution_authorized
    with db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM gamma_terminal_review_holds").fetchone()[0] == 0


def test_old_review_claim_replay_refuses_consistent_historical_fill_rewrite(store):
    """Correct final fill and replay version must not hide altered fill history."""
    _two_increment_terminal_parent()
    initial = _hold()
    assert initial.held_for_review
    with db._conn() as conn:
        # Rewrite both incremental prices and first cumulative amount so the
        # total remains 20/1760; the final parent and inventory revisions do
        # not change, yet the actual evidence sequence is different.
        conn.execute(
            "UPDATE kite_execution_increments SET cumulative_value=910, price=91 "
            "WHERE account_id=? AND order_id=? AND cumulative_quantity=10",
            (ACCOUNT, "EXIT-TRANCHE-1"),
        )
        conn.execute(
            "UPDATE kite_execution_increments SET price=85 "
            "WHERE account_id=? AND order_id=? AND cumulative_quantity=20",
            (ACCOUNT, "EXIT-TRANCHE-1"),
        )
    assert review().ready_for_review
    outcome = _hold()
    assert outcome.reason == "existing_review_claim_evidence_changed"
    assert not outcome.held_for_review and not outcome.execution_authorized
    with db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM gamma_terminal_review_holds").fetchone()[0] == 1


def test_signed_parent_increment_rejects_nonmonotone_broker_event_chronology(store):
    """Sorting by cumulative quantity must not hide inverted event history."""
    _two_increment_terminal_parent()
    with db._conn() as conn:
        conn.execute(
            "UPDATE kite_execution_increments SET received_ts_ms=received_ts_ms+900000 "
            "WHERE account_id=? AND order_id=? AND cumulative_quantity=10",
            (ACCOUNT, "EXIT-TRANCHE-1"),
        )
    from app.services.kite_engine import fill_ledger
    rows = [row for row in fill_ledger.increments(UID, account_id=ACCOUNT, symbol=SYMBOL)
            if row["order_id"] == "EXIT-TRANCHE-1"]
    assert [row["cumulative_quantity"] for row in rows] == [20, 10]
    assert review().reason == "parent_signed_execution_disagrees"
    claim = _hold()
    assert not claim.held_for_review and not claim.execution_authorized


def test_ordered_increment_digest_changes_if_event_chronology_is_rewritten(store):
    """Same signed values in different event order cannot replay an old proof."""
    _two_increment_terminal_parent()
    first = _hold()
    assert first.held_for_review and not first.replay
    with db._conn() as conn:
        conn.execute(
            "UPDATE kite_execution_increments SET received_ts_ms=received_ts_ms+900000 "
            "WHERE account_id=? AND order_id=? AND cumulative_quantity=10",
            (ACCOUNT, "EXIT-TRANCHE-1"),
        )
    result = _hold()
    assert not result.held_for_review and not result.execution_authorized
    assert result.reason == "parent_signed_execution_disagrees"
    with db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM gamma_terminal_review_holds").fetchone()[0] == 1


def _offline_child(**overrides):
    from app.services.gamma_offline_canonical_child import reserve_offline_canonical_terminal_child
    return reserve_offline_canonical_terminal_child(**{**ID, **overrides})


def test_offline_canonical_child_is_durable_and_never_submittable(store):
    parent = prepared_terminal_lineage()
    result = _offline_child()
    assert result.reserved_for_review and not result.replay
    assert not result.execution_authorized and result.remaining_quantity == 30
    assert result.parent_intent_key == parent.intent_key
    child = order_journal.get(result.child_intent_key)
    assert child is not None
    assert child.state == 'RESERVED' and child.side == 'SELL' and child.quantity == 30
    assert child.payload['gamma_terminal_review_only'] is True
    assert child.payload['execution_authorized'] is False
    assert child.payload['parent_intent_key'] == parent.intent_key
    assert not child.order_id
    assert not order_journal.claim_submission(child.intent_key)
    assert order_journal.get(child.intent_key).state == 'RESERVED'
    assert order_journal.account_bucket_has_unresolved(account_id=ACCOUNT, exchange='NFO', symbol=SYMBOL)
    with db._conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM kite_order_intents').fetchone()[0] == 2
        assert conn.execute('SELECT COUNT(*) FROM gamma_terminal_review_holds').fetchone()[0] == 1


def test_offline_canonical_child_is_idempotent_across_process_initialization(store):
    prepared_terminal_lineage()
    first = _offline_child()
    assert first.reserved_for_review
    assert db.init()
    second = _offline_child()
    assert second.reserved_for_review and second.replay
    assert second.child_intent_key == first.child_intent_key
    assert not second.execution_authorized
    with db._conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM kite_order_intents').fetchone()[0] == 2


def test_offline_canonical_child_concurrent_threads_only_once(store):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    prepared_terminal_lineage()
    barrier = Barrier(2)
    def worker():
        barrier.wait(timeout=10)
        return _offline_child()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: worker(), (1, 2)))
    assert first.reserved_for_review and second.reserved_for_review
    assert sorted([first.replay, second.replay]) == [False, True]
    assert first.child_intent_key == second.child_intent_key
    assert not first.execution_authorized and not second.execution_authorized
    with db._conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM kite_order_intents').fetchone()[0] == 2


def test_offline_canonical_child_fails_closed_if_review_parent_proof_changes(store):
    prepared_terminal_lineage()
    first = _offline_child()
    assert first.reserved_for_review
    with db._conn() as conn:
        conn.execute('UPDATE kite_inventory SET version=version+1 WHERE account_id=? AND uid=? AND symbol=?',
                     (ACCOUNT, UID, SYMBOL))
    second = _offline_child()
    assert second.reason == 'inventory_proof_changed'
    assert not second.reserved_for_review and not second.execution_authorized
    assert not order_journal.claim_submission(first.child_intent_key)


def test_offline_canonical_child_cannot_transition_or_release_its_review_hold(store):
    prepared_terminal_lineage()
    first = _offline_child()
    assert first.reserved_for_review
    with pytest.raises(ValueError, match='gamma_offline_review_intent_immutable'):
        order_journal.transition(first.child_intent_key, 'CANCELLED')
    assert order_journal.get(first.child_intent_key).state == 'RESERVED'
    result = _offline_child()
    assert result.reserved_for_review and result.replay
    assert not result.execution_authorized
    with db._conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM kite_order_intents').fetchone()[0] == 2


@pytest.mark.parametrize('mutation', [
    'transition', 'acknowledge', 'uncertain', 'observe', 'mark_projected',
])
def test_offline_canonical_child_refuses_all_public_journal_mutations(store, mutation):
    """No direct journal API may convert review evidence into a broker order."""
    prepared_terminal_lineage()
    child_result = _offline_child()
    assert child_result.reserved_for_review
    key = child_result.child_intent_key
    calls = {
        'transition': lambda: order_journal.transition(key, 'CANCELLED'),
        'acknowledge': lambda: order_journal.acknowledge(key, 'FAKE-BROKER-ACK'),
        'uncertain': lambda: order_journal.submission_uncertain(key, 'fake-timeout'),
        'observe': lambda: order_journal.observe_order(
            key, status='COMPLETE', order_id='FAKE-BROKER-ACK',
            filled_quantity=30, average_price=88.0),
        'mark_projected': lambda: order_journal.mark_projected(key, 0),
    }
    with pytest.raises(ValueError, match='gamma_offline_review_intent_immutable'):
        calls[mutation]()
    child = order_journal.get(key)
    assert child.state == 'RESERVED' and not child.order_id
    assert not child.reconciliation_required and not child.projection_pending
    assert not order_journal.claim_submission(key)
    with db._conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM kite_order_observations WHERE intent_key=?',
                            (key,)).fetchone()[0] == 0
    replay = _offline_child()
    assert replay.reserved_for_review and replay.replay
    assert not replay.execution_authorized


def test_offline_canonical_child_after_review_checkpoint_recovers_without_resend(store):
    prepared_terminal_lineage()
    checkpoint = _hold()
    assert checkpoint.held_for_review
    assert order_journal.get(checkpoint.candidate_intent_key) is None
    result = _offline_child()
    assert result.reserved_for_review
    assert result.child_intent_key == checkpoint.candidate_intent_key
    assert not order_journal.claim_submission(result.child_intent_key)


def test_offline_canonical_child_rejects_other_user_competing_order_after_checkpoint(store):
    prepared_terminal_lineage()
    assert _hold().held_for_review
    order_journal.reserve(uid='other-owner', account_id=ACCOUNT,
                          strategy_id='other-strategy', generation_id='gen-2',
                          signal_id='other-sell', exchange='NFO', symbol=SYMBOL,
                          side='SELL', quantity=1)
    result = _offline_child()
    assert result.reason == 'account_bucket_order_conflict'
    assert not result.reserved_for_review and not result.execution_authorized
    with db._conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM kite_order_intents').fetchone()[0] == 2


@pytest.mark.parametrize('setting', ['1', 'true', 'yes'])
def test_offline_canonical_child_refuses_live_execution_process(store, monkeypatch, setting):
    prepared_terminal_lineage()
    monkeypatch.setenv('STERLING_LIVE_EXECUTION', setting)
    result = _offline_child()
    assert result.reason == 'live_execution_environment_refused'
    assert not result.reserved_for_review
    with db._conn() as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='gamma_terminal_review_holds'").fetchone() is None


def test_offline_canonical_child_refuses_user_scope_and_tampered_ledger(store):
    prepared_terminal_lineage()
    assert _offline_child(uid='other-user').reserved_for_review is False
    with db._conn() as conn:
        conn.execute('UPDATE kite_execution_increments SET signed_quantity=-1 '
                     'WHERE account_id=? AND order_id=? AND side=\'SELL\'',
                     (ACCOUNT, 'EXIT-TRANCHE-1'))
    result = _offline_child()
    assert not result.reserved_for_review
    assert result.reason in {'parent_signed_execution_disagrees', 'signed_fill_proof_changed'}


def test_offline_canonical_child_journal_send_refusal_survives_real_payload_replay(store):
    prepared_terminal_lineage()
    child_result = _offline_child()
    child = order_journal.get(child_result.child_intent_key)
    assert child
    replayed = order_journal.reserve(
        uid=UID, account_id=ACCOUNT, strategy_id='gamma-unwind',
        generation_id='arbitrary-new-generation', signal_id=child.signal_id,
        exchange='NFO', symbol=SYMBOL, side='SELL', quantity=30,
        payload=child.payload,
    )
    assert replayed.intent_key == child.intent_key
    assert not order_journal.claim_submission(replayed.intent_key)
    assert order_journal.get(replayed.intent_key).state == 'RESERVED'


def test_offline_child_two_processes_get_one_durable_canonical_record(store):
    import json
    import subprocess

    prepared_terminal_lineage()
    script = '''
import json, sys
from app.services import db
from app.services.gamma_offline_canonical_child import reserve_offline_canonical_terminal_child
db._DB_PATH = sys.argv[1]
assert db.init()
r = reserve_offline_canonical_terminal_child(
    uid="tranche-review-user", account_id="acct-tranche", strategy_id="gamma-unwind",
    signal_id="gamma:partial", exchange="NFO", symbol="SYNTHETIC26NOV25000CE",
    product="NRML",
)
print(json.dumps({"held": r.reserved_for_review, "replay": r.replay,
                  "authorized": r.execution_authorized, "key": r.child_intent_key,
                  "reason": r.reason}))
'''
    args, env, cwd = _child_review_command(script, db._DB_PATH)
    p1 = subprocess.Popen(args, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    p2 = subprocess.Popen(args, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out1, err1 = p1.communicate(timeout=30)
    out2, err2 = p2.communicate(timeout=30)
    assert p1.returncode == p2.returncode == 0, (err1[-500:], err2[-500:])
    results = [json.loads(out1.strip().splitlines()[-1]), json.loads(out2.strip().splitlines()[-1])]
    assert all(r['held'] and not r['authorized'] for r in results), results
    assert sorted(r['replay'] for r in results) == [False, True]
    assert results[0]['key'] == results[1]['key']
    assert not order_journal.claim_submission(results[0]['key'])
    with db._conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM kite_order_intents').fetchone()[0] == 2
        assert conn.execute('SELECT COUNT(*) FROM gamma_terminal_review_holds').fetchone()[0] == 1


def test_offline_child_crash_during_canonical_insert_rolls_back_and_replays_checkpoint(store):
    import subprocess

    prepared_terminal_lineage()
    saved_checkpoint = _hold()
    assert saved_checkpoint.held_for_review
    script = '''
import os,sys
from app.services import db
from app.services.gamma_offline_canonical_child import reserve_offline_canonical_terminal_child
db._DB_PATH=sys.argv[1]
assert db.init()
original=db._conn
from contextlib import contextmanager
@contextmanager
def crash_conn():
  with original() as conn:
    conn.create_function("crash_before_commit", 0, lambda: os._exit(79))
    conn.execute("""CREATE TEMP TRIGGER abort_offline_child AFTER INSERT ON kite_order_intents
      WHEN NEW.signal_id LIKE '%:terminal-child:%'
      BEGIN SELECT crash_before_commit(); END""")
    yield conn
db._conn=crash_conn
reserve_offline_canonical_terminal_child(
  uid="tranche-review-user",account_id="acct-tranche",strategy_id="gamma-unwind",
  signal_id="gamma:partial",exchange="NFO",symbol="SYNTHETIC26NOV25000CE",product="NRML")
'''
    args, env, cwd = _child_review_command(script, db._DB_PATH)
    crashed = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=30)
    assert crashed.returncode == 79, crashed.stderr[-500:]
    with db._conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM kite_order_intents').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM gamma_terminal_review_holds').fetchone()[0] == 1
    retry = _offline_child()
    assert retry.reserved_for_review and not retry.replay and not retry.execution_authorized
    assert retry.child_intent_key == saved_checkpoint.candidate_intent_key
    assert not order_journal.claim_submission(retry.child_intent_key)


def test_offline_child_cannot_be_claimed_after_setting_live_mode(store, monkeypatch):
    prepared_terminal_lineage()
    result = _offline_child()
    assert result.reserved_for_review
    monkeypatch.setenv('STERLING_LIVE_EXECUTION', 'true')
    assert not order_journal.claim_submission(result.child_intent_key)
    assert order_journal.get(result.child_intent_key).state == 'RESERVED'


def test_canonical_regular_reservation_cannot_convert_offline_child_into_live_order(store):
    prepared_terminal_lineage()
    result = _offline_child()
    child = order_journal.get(result.child_intent_key)
    assert child
    with pytest.raises(ValueError, match='immutable_intent_conflict'):
        order_journal.reserve(
            uid=UID, account_id=ACCOUNT, strategy_id='gamma-unwind',
            generation_id='new-live-generation', signal_id=child.signal_id,
            exchange='NFO', symbol=SYMBOL, side='SELL', quantity=30,
            payload={'intent_type': 'EXIT', 'product': 'NRML', 'reduction_kind': 'exit'},
        )
    assert not order_journal.claim_submission(child.intent_key)
    assert order_journal.get(child.intent_key).state == 'RESERVED'
