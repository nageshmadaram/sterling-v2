"""Read-only Gamma partial-exit lineage audit; never an order admission token.

This diagnostic intentionally has no broker write, reservation, risk approval,
or output that could authorize a fresh SELL. It identifies the *prerequisites*
for designing a separate durable remainder intent without weakening existing
UNKNOWN/immutability protections. A positive review is not broker truth.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isclose

from app.services import db
from app.services.kite_engine import fill_ledger, order_journal


@dataclass(frozen=True)
class PartialExitReadiness:
    ready_for_review: bool
    reason: str
    remaining_quantity: int = 0
    parent_intent_key: str = ""
    parent_order_id: str = ""
    # Deliberate invariant: this report can NEVER authorize a broker order.
    execution_authorized: bool = False


def inspect_terminal_partial_exit(
    *, uid: str, account_id: str, strategy_id: str, signal_id: str,
    exchange: str, symbol: str, product: str,
) -> PartialExitReadiness:
    """Cross-check immutable order, projection, and signed account ledger.

    Do not use this diagnostic as a trading gate: fresh broker inventory, open
    GTT/child orders, account fencing and atomic tranche reservation are *not*
    established here. Every database/read disagreement fails closed.
    """
    def refused(reason: str) -> PartialExitReadiness:
        return PartialExitReadiness(False, reason)

    if any(not isinstance(value, str) or not value or value.strip() != value
           for value in (uid, account_id, strategy_id, signal_id,
                         exchange, symbol, product)):
        return refused("invalid_review_identity")
    if (exchange != exchange.upper() or symbol != symbol.upper()
            or product != product.upper()):
        return refused("noncanonical_review_identity")

    try:
        key = order_journal.make_key(
            uid=uid, account_id=account_id, strategy_id=strategy_id,
            generation_id="read-only", signal_id=f"{signal_id}:reduce:exit",
            exchange=exchange, symbol=symbol, side="SELL",
        )
        intent = order_journal.get(key)
        if intent is None:
            return refused("parent_intent_missing")
        if (intent.side != "SELL" or intent.uid != uid
                or intent.account_id != account_id
                or intent.strategy_id != strategy_id
                or intent.signal_id != f"{signal_id}:reduce:exit"
                or intent.exchange != exchange or intent.symbol != symbol
                or intent.payload.get("product") != product
                or intent.payload.get("reduction_kind") != "exit"
                or intent.payload.get("intent_type") != "EXIT"):
            return refused("parent_identity_or_product_mismatch")
        if (intent.state != "CANCELLED" or not intent.order_id
                or not intent.tag.startswith("ke")
                or type(intent.quantity) is not int
                or not 0 < intent.filled_quantity < intent.quantity
                or intent.filled_value <= 0):
            return refused("parent_not_terminal_partial")
        if (intent.reconciliation_required or intent.projection_pending
                or intent.projection_version <= 0):
            return refused("parent_reconciliation_or_projection_pending")
        # Another unresolved order for this very broker bucket could fill
        # between inspections. Never infer available inventory from the parent.
        if any(item.exchange == exchange and item.symbol == symbol
               for item in order_journal.unresolved(uid, account_id)):
            return refused("other_bucket_intent_unresolved")
        remainder = intent.quantity - intent.filled_quantity
        inv = db.get_inventory_strict(account_id, uid, symbol)
        if (inv is None or inv.get("account_id") != account_id
                or inv.get("uid") != uid or inv.get("symbol") != symbol
                or inv.get("exchange") != exchange
                or inv.get("reconciliation_required")
                or type(inv.get("net_quantity")) is not int
                or inv["net_quantity"] != remainder
                or type(inv.get("version")) is not int
                or inv["version"] <= 0):
            return refused("account_inventory_not_projected_exactly")
        executions = fill_ledger.increments(
            uid, account_id=account_id, symbol=symbol,
        )
        parent_rows = [row for row in executions
                       if row.get("order_id") == intent.order_id]
        if (not parent_rows
                or any(row.get("exchange") != exchange
                       or row.get("side") != "SELL"
                       or row.get("source") not in {"exit", "recovery", "reconciliation"}
                       for row in parent_rows)):
            return refused("parent_signed_execution_missing")
        final_row = max(parent_rows, key=lambda row: int(row["cumulative_quantity"]))
        if (int(final_row["cumulative_quantity"]) != intent.filled_quantity
                or not isclose(float(final_row["cumulative_value"]),
                               intent.filled_value, rel_tol=1e-9, abs_tol=1e-6)
                or int(final_row["signed_quantity"]) >= 0):
            return refused("parent_signed_execution_disagrees")
        return PartialExitReadiness(
            True, "lineage_evidence_consistent_review_only", remainder,
            intent.intent_key, intent.order_id,
        )
    except Exception:
        # Do not leak broker/account/database internals into a readout that
        # might be exposed in a review log or administrator UI later.
        return refused("partial_lineage_evidence_unavailable")
