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


async def submit_reduction(
    *, client: Any, uid: str, strategy_id: str, generation_id: str,
    signal_id: str, exchange: str, symbol: str, quantity: int,
    product: str, order_type: str = "market_order", limit_price: float = 0.0,
    kind: str = "exit", reason: str = "", close: bool = True,
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
    if type(getattr(client, "_is_paper", None)) is not bool:
        return ExecutionResult(False, "REJECTED", error="Broker mode unknown")
    account_id = str(getattr(client, "_account_id", "") or "").strip()
    if not account_id:
        return ExecutionResult(False, "REJECTED", error="Authenticated broker account unavailable")

    try:
        holding = db.get_inventory_strict(account_id, uid, symbol)
    except Exception:
        return ExecutionResult(False, "REJECTED", error="Canonical inventory unavailable")
    try:
        sufficient = bool(
            holding
            and str(holding.get("exchange") or "").upper() == exchange.upper()
            and not bool(holding.get("reconciliation_required"))
            and int(holding.get("net_quantity") or 0) >= qty
        )
    except (TypeError, ValueError, OverflowError):
        sufficient = False
    if not sufficient:
        return ExecutionResult(
            False, "REJECTED",
            error="Canonical inventory is missing, stale, unreconciled or insufficient; reconcile before reduction",
        )

    request = ExecutionRequest(
        uid=uid,
        account_id=account_id,
        strategy_id=strategy_id,
        generation_id=generation_id,
        signal_id=f"{signal_id}:reduce:{kind}",
        exchange=exchange.upper(),
        symbol=symbol,
        side="SELL",
        quantity=qty,
        exposure_effect=(
            ExposureEffect.CLOSE_POSITION if close else ExposureEffect.REDUCE_EXPOSURE
        ),
        order_type=order_type,
        price=px,
        payload={"product": product.upper(), "intent_type": "EXIT",
                 "reduction_kind": kind, "reason": reason},
    )
    try:
        result = await CanonicalExecutionService().submit_order(
            request, broker_client=client,
        )
    except BaseException as exc:
        # Cancellation must propagate after the executor marks any in-flight
        # broker send uncertain. Other unknown failures must never trigger a
        # legacy engine's stop re-arm or second SELL.
        import asyncio
        if isinstance(exc, asyncio.CancelledError):
            raise
        return ExecutionResult(False, "UNKNOWN", error=f"Canonical exit failed; reconciliation required: {exc}")
    if result.success and not result.order_id:
        return ExecutionResult(
            False, "UNKNOWN", intent_key=result.intent_key,
            error="Canonical order acknowledgement missing broker order ID",
        )
    return result
