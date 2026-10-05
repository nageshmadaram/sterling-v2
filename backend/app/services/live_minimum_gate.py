"""Reads the facts behind the day-one exposure envelope, then asks the rule.

:mod:`app.core.live_minimum` is deliberately pure: limits and numbers in, a
verdict out. This module is the other half -- it goes and reads the numbers from
the systems that own them, so a failure to read one shows up as an UNKNOWN input
(which the rule refuses) rather than as a limit that quietly answered "fine":

* open gross exposure and the lot size come from the fill-ledger inventory
  (``kite_inventory``), through the strict reader that raises on a database error;
* today's realised loss comes from the same account-level reading the
  daily-loss circuit breaker uses, and the breaker's own hard-halt figure is the
  default loss budget, so the two can never disagree about what "too much" is;
* the capital the order is measured against is the broker-verified figure the
  canonical executor already insisted on, never a caller-supplied one.

Only exposure-increasing entries on a live broker transport come through here.
The SafetySupervisor (safe mode, recovery state, unresolved intents, kill
switch, the loss halt itself, external exposure) has already been asked by the
time this runs, so the system-state half of the envelope is not asked again.
"""
from __future__ import annotations

import inspect
import logging
from typing import Any, Mapping

from app.core.live_minimum import (
    DAILY_LOSS_ENV,
    DEFAULT_MAX_DEPLOYED_PCT,
    GROSS_EXPOSURE_ENV,
    MAX_DEPLOYED_PCT_ENV,
    MAX_LOTS_ENV,
    LiveMinimumEnvelope,
    LiveMinimumVerdict,
    check_order,
    configured_deployed_pct,
    configured_envelope,
)

log = logging.getLogger(__name__)

__all__ = [
    "evaluate_entry",
    "resolve_envelope",
    "open_gross_exposure",
    "describe_envelope",
]

#: Intent states that mean the order has already gone out. A repeat of the same
#: signal then resolves to the existing intent in the journal; the envelope must
#: not turn that idempotent replay into a refusal.
_PLACED_STATES = frozenset({"SUBMITTED", "FILLED", "PARTIAL", "ACKNOWLEDGED"})

#: Payload keys that may carry the order's unit price, in preference order.
_PRICE_KEYS = ("limit_price", "price", "entry_price")


def _is_replay_of_placed_intent(request: Any) -> bool:
    try:
        from app.services.kite_engine import order_journal

        key = order_journal.make_key(
            uid=request.uid,
            account_id=request.account_id,
            strategy_id=request.strategy_id,
            generation_id=request.generation_id,
            signal_id=request.signal_id,
            exchange=request.exchange,
            symbol=request.symbol,
            side=request.side,
        )
        existing = order_journal.get(key)
    except Exception:  # noqa: BLE001 - an unreadable journal is not a replay
        return False
    return existing is not None and str(existing.state) in _PLACED_STATES


def _read_inventory(request: Any) -> list[dict] | None:
    try:
        from app.services import db

        return list(db.get_inventory_all_strict(request.account_id, request.uid))
    except Exception as exc:  # noqa: BLE001 - unreadable is unknown, never "flat"
        log.warning("live-minimum: inventory unreadable: %s", exc)
        return None


def open_gross_exposure(rows: list[dict] | None) -> float | None:
    """Capital deployed in open positions: ``sum(|net qty| x average cost)``.

    ``None`` when it cannot be established -- an unreadable inventory, a held
    row with no cost basis, or a row the ledger has flagged as contradictory.
    """
    if rows is None:
        return None
    total = 0.0
    for row in rows:
        try:
            quantity = int(row.get("net_quantity") or 0)
            if quantity == 0:
                continue
            if row.get("reconciliation_required"):
                return None
            cost = float(row.get("average_cost") or 0.0)
        except (TypeError, ValueError):
            return None
        if cost <= 0:
            return None
        total += abs(quantity) * cost
    return round(total, 2)


def _order_value(request: Any, order_value: float | None) -> float | None:
    """The capital the order commits: the approved figure, else its own notional.

    A priced order (LIMIT / SL) states what it will spend even when the approval
    did not carry a figure. A MARKET order with no approved figure has nothing to
    measure and stays unknown.
    """
    if order_value is not None and order_value > 0:
        return float(order_value)
    try:
        price = float(getattr(request, "price", 0.0) or 0.0)
        quantity = int(getattr(request, "quantity", 0) or 0)
    except (TypeError, ValueError):
        return None
    return price * quantity if price > 0 and quantity > 0 else None


def _unit_price(request: Any, order_value: float | None) -> float | None:
    for candidate in (getattr(request, "price", 0.0),
                      *((request.payload or {}).get(k) for k in _PRICE_KEYS)):
        try:
            if candidate is not None and float(candidate) > 0:
                return float(candidate)
        except (TypeError, ValueError):
            continue
    quantity = int(getattr(request, "quantity", 0) or 0)
    if order_value is not None and order_value > 0 and quantity > 0:
        return order_value / quantity
    return None


def _averaging_down(rows: list[dict] | None, request: Any,
                    unit_price: float | None) -> bool | None:
    """Does this order add to a same-direction position that is under water?"""
    if rows is None:
        return None
    side = str(request.side or "").upper()
    held = next((r for r in rows if str(r.get("symbol") or "") == request.symbol), None)
    try:
        net = int(held.get("net_quantity") or 0) if held else 0
    except (TypeError, ValueError):
        return None
    adds_to_existing = (side == "BUY" and net > 0) or (side == "SELL" and net < 0)
    if not adds_to_existing:
        return False
    cost = float(held.get("average_cost") or 0.0)
    if cost <= 0 or unit_price is None:
        return None
    return unit_price < cost if side == "BUY" else unit_price > cost


