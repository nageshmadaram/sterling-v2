"""Read-only Gamma partial-exit lineage audit; never an order admission token.

This diagnostic intentionally has no broker write, reservation, risk approval,
or output that could authorize a fresh SELL. It identifies the *prerequisites*
for designing a separate durable remainder intent without weakening existing
UNKNOWN/immutability protections. A positive review is not broker truth.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json

from app.services import db
from app.services.kite_engine import fill_ledger, order_journal
from app.services.gamma_signed_increment_provenance import valid_parent_sell_increments


@dataclass(frozen=True)
class PartialExitReadiness:
    ready_for_review: bool
    reason: str
    remaining_quantity: int = 0
    parent_intent_key: str = ""
    parent_order_id: str = ""
    parent_quantity: int = 0
    parent_filled_quantity: int = 0
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
        # Account inventory is shared across users. A pending intent from any
        # user under this same authenticated account and contract may settle
        # into the bucket; inspecting only this user's journal would undercount.
        if order_journal.account_bucket_has_unresolved(
            account_id=account_id, exchange=exchange, symbol=symbol,
        ):
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
        # Another scoped user may already own the same broker account/contract.
        # The user-local net balance cannot prove that the shared broker bucket
        # has no other exposure. This is a conservative offline review fence.
        with db._conn() as conn:
            other_owner = conn.execute(
                "SELECT 1 FROM kite_inventory WHERE account_id=? AND symbol=? "
                "AND uid<>? AND (net_quantity<>0 OR reconciliation_required=1) "
                "LIMIT 1", (account_id, symbol, uid),
            ).fetchone()
        if other_owner is not None:
            return refused("other_owner_inventory_present")
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
        if not valid_parent_sell_increments(
            parent_rows, account_id=account_id, uid=uid, symbol=symbol,
            exchange=exchange, order_id=intent.order_id,
            cumulative_quantity=intent.filled_quantity,
            cumulative_value=intent.filled_value,
        ):
            return refused("parent_signed_execution_disagrees")
        return PartialExitReadiness(
            True, "lineage_evidence_consistent_review_only", remainder,
            intent.intent_key, intent.order_id,
            intent.quantity, intent.filled_quantity,
        )
    except Exception:
        # Do not leak broker/account/database internals into a readout that
        # might be exposed in a review log or administrator UI later.
        return refused("partial_lineage_evidence_unavailable")


@dataclass(frozen=True)
class ChildTranchePreview:
    """Stable *proposed* lineage only; no persistent reservation/authorization."""
    identifiable_for_review: bool
    reason: str
    child_signal_id: str = ""
    candidate_intent_key: str = ""
    remaining_quantity: int = 0
    parent_intent_key: str = ""
    parent_order_id: str = ""
    parent_quantity: int = 0
    parent_filled_quantity: int = 0
    execution_authorized: bool = False


def preview_terminal_child_identity(
    *, uid: str, account_id: str, strategy_id: str, signal_id: str,
    exchange: str, symbol: str, product: str,
) -> ChildTranchePreview:
    """Compute an idempotent child *candidate*, without reserving or sending.

    The digest deliberately excludes timestamps, scheduler generation, reasons,
    and inventory versions: a restart, benign recheck or different UI reason
    must NOT mint a fresh SELL identity. Parent signed cumulative fill and
    order identity are immutable terminal evidence after projection.

    This is not a transactional checkpoint. It cannot authorize orders even if
    it returns a candidate key; further authenticated broker inventory, GTT
    lifecycle, concurrent reservation and independent review are required.
    """
    def refused(reason: str) -> ChildTranchePreview:
        return ChildTranchePreview(False, reason)

    audit = inspect_terminal_partial_exit(
        uid=uid, account_id=account_id, strategy_id=strategy_id,
        signal_id=signal_id, exchange=exchange, symbol=symbol, product=product,
    )
    if not audit.ready_for_review:
        return refused(audit.reason)
    if (not audit.parent_intent_key or not audit.parent_order_id
            or audit.remaining_quantity <= 0
            or audit.parent_quantity <= audit.parent_filled_quantity
            or audit.parent_filled_quantity <= 0):
        return refused("invalid_parent_tranche_evidence")
    try:
        payload = [
            "gamma-terminal-child-v1", uid, account_id, strategy_id,
            signal_id, exchange, symbol, product, audit.parent_intent_key,
            audit.parent_order_id, audit.parent_quantity,
            audit.parent_filled_quantity, audit.remaining_quantity,
        ]
        digest = sha256(json.dumps(payload, sort_keys=False,
                                   separators=(",", ":"),
                                   ensure_ascii=True).encode("utf-8")).hexdigest()
        child_signal_id = f"{signal_id}:terminal-child:v1:{digest[:32]}"
        journal_signal = f"{child_signal_id}:reduce:exit"
        candidate_key = order_journal.make_key(
            uid=uid, account_id=account_id, strategy_id=strategy_id,
            generation_id="review-only", signal_id=journal_signal,
            exchange=exchange, symbol=symbol, side="SELL",
        )
        if order_journal.identity_exists(
            uid=uid, account_id=account_id, strategy_id=strategy_id,
            signal_id=journal_signal, exchange=exchange, symbol=symbol,
            side="SELL",
        ):
            return refused("child_identity_already_recorded")
        # Read again to narrow accidental stale-positive diagnostics. This is
        # still not atomic and can NEVER be used as a pre-disarm order gate.
        if order_journal.account_bucket_has_unresolved(
            account_id=account_id, exchange=exchange, symbol=symbol,
        ):
            return refused("other_bucket_intent_unresolved")
        return ChildTranchePreview(
            True, "stable_child_identity_preview_only", child_signal_id,
            candidate_key, audit.remaining_quantity, audit.parent_intent_key,
            audit.parent_order_id, audit.parent_quantity, audit.parent_filled_quantity,
        )
    except Exception:
        return refused("child_identity_evidence_unavailable")
