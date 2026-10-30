"""Conservative canonical bridge for legacy strategy risk-reducing orders.

No broker writes here. Legacy engines supply a verified account, exact
contract and stable reduction identity; the only sender is
CanonicalExecutionService with the durable order-intent journal.

Fail closed if an engine's position has not been reconciled to canonical
inventory. NEVER synthesize inventory or reclassify an unknown SELL to entry.
"""
from __future__ import annotations

import math
from numbers import Integral
from typing import Any

from app.services import db
from app.services.execution_service import (
    CanonicalExecutionService, ExecutionRequest, ExecutionResult, ExposureEffect,
)


def reduction_inventory_refusal(
    *, client: Any, uid: str, exchange: str, symbol: str,
    quantity: int, side: str = "SELL",
) -> str | None:
    """Side-effect-free admission check, safe before canceling a resting GTT.

    Cannot replace the two fresh broker position proofs at submit time. It
    prevents removing protection when canonical inventory is not ready.
    """
    if type(getattr(client, "_is_paper", None)) is not bool:
        return "Broker mode unknown"
    account_id = str(getattr(client, "_account_id", "") or "").strip()
    if not account_id:
        return "Authenticated broker account unavailable"
    if type(quantity) is not int or quantity <= 0 or side not in ("SELL", "BUY"):
        return "Invalid reduction quantity or closing side"
    try:
        holding = db.get_inventory_strict(account_id, uid, symbol)
        if not holding or holding.get("reconciliation_required"):
            return "Canonical inventory missing or requires reconciliation"
        if str(holding.get("exchange") or "").strip().upper() != exchange.upper():
            return "Canonical inventory exchange mismatch"
        net = int(holding.get("net_quantity") or 0)
        if not ((side == "SELL" and net >= quantity)
                or (side == "BUY" and net <= -quantity)):
            return "Canonical inventory insufficient for exact closing side"
    except Exception:
        return "Canonical inventory unavailable or invalid"
    return None


async def submit_reduction(
    *, client: Any, uid: str, strategy_id: str, generation_id: str,
    signal_id: str, exchange: str, symbol: str, quantity: int,
    product: str, order_type: str = "market_order", limit_price: float = 0.0,
    kind: str = "exit", reason: str = "", close: bool = True,
    side: str = "SELL",
) -> ExecutionResult:
    """Reserve/send a SELL only through authenticated, canonical reduction.

    A missing canonical inventory row is a migration blocker, not permission to
    create or backfill a holding based solely on this caller's position cache.
    The execution service additionally proves fresh broker inventory (twice)
    and records UNKNOWN sends without automatic resend.
    """
    try:
        qty = int(quantity)
        px = float(limit_price)
    except (TypeError, ValueError, OverflowError):
        return ExecutionResult(False, "REJECTED", error="Invalid reduction quantity or price")
    if (isinstance(quantity, bool) or not isinstance(quantity, Integral)
            or qty <= 0 or not math.isfinite(px) or px < 0):
        return ExecutionResult(False, "REJECTED", error="Reduction requires integral positive quantity and finite nonnegative price")
    if not all(isinstance(x, str) and x.strip() for x in (
        uid, strategy_id, generation_id, signal_id, exchange, symbol, product, kind
    )):
        return ExecutionResult(False, "REJECTED", error="Missing immutable reduction identity")
    if side not in ("SELL", "BUY"):
        return ExecutionResult(False, "REJECTED", error="Unknown reduction closing side")
    account_id = str(getattr(client, "_account_id", "") or "").strip()
    inventory_refusal = reduction_inventory_refusal(
        client=client, uid=uid, exchange=exchange, symbol=symbol,
        quantity=qty, side=side,
    )
    if inventory_refusal:
        return ExecutionResult(False, "REJECTED", error=inventory_refusal)

    request = ExecutionRequest(
        uid=uid,
        account_id=account_id,
        strategy_id=strategy_id,
        generation_id=generation_id,
        signal_id=f"{signal_id}:reduce:{kind}",
        exchange=exchange.upper(),
        symbol=symbol,
        side=side,
        quantity=qty,
        exposure_effect=(
            ExposureEffect.CLOSE_POSITION if close else ExposureEffect.REDUCE_EXPOSURE
        ),
        order_type=order_type,
        price=px,
        # The engine persists the initiating reason beside the exit claim.
        # Do not put a later/changed UI reason in the immutable journal payload:
        # the same signal must never be re-sent just because an EOD sweep and
        # stop-monitor reported different reasons on replay.
        payload={"product": product.upper(), "intent_type": "EXIT",
                 "reduction_kind": kind},
    )
    try:
        result = await CanonicalExecutionService().submit_order(
            request, broker_client=client,
        )
    except Exception as exc:
        # The canonical executor already marks cancellation in flight UNKNOWN
        # and CancelledError propagates. Other unclassified exceptions must
        # never prompt the engine to send a second SELL or re-arm protection.
        return ExecutionResult(False, "UNKNOWN", error=f"Canonical exit failed; reconciliation required: {exc}")
    if result.success and not result.order_id:
        return ExecutionResult(
            False, "UNKNOWN", intent_key=result.intent_key,
            error="Canonical order acknowledgement missing broker order ID",
        )
    return result
