"""Pure broker-order reconciliation facts for the future EMA cash-equity live path.

This module does NOT send broker orders, update positions, or grant live-entry
permission. The canonical Kite order journal and signed fill ledger remain
authoritative. In particular, an ACK, GTT trigger or order status is not a fill.
Consumers must persist broker-observed cumulative facts before projecting any
incremental fill and must resolve unknown submissions by reading broker state.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

TERMINAL_ORDER_STATUSES = frozenset({"COMPLETE", "CANCELLED", "CANCELED", "REJECTED"})
RECONCILIATION_STATES = frozenset({"UNKNOWN", "SUBMITTING"})


class LiveOrderEvidenceError(ValueError):
    """The broker did not prove one exact, monotone order/fill identity."""


def _positive_quantity(value: Any, name: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool):
        raise LiveOrderEvidenceError(f"{name}_invalid")
    try:
        n = Decimal(str(value))
        if not n.is_finite() or n != int(n) or n < (0 if allow_zero else 1):
            raise ValueError()
        return int(n)
    except (TypeError, ValueError, InvalidOperation, OverflowError) as exc:
        raise LiveOrderEvidenceError(f"{name}_invalid") from exc


def _money(value: Any, name: str, *, allow_zero: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise LiveOrderEvidenceError(f"{name}_invalid")
    try:
        n = Decimal(str(value))
        if not n.is_finite() or n < 0 or (n == 0 and not allow_zero):
            raise ValueError()
        return n
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise LiveOrderEvidenceError(f"{name}_invalid") from exc


def _id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise LiveOrderEvidenceError(f"{name}_invalid")
    return value


@dataclass(frozen=True)
class LiveOrderIdentity:
    uid: str
    account_id: str
    order_id: str
    exchange: str
    symbol: str
    product: str
    side: str
    quantity: int

    def __post_init__(self) -> None:
        for name in ("uid", "account_id", "order_id", "exchange", "symbol", "product", "side"):
            _id(getattr(self, name), name)
        if self.exchange not in {"NSE", "BSE"}:
            raise LiveOrderEvidenceError("cash_equity_exchange_required")
        if self.product not in {"CNC", "MIS"} or self.side not in {"BUY", "SELL"}:
            raise LiveOrderEvidenceError("cash_equity_product_or_side_invalid")
        _positive_quantity(self.quantity, "quantity")


@dataclass(frozen=True)
class LiveOrderFact:
    identity: LiveOrderIdentity
    status: str
    filled_quantity: int
    cumulative_value: Decimal

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_ORDER_STATUSES

    @property
    def unresolved(self) -> bool:
        return not self.terminal


@dataclass(frozen=True)
class LiveFillDelta:
    identity: LiveOrderIdentity
    quantity: int
    value: Decimal

    @property
    def average_price(self) -> Decimal:
        if self.quantity <= 0:
            raise LiveOrderEvidenceError("no_new_confirmed_fill")
        return self.value / self.quantity


def broker_order_fact(identity: LiveOrderIdentity, history: Sequence[Mapping[str, Any]],
                      *, broker_account_id: str, broker_is_paper: bool | None) -> LiveOrderFact:
    """Validate all history rows before trusting the latest cumulative fill.

    An accepted order with zero filled shares is NOT treated as a holding.
    A CANCELLED order may still carry a nonzero partial fill.
    """
    if broker_is_paper is not False or broker_account_id != identity.account_id:
        raise LiveOrderEvidenceError("live_broker_account_binding_unverified")
    if not isinstance(history, (list, tuple)) or not history:
        raise LiveOrderEvidenceError("order_history_unavailable")
    prior_filled = 0
    prior_value = Decimal(0)
    latest: LiveOrderFact | None = None
    for row in history:
        if not isinstance(row, Mapping):
            raise LiveOrderEvidenceError("order_history_row_invalid")
        expected = {
            "order_id": identity.order_id,
            "exchange": identity.exchange,
            "tradingsymbol": identity.symbol,
            "product": identity.product,
            "transaction_type": identity.side,
        }
        if any(str(row.get(field) or "").strip().upper() != value.upper()
               for field, value in expected.items()):
            raise LiveOrderEvidenceError("broker_order_identity_conflict")
        requested = _positive_quantity(row.get("quantity"), "broker_order_quantity")
        if requested != identity.quantity:
            raise LiveOrderEvidenceError("broker_order_quantity_conflict")
        filled = _positive_quantity(row.get("filled_quantity"), "filled_quantity", allow_zero=True)
        if filled > identity.quantity or filled < prior_filled:
            raise LiveOrderEvidenceError("broker_filled_quantity_conflict")
        average = _money(row.get("average_price", 0), "average_price", allow_zero=filled == 0)
        value = average * filled
        if filled == 0:
            value = Decimal(0)
        if value < prior_value or (filled == prior_filled and value != prior_value):
            raise LiveOrderEvidenceError("broker_cumulative_fill_value_conflict")
        status = _id(row.get("status"), "broker_order_status").upper()
        if latest is not None and latest.terminal and (filled != prior_filled or status not in TERMINAL_ORDER_STATUSES):
            raise LiveOrderEvidenceError("terminal_order_history_reopened_or_filled")
        if status == "COMPLETE" and filled != identity.quantity:
            raise LiveOrderEvidenceError("complete_order_quantity_mismatch")
        latest = LiveOrderFact(identity, status, filled, value)
        prior_filled, prior_value = filled, value
    assert latest is not None
    return latest


def incremental_confirmed_fill(previous: LiveOrderFact | None,
                               current: LiveOrderFact) -> LiveFillDelta:
    """Return the unprojected cumulative broker delta, never a requested qty.

    Callers must reconcile the exact signed fill ledger and broker inventory,
    then durably commit the canonical projection before sending further orders.
    """
    if previous is not None and previous.identity != current.identity:
        raise LiveOrderEvidenceError("fill_projection_order_identity_conflict")
    prior_qty = previous.filled_quantity if previous else 0
    prior_value = previous.cumulative_value if previous else Decimal(0)
    delta_qty = current.filled_quantity - prior_qty
    delta_value = current.cumulative_value - prior_value
    if delta_qty < 0 or delta_value < 0 or (delta_qty == 0 and delta_value != 0):
        raise LiveOrderEvidenceError("fill_delta_not_monotone")
    if delta_qty > 0 and delta_value <= 0:
        raise LiveOrderEvidenceError("new_fill_has_no_value")
    return LiveFillDelta(current.identity, delta_qty, delta_value)
