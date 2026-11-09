"""OFFLINE-ONLY atomic review hold for Gamma terminal-partial child lineage.

Never a canonical order intent. There is deliberately no broker, live mode,
HTTP route, journal claim_submission, automatic retry, or claim release path.
Production execution must NOT consume a ReviewHoldResult as an admission token.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
import time
from math import isclose

from app.services import db
from app.services.gamma_tranche_readiness import preview_terminal_child_identity
from app.services.kite_engine import order_journal


@dataclass(frozen=True)
class ReviewHoldResult:
    held_for_review: bool
    reason: str
    candidate_intent_key: str = ""
    parent_intent_key: str = ""
    remaining_quantity: int = 0
    replay: bool = False
    # Not a broker-order reservation, execution approval, or operator consent.
    execution_authorized: bool = False


def _schema(conn: sqlite3.Connection) -> None:
    # Use an isolated, disposable test DB only. Durable within that DB, with a
    # single irreversible review hold per account/contract. No production DB
    # migrations until an independently approved implementation exists.
    conn.execute("""CREATE TABLE IF NOT EXISTS gamma_terminal_review_holds (
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
        parent_filled_quantity INTEGER NOT NULL CHECK(parent_filled_quantity > 0),
        remaining_quantity INTEGER NOT NULL CHECK(remaining_quantity > 0),
        inventory_version INTEGER NOT NULL CHECK(inventory_version > 0),
        status TEXT NOT NULL DEFAULT 'REVIEW_HELD'
            CHECK(status = 'REVIEW_HELD'),
        created_ms INTEGER NOT NULL,
        UNIQUE(account_id, exchange, symbol)
    )""")


def hold_terminal_child_review(
    *, uid: str, account_id: str, strategy_id: str, signal_id: str,
    exchange: str, symbol: str, product: str,
) -> ReviewHoldResult:
    """Persist a *non-executing* immutable claim after same-txn evidence checks.

    Preflight is advisory. Every relevant SQL assertion is repeated with the
    SQLite account-wide writer lock held, preventing an intervening local
    journal/fill/inventory writer from bypassing the review snapshot. This
    cannot serialize external brokers and is NEVER a live execution gate.
    """
    def stop(reason: str) -> ReviewHoldResult:
        return ReviewHoldResult(False, reason)

    # Intentional hard kill-switch, independent of the global broker switch.
    # No invocation may alter an operator's persistent/live database.
    if not (db.under_test() and db.is_disposable_path(db._DB_PATH)):
        return stop("offline_disposable_test_database_required")
    if not db.is_available():
        return stop("review_storage_unavailable")

    candidate = preview_terminal_child_identity(
        uid=uid, account_id=account_id, strategy_id=strategy_id,
        signal_id=signal_id, exchange=exchange, symbol=symbol,
        product=product,
    )
    if not candidate.identifiable_for_review:
        return stop(candidate.reason)
    try:
        with db._conn() as conn:
            conn.execute("PRAGMA synchronous=FULL")
            if conn.execute("PRAGMA synchronous").fetchone()[0] != 2:
                raise RuntimeError("full_synchronous_durability_not_supported")
            conn.execute("BEGIN IMMEDIATE")
            _schema(conn)
            parent = conn.execute(
                "SELECT * FROM kite_order_intents WHERE intent_key=?",
                (candidate.parent_intent_key,),
            ).fetchone()
            if parent is None:
                return stop("parent_changed_during_review")
            parent_payload = json.loads(parent["payload_json"] or "{}")
            if (parent["uid"] != uid or parent["account_id"] != account_id
                    or parent["strategy_id"] != strategy_id
                    or parent["signal_id"] != f"{signal_id}:reduce:exit"
                    or parent["exchange"] != exchange
                    or parent["symbol"] != symbol or parent["side"] != "SELL"
                    or parent["state"] != "CANCELLED"
                    or not str(parent["order_id"] or "")
                    or parent["order_id"] != candidate.parent_order_id
                    or int(parent["quantity"]) != candidate.parent_quantity
                    or int(parent["observed_quantity"]) != candidate.parent_filled_quantity
                    or parent_payload.get("product") != product
                    or parent_payload.get("reduction_kind") != "exit"
                    or parent_payload.get("intent_type") != "EXIT"
                    or int(parent["reconciliation_required"]) != 0
                    or int(parent["projection_pending"]) != 0
                    or int(parent["projection_version"]) <= 0):
                return stop("parent_changed_during_review")
            original = int(parent["quantity"])
            filled = int(parent["observed_quantity"])
            remaining = original - filled
            if not 0 < filled < original or remaining != candidate.remaining_quantity:
                return stop("parent_quantity_changed_during_review")
            # Reject another user's unresolved order as well as our own, and
            # terminal-but-unprojected rows. No new canonical child can exist.
            marks = ",".join("?" for _ in order_journal.UNRESOLVED)
            if conn.execute(
                "SELECT 1 FROM kite_order_intents WHERE account_id=? AND exchange=? AND symbol=? "
                f"AND (state IN ({marks}) OR reconciliation_required=1 "
                "OR projection_pending=1) LIMIT 1",
                (account_id, exchange, symbol, *sorted(order_journal.UNRESOLVED)),
            ).fetchone():
                return stop("account_bucket_changed_during_review")
            if conn.execute(
                "SELECT 1 FROM kite_order_intents WHERE uid=? AND account_id=? "
                "AND strategy_id=? AND signal_id=? AND exchange=? "
                "AND symbol=? AND side='SELL' LIMIT 1",
                (uid, account_id, strategy_id,
                 f"{candidate.child_signal_id}:reduce:exit", exchange, symbol),
            ).fetchone():
                return stop("canonical_child_intent_already_recorded")
            inv = conn.execute(
                "SELECT * FROM kite_inventory WHERE account_id=? AND uid=? AND symbol=?",
                (account_id, uid, symbol),
            ).fetchone()
            if (inv is None or inv["exchange"] != exchange
                    or int(inv["reconciliation_required"]) != 0
                    or int(inv["version"]) <= 0
                    or int(inv["net_quantity"]) != remaining):
                return stop("inventory_changed_during_review")
            if conn.execute(
                "SELECT 1 FROM kite_inventory WHERE account_id=? AND symbol=? "
                "AND uid<>? AND (net_quantity<>0 OR reconciliation_required=1) "
                "LIMIT 1", (account_id, symbol, uid),
            ).fetchone():
                return stop("other_owner_inventory_changed_during_review")
            executions = conn.execute(
                "SELECT * FROM kite_execution_increments WHERE account_id=? "
                "AND uid=? AND symbol=? AND order_id=? "
                "ORDER BY cumulative_quantity DESC",
                (account_id, uid, symbol, parent["order_id"]),
            ).fetchall()
            if (not executions
                    or any(r["exchange"] != exchange or r["side"] != "SELL"
                           or r["source"] not in {"exit", "recovery", "reconciliation"}
                           or int(r["signed_quantity"]) >= 0 for r in executions)
                    or int(executions[0]["cumulative_quantity"]) != filled
                    or not isclose(float(executions[0]["cumulative_value"]),
                                   float(parent["observed_value"]),
                                   rel_tol=1e-9, abs_tol=1e-6)):
                return stop("signed_execution_changed_during_review")
            prior = conn.execute(
                "SELECT * FROM gamma_terminal_review_holds WHERE candidate_intent_key=?",
                (candidate.candidate_intent_key,),
            ).fetchone()
            if prior is not None:
                if (prior["parent_intent_key"] != candidate.parent_intent_key
                        or prior["uid"] != uid or prior["account_id"] != account_id
                        or prior["strategy_id"] != strategy_id
                        or prior["signal_id"] != signal_id or prior["exchange"] != exchange
                        or prior["symbol"] != symbol or prior["product"] != product
                        or prior["parent_order_id"] != parent["order_id"]
                        or int(prior["parent_filled_quantity"]) != filled
                        or int(prior["remaining_quantity"]) != remaining):
                    return stop("existing_review_claim_conflicts")
                return ReviewHoldResult(True, "review_hold_replayed", candidate.candidate_intent_key,
                                        candidate.parent_intent_key, remaining, replay=True)
            if conn.execute(
                "SELECT 1 FROM gamma_terminal_review_holds WHERE account_id=? "
                "AND exchange=? AND symbol=? LIMIT 1",
                (account_id, exchange, symbol),
            ).fetchone():
                return stop("account_bucket_review_held")
            conn.execute(
                "INSERT INTO gamma_terminal_review_holds "
                "(candidate_intent_key,parent_intent_key,uid,account_id,strategy_id,"
                "signal_id,exchange,symbol,product,parent_order_id,parent_filled_quantity,"
                "remaining_quantity,inventory_version,created_ms) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (candidate.candidate_intent_key, candidate.parent_intent_key,
                 uid, account_id, strategy_id, signal_id, exchange, symbol, product,
                 parent["order_id"], filled, remaining, int(inv["version"]),
                 int(time.time() * 1000)),
            )
            return ReviewHoldResult(True, "review_held_offline_only", candidate.candidate_intent_key,
                                    candidate.parent_intent_key, remaining)
    except Exception:
        # Retain the current state and do not manufacture a replacement key.
        return stop("review_claim_storage_or_consistency_unavailable")