def _daily_loss(uid: str) -> tuple[float | None, float | None]:
    """(realised loss today as a positive number, the account's hard-halt size)."""
    try:
        from app.services import live_safety

        state = live_safety.daily_loss_state(uid=uid)
        pnl = float(state["pnl_inr"])
        halt = abs(float(state["hard_halt_inr"]))
    except Exception as exc:  # noqa: BLE001 - a failed risk read is never zero loss
        log.warning("live-minimum: daily loss unreadable: %s", exc)
        return None, None
    return max(0.0, -pnl), (halt if halt > 0 else None)


async def _lot_size(request: Any, rows: list[dict] | None, broker_client: Any) -> int | None:
    """Lot size, only asked for when the operator capped lots."""
    try:
        declared = int((request.payload or {}).get("lot_size") or 0)
        if declared > 0:
            return declared
    except (TypeError, ValueError):
        pass
    held = next((r for r in (rows or []) if str(r.get("symbol") or "") == request.symbol), None)
    if held and int(held.get("lot_size") or 0) > 0:
        return int(held["lot_size"])
    lookup = getattr(broker_client, "instrument_lot_sizes", None)
    if not callable(lookup):
        return None
    try:
        key = f"{request.exchange}:{request.symbol}"
        found = lookup([key])
        if inspect.isawaitable(found):
            found = await found
        size = int((found or {}).get(key) or 0)
    except Exception as exc:  # noqa: BLE001
        log.warning("live-minimum: lot size lookup failed for %s: %s", request.symbol, exc)
        return None
    return size if size > 0 else None


def resolve_envelope(
    explicit: LiveMinimumEnvelope,
    *,
    hard_halt_inr: float | None,
    available_capital: float | None,
    open_gross: float | None,
    deployed_pct: float = DEFAULT_MAX_DEPLOYED_PCT,
) -> LiveMinimumEnvelope:
    """Explicit limits win; anything the operator left out is derived.

    A limit the operator set but got wrong is never overwritten by a derived
    one -- ``explicit.invalid`` carries through and refuses the entry.
    """
    sources = dict(explicit.sources)

    daily = explicit.daily_loss_budget
    if daily is None and DAILY_LOSS_ENV not in explicit.invalid and hard_halt_inr:
        daily = float(hard_halt_inr)
        sources["daily_loss_budget"] = "derived: the account's daily-loss hard halt"

    gross = explicit.gross_exposure_limit
    if (gross is None and GROSS_EXPOSURE_ENV not in explicit.invalid
            and available_capital and available_capital > 0 and open_gross is not None):
        gross = round((float(available_capital) + float(open_gross)) * deployed_pct / 100.0, 2)
        sources["gross_exposure_limit"] = (
            f"derived: {deployed_pct:g}% of broker-verified capital"
        )

    return LiveMinimumEnvelope(
        daily_loss_budget=daily,
        gross_exposure_limit=gross,
        max_lots=explicit.max_lots,
        invalid=explicit.invalid,
        sources=sources,
    )


async def evaluate_entry(
    *,
    request: Any,
    order_value: float | None,
    available_capital: float | None,
    broker_client: Any = None,
    env: Mapping[str, str] | None = None,
) -> LiveMinimumVerdict:
    """Does this exposure-increasing order fit the day-one envelope?

    Every input that cannot be read is passed on as ``None`` and refuses.
    """
    if _is_replay_of_placed_intent(request):
        return LiveMinimumVerdict(allowed=True)

    order_value = _order_value(request, order_value)
    explicit = configured_envelope(env)
    rows = _read_inventory(request)
    gross = open_gross_exposure(rows)
    loss, hard_halt = _daily_loss(request.uid)
    envelope = resolve_envelope(
        explicit,
        hard_halt_inr=hard_halt,
        available_capital=available_capital,
        open_gross=gross,
        deployed_pct=configured_deployed_pct(env),
    )

    lot = None
    if envelope.max_lots is not None:
        lot = await _lot_size(request, rows, broker_client)

    verdict = check_order(
        quantity=int(request.quantity),
        minimum_executable_quantity=lot,
        order_value=order_value,
        open_gross_exposure=gross,
        realised_loss_today=loss,
        is_averaging_down=_averaging_down(rows, request, _unit_price(request, order_value)),
        envelope=envelope,
        max_lots=envelope.max_lots,
        require_system_state=False,
    )
    if not verdict.allowed:
        log.warning(
            "live-minimum refused %s %s x%s for %s: %s",
            request.side, request.symbol, request.quantity, request.strategy_id,
            verdict.message(),
        )
    return verdict


def describe_envelope(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """What an operator needs to know about the envelope without placing an order."""
    explicit = configured_envelope(env)
    hard_halt = None
    try:
        from app.services import live_safety

        hard_halt = abs(float(live_safety.daily_loss_config().hard_halt_inr)) or None
    except Exception:  # noqa: BLE001
        pass
    return {
        "explicit": explicit.as_dict(),
        "daily_loss_budget_default": hard_halt,
        "max_deployed_pct": configured_deployed_pct(env),
        "environment": {
            "daily_loss": DAILY_LOSS_ENV,
            "gross_exposure": GROSS_EXPOSURE_ENV,
            "max_deployed_pct": MAX_DEPLOYED_PCT_ENV,
            "max_lots": MAX_LOTS_ENV,
        },
    }
