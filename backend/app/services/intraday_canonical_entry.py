"""Canonical entry bridge for the Intraday Pack.

Only exposure-increasing BUYs cross this module. Exits, scale-outs and protective
mutations remain in ``intraday_runner`` so an admission halt can never strand an
open position.

The bridge binds the strategy generation, signal identity, authenticated account,
quantity, price and fresh broker capital into the canonical risk proof. It does
not decide whether the signal is valid; ``intraday_runner.arm`` has already run
the strategy/session/liquidity/sizing gates before calling here.
"""
from __future__ import annotations

import time
from typing import Any

from app.engines.intraday import CONTRACT_VERSION, STRATEGY_ID
from app.services.execution_service import (
    CanonicalExecutionService,
    ExecutionRequest,
    ExecutionResult,
    ExposureEffect,
    RiskApproval,
)


async def _available_entry_capital(client: Any) -> float:
    """Return capital evidence appropriate to the transport mode.

    Live clients must provide fresh broker F&O margins. Explicit paper clients may
    use the adapter's own simulated equity balance when no authenticated broker
    session exists; no configured/operator number is accepted as a substitute.
    """
    from app.services.kite_engine.service import available_fo_capital

    available = float(await available_fo_capital(client))
    if available > 0:
        return available

    if getattr(client, "_is_paper", None) is not True:
        return 0.0

    get_balances = getattr(client, "get_balances", None)
    if not callable(get_balances):
        return 0.0
    try:
        balances = await get_balances()
    except Exception:
        return 0.0

    for balance in balances or []:
        asset = str(getattr(balance, "asset", "") or "").strip().lower()
        if asset.startswith("inr (equity)"):
            try:
                simulated = float(getattr(balance, "available", 0.0) or 0.0)
            except (TypeError, ValueError):
                return 0.0
            return simulated if simulated > 0 else 0.0
    return 0.0


async def submit_entry(
    *,
    client: Any,
    uid: str,
    signal_id: str,
    sub_strategy: str,
    exchange: str,
    symbol: str,
    quantity: int,
    limit_price: float,
) -> ExecutionResult:
    """Submit one long-option Intraday entry through the canonical authority."""
    account_id = str(getattr(client, "_account_id", "") or "").strip()
    if not account_id:
        return ExecutionResult(
            success=False,
            status="REJECTED",
            error="Authenticated broker account identity is unavailable",
        )

    # The canonical journal requires fresh broker evidence for exposure increases.
    # Do not substitute cfg.capital_inr: that is an operator allocation, not proof
    # of current buying power.
    available_capital = await _available_entry_capital(client)
    if available_capital <= 0:
        return ExecutionResult(
            success=False,
            status="REJECTED",
            error="Fresh broker F&O capital is unavailable",
        )

    qty = int(quantity)
    price = float(limit_price)
    generation_id = f"{CONTRACT_VERSION}:{str(sub_strategy or 'unknown')}"
    now_ms = int(time.time() * 1000)
    capital_required = max(0.0, price * qty)

    request = ExecutionRequest(
        uid=uid,
        account_id=account_id,
        strategy_id=STRATEGY_ID,
        generation_id=generation_id,
        signal_id=signal_id,
        exchange=exchange,
        symbol=symbol,
        side="BUY",
        quantity=qty,
        tag=STRATEGY_ID[:20],
        exposure_effect=ExposureEffect.INCREASE_EXPOSURE,
        order_type="limit_order",
        price=price,
        capital_required=capital_required,
        payload={"product": "MIS", "sub_strategy": str(sub_strategy or "")},
    )
    approval = RiskApproval(
        approval_id=f"intraday-{now_ms}",
        uid=uid,
        account_id=account_id,
        symbol=symbol,
        side="BUY",
        quantity=qty,
        generation_id=generation_id,
        approved=True,
        reason="Intraday strategy, session, liquidity, sizing and account safety gates passed",
        timestamp_ms=now_ms,
        available_capital=available_capital,
        capital_required=capital_required,
        strategy_id=STRATEGY_ID,
        signal_id=signal_id,
    )
    return await CanonicalExecutionService().submit_order(
        request,
        broker_client=client,
        risk_approval=approval,
    )
