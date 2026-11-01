"""Canonical exposure-increasing entry bridge for strategy runners.

The strategy runner owns signal validity, sizing, session rules and protection.
This bridge binds those decisions to authenticated account identity, fresh
capital evidence, the durable execution journal and the global admission
authority. It never weakens the global live-entry floor.
"""
from __future__ import annotations

import math
import time
from numbers import Integral
from typing import Any, Mapping, Optional

from app.services.execution_service import (
    CanonicalExecutionService,
    ExecutionRequest,
    ExecutionResult,
    ExposureEffect,
    RiskApproval,
)


async def available_entry_capital(client: Any) -> float:
    """Fresh F&O capital, with a simulated-balance fallback for paper clients."""
    from app.services.kite_engine.service import available_fo_capital

    try:
        available = float(await available_fo_capital(client))
    except Exception:
        available = 0.0
    if math.isfinite(available) and available > 0:
        return available

    if getattr(client, "_is_paper", None) is not True:
        return 0.0
    reader = getattr(client, "get_balances", None)
    if not callable(reader):
        return 0.0
    try:
        balances = await reader()
    except Exception:
        return 0.0
    for balance in balances or []:
        asset = str(getattr(balance, "asset", "") or "").strip().lower()
        if not asset.startswith("inr (equity)"):
            continue
        try:
            value = float(getattr(balance, "available", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0
        return value if math.isfinite(value) and value > 0 else 0.0
    return 0.0


async def submit_entry(
    *,
    client: Any,
    uid: str,
    strategy_id: str,
    generation_id: str,
    signal_id: str,
    exchange: str,
    symbol: str,
    quantity: int,
    capital_price: float,
    order_type: str = "limit_order",
    limit_price: float = 0.0,
    product: str = "MIS",
    tag: str = "",
    reason: str = "Strategy admission and risk gates passed",
    payload: Optional[Mapping[str, Any]] = None,
) -> ExecutionResult:
    """Submit one BUY exposure increase through the canonical authority."""
    account_id = str(getattr(client, "_account_id", "") or "").strip()
    if not account_id:
        return ExecutionResult(
            success=False,
            status="REJECTED",
            error="Authenticated broker account identity is unavailable",
        )

    # Reject malformed or non-finite financial inputs before reserving an
    # execution intent. Never coerce a fractional or boolean size to contracts.
    try:
        qty = int(quantity)
        price = float(capital_price)
    except (TypeError, ValueError, OverflowError):
        qty, price = 0, 0.0
    if (
        isinstance(quantity, bool)
        or not isinstance(quantity, Integral)
        or qty <= 0
        or not math.isfinite(price)
        or price <= 0
    ):
        return ExecutionResult(
            success=False,
            status="REJECTED",
            error="Canonical entry requires finite positive price and integral quantity",
        )

    available = await available_entry_capital(client)
    if available <= 0:
        return ExecutionResult(
            success=False,
            status="REJECTED",
            error="Fresh broker F&O capital is unavailable",
        )

    required = price * qty
    if not math.isfinite(required) or required <= 0:
        return ExecutionResult(
            success=False,
            status="REJECTED",
            error="Canonical entry capital requirement is not finite",
        )
    now_ms = int(time.time() * 1000)
    request_payload = dict(payload or {})
    request_payload.setdefault("product", product)
    request_payload.setdefault("intent_type", "ENTRY")

    request = ExecutionRequest(
        uid=uid,
        account_id=account_id,
        strategy_id=str(strategy_id),
        generation_id=str(generation_id),
        signal_id=str(signal_id),
        exchange=str(exchange),
        symbol=str(symbol),
        side="BUY",
        quantity=qty,
        tag=str(tag or strategy_id)[:20],
        exposure_effect=ExposureEffect.INCREASE_EXPOSURE,
        order_type=str(order_type),
        price=float(limit_price or 0.0),
        capital_required=required,
        available_capital=available,
        payload=request_payload,
    )
    approval = RiskApproval(
        approval_id=f"{strategy_id}-{now_ms}",
        uid=uid,
        account_id=account_id,
        symbol=str(symbol),
        side="BUY",
        quantity=qty,
        generation_id=str(generation_id),
        approved=True,
        reason=str(reason),
        timestamp_ms=now_ms,
        available_capital=available,
        capital_required=required,
        strategy_id=str(strategy_id),
        signal_id=str(signal_id),
    )
    return await CanonicalExecutionService().submit_order(
        request,
        broker_client=client,
        risk_approval=approval,
    )
