"""Durable *offline-only*, non-submittable Gamma terminal child intent prototype.

This is an engineering acceptance fixture, NOT a live parent-child order path.
It writes a canonical RESERVED child only in an isolated disposable SQLite
TEST database. The canonical submission CAS explicitly rejects its immutable
review-only payload, even on a direct call. No Kite/GTT/network API is used.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
import time

from app.services import db
from app.services.gamma_signed_increment_provenance import (
    valid_parent_sell_increments, parent_sell_increment_fingerprint,
)
from app.services.gamma_tranche_review_claims import (
    _strictly_disposable_review_database, hold_terminal_child_review,
)
from app.services.kite_engine import order_journal


@dataclass(frozen=True)
class OfflineChildReservation:
    reserved_for_review: bool
    reason: str
    child_intent_key: str = ""
    parent_intent_key: str = ""
    remaining_quantity: int = 0
    replay: bool = False
    execution_authorized: bool = False


def _identity(*, uid: str, account_id: str, strategy_id: str, signal_id: str,
              exchange: str, symbol: str, product: str, parent_key: str,
              parent_order_id: str, requested: int, filled: int) -> tuple[str, str]:
    remaining = requested - filled
    items = ["gamma-terminal-child-v1", uid, account_id, strategy_id,
             signal_id, exchange, symbol, product, parent_key,
             parent_order_id, requested, filled, remaining]
    digest = sha256(json.dumps(items, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    child_signal_id = f"{signal_id}:terminal-child:v1:{digest[:32]}"
    journal_signal = f"{child_signal_id}:reduce:exit"
    key = order_journal.make_key(
        uid=uid, account_id=account_id, strategy_id=strategy_id,
        generation_id="offline-only", signal_id=journal_signal,
        exchange=exchange, symbol=symbol, side="SELL",
    )
    return journal_signal, key


def reserve_offline_canonical_terminal_child(
    *, uid: str, account_id: str, strategy_id: str, signal_id: str,
    exchange: str, symbol: str, product: str,
) -> OfflineChildReservation:
    """Stage a canonical review-only child using account-wide SQLite fencing.

    This deliberately does not consult or return executable trade permission.
    A prior REVIEW_HELD record is mandatory. If it was committed before a
    simulated crash, a retry can proceed; if the child exists, exact replay
    is allowed only with unchanged signed parent evidence and journal state.
    """
    def refuse(reason: str) -> OfflineChildReservation:
        return OfflineChildReservation(False, reason)

    if not (db.under_test() and db.is_disposable_path(db._DB_PATH)
            and _strictly_disposable_review_database(db._DB_PATH)):
        return refuse("offline_disposable_test_database_required")
    if str(os.environ.get("STERLING_LIVE_EXECUTION", "0")).strip().lower() not in {
        "", "0", "false", "off"
    }:
        return refuse("live_execution_environment_refused")
    if not db.is_available():
        return refuse("offline_journal_unavailable")
    for val in (uid, account_id, strategy_id, signal_id, exchange, symbol, product):
        if type(val) is not str or not val or val != val.strip():
            return refuse("invalid_child_identity")
    if exchange != exchange.upper() or symbol != symbol.upper() or product != product.upper():
        return refuse("noncanonical_child_identity")

    # An irreversible review proof can be committed without a child if the
    # process dies. It must not be re-minted, renewed or rewritten on restart.
    try:
        with db._conn() as conn:
            already_held = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='gamma_terminal_review_holds'",
            ).fetchone() and conn.execute(
                "SELECT 1 FROM gamma_terminal_review_holds WHERE account_id=? "
                "AND exchange=? AND symbol=?",
                (account_id, exchange, symbol),
            ).fetchone()
        if not already_held:
            proof = hold_terminal_child_review(
                uid=uid, account_id=account_id, strategy_id=strategy_id,
                signal_id=signal_id, exchange=exchange, symbol=symbol,
                product=product,
            )
            if not proof.held_for_review:
                # Concurrent creation can finish *between* our initial
                # no-hold check and the checkpoint operation. Do not convert
                # that benign race into a new identity or bypass verification:
                # only proceed when the durable checkpoint now exists, then
                # revalidate all owner/evidence fields under BEGIN IMMEDIATE.
                with db._conn() as probe:
                    concurrent_hold = probe.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='gamma_terminal_review_holds'",
                    ).fetchone() and probe.execute(
                        "SELECT 1 FROM gamma_terminal_review_holds WHERE "
                        "account_id=? AND exchange=? AND symbol=?",
                        (account_id, exchange, symbol),
                    ).fetchone()
                if not concurrent_hold:
                    return refuse(proof.reason)

        with db._conn() as conn:
            conn.execute("PRAGMA synchronous=FULL")
            if conn.execute("PRAGMA synchronous").fetchone()[0] != 2:
                return refuse("full_sync_unavailable")
            conn.execute("BEGIN IMMEDIATE")
            try:
                order_journal._require_no_simulation_reset(conn, uid)
            except ValueError as exc:
                if str(exc) == "simulation_reset_fenced":
                    return refuse("simulation_reset_fenced")
                raise
            hold = conn.execute(
                "SELECT * FROM gamma_terminal_review_holds WHERE account_id=? "
                "AND exchange=? AND symbol=?",
                (account_id, exchange, symbol),
            ).fetchone()
            if hold is None:
                return refuse("review_hold_missing")
            if (hold["uid"] != uid or hold["account_id"] != account_id
                    or hold["strategy_id"] != strategy_id or hold["signal_id"] != signal_id
                    or hold["exchange"] != exchange or hold["symbol"] != symbol
                    or hold["product"] != product or hold["status"] != "REVIEW_HELD"):
                return refuse("review_hold_scope_conflict")

            parent = conn.execute(
                "SELECT * FROM kite_order_intents WHERE intent_key=?",
                (hold["parent_intent_key"],),
            ).fetchone()
            if parent is None:
                return refuse("parent_intent_missing")
            payload = json.loads(parent["payload_json"] or "{}")
            if (parent["uid"] != uid or parent["account_id"] != account_id
                    or parent["strategy_id"] != strategy_id
                    or parent["signal_id"] != f"{signal_id}:reduce:exit"
                    or parent["exchange"] != exchange or parent["symbol"] != symbol
                    or parent["side"] != "SELL" or parent["state"] != "CANCELLED"
                    or not parent["order_id"] or parent["order_id"] != hold["parent_order_id"]
                    or int(parent["projection_pending"]) != 0
                    or int(parent["reconciliation_required"]) != 0
                    or int(parent["projection_version"]) != hold["parent_projection_version"]
                    or float(parent["observed_value"]) != hold["parent_observed_value"]
                    or payload.get("product") != product
                    or payload.get("intent_type") != "EXIT"
                    or payload.get("reduction_kind") != "exit"):
                return refuse("parent_proof_changed")
            requested, filled = int(parent["quantity"]), int(parent["observed_quantity"])
            remaining = requested - filled
            if (not 0 < filled < requested or filled != hold["parent_filled_quantity"]
                    or remaining != hold["remaining_quantity"]):
                return refuse("parent_quantity_changed")
            journal_signal, child_key = _identity(
                uid=uid, account_id=account_id, strategy_id=strategy_id,
                signal_id=signal_id, exchange=exchange, symbol=symbol,
                product=product, parent_key=hold["parent_intent_key"],
                parent_order_id=parent["order_id"], requested=requested,
                filled=filled,
            )
            if child_key != hold["candidate_intent_key"]:
                return refuse("child_identity_drift")
            inventory = conn.execute(
                "SELECT * FROM kite_inventory WHERE account_id=? AND uid=? AND symbol=?",
                (account_id, uid, symbol),
            ).fetchone()
            if (inventory is None or inventory["exchange"] != exchange
                    or int(inventory["reconciliation_required"]) != 0
                    or int(inventory["version"]) != hold["inventory_version"]
                    or int(inventory["net_quantity"]) != remaining):
                return refuse("inventory_proof_changed")
            if conn.execute(
                "SELECT 1 FROM kite_inventory WHERE account_id=? AND symbol=? "
                "AND uid<>? AND (net_quantity<>0 OR reconciliation_required=1) LIMIT 1",
                (account_id, symbol, uid),
            ).fetchone():
                return refuse("other_user_exposure")
            fills = conn.execute(
                "SELECT * FROM kite_execution_increments WHERE account_id=? "
                "AND uid=? AND symbol=? AND order_id=? "
                "ORDER BY COALESCE(NULLIF(exchange_ts_ms,0),received_ts_ms), "
                "received_ts_ms,id",
                (account_id, uid, symbol, parent["order_id"]),
            ).fetchall()
            if (not valid_parent_sell_increments(
                    fills, account_id=account_id, uid=uid, symbol=symbol,
                    exchange=exchange, order_id=parent["order_id"],
                    cumulative_quantity=filled, cumulative_value=parent["observed_value"])
                    or parent_sell_increment_fingerprint(fills) != hold["parent_increment_digest"]):
                return refuse("signed_fill_proof_changed")

            existing = conn.execute(
                "SELECT * FROM kite_order_intents WHERE intent_key=?", (child_key,),
            ).fetchone()
            if existing is not None:
                child_payload = json.loads(existing["payload_json"] or "{}")
                if (existing["uid"] != uid or existing["account_id"] != account_id
                        or existing["strategy_id"] != strategy_id
                        or existing["signal_id"] != journal_signal
                        or existing["exchange"] != exchange or existing["symbol"] != symbol
                        or existing["side"] != "SELL" or existing["quantity"] != remaining
                        or existing["state"] != "RESERVED" or existing["order_id"]
                        or child_payload.get("gamma_terminal_review_only") is not True
                        or child_payload.get("parent_intent_key") != hold["parent_intent_key"]
                        or child_payload.get("parent_increment_digest") != hold["parent_increment_digest"]):
                    return refuse("immutable_child_identity_conflict")
                replay = True
            else:
                # The same canonical identity may exist under a legacy key.
                if conn.execute(
                    "SELECT 1 FROM kite_order_intents WHERE uid=? AND account_id=? "
                    "AND strategy_id=? AND signal_id=? AND exchange=? AND symbol=? "
                    "AND side='SELL' LIMIT 1",
                    (uid, account_id, strategy_id, journal_signal, exchange, symbol),
                ).fetchone():
                    return refuse("legacy_child_identity_conflict")
                replay = False

            marks = ",".join("?" for _ in order_journal.UNRESOLVED)
            if conn.execute(
                "SELECT 1 FROM kite_order_intents WHERE account_id=? AND exchange=? "
                "AND symbol=? AND intent_key<>? "
                f"AND (state IN ({marks}) OR reconciliation_required=1 "
                "OR projection_pending=1) LIMIT 1",
                (account_id, exchange, symbol, child_key, *sorted(order_journal.UNRESOLVED)),
            ).fetchone():
                return refuse("account_bucket_order_conflict")
            if replay:
                return OfflineChildReservation(
                    True, "offline_child_replayed_no_execution", child_key,
                    hold["parent_intent_key"], remaining, replay=True,
                )
            child_payload = {
                "intent_type": "EXIT", "reduction_kind": "exit", "product": product,
                "gamma_terminal_review_only": True, "execution_authorized": False,
                "parent_intent_key": hold["parent_intent_key"],
                "parent_order_id": parent["order_id"],
                "parent_increment_digest": hold["parent_increment_digest"],
            }
            now = int(time.time() * 1000)
            conn.execute(
                "INSERT INTO kite_order_intents "
                "(intent_key,uid,account_id,strategy_id,generation_id,signal_id,exchange,symbol,"
                "side,quantity,tag,state,payload_json,capital_required,created_ms,updated_ms) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (child_key, uid, account_id, strategy_id, "offline-review-only",
                 journal_signal, exchange, symbol, "SELL", remaining,
                 "ke" + child_key[:18], "RESERVED", json.dumps(child_payload,sort_keys=True),
                 0, now, now),
            )
            return OfflineChildReservation(
                True, "offline_child_reserved_no_execution", child_key,
                hold["parent_intent_key"], remaining,
            )
    except Exception:
        return refuse("offline_child_transaction_or_evidence_unavailable")
