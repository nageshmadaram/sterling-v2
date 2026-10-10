"""Telegram lifecycle alerts for Snapback positions: entry, TSL, exit.

Snapback was silent on Telegram: the only messages it could send were operational
faults, so a position opened, trailed and closed without the owner hearing of it.
These producers hand one event per transition to the shared durable lifecycle outbox
(the same one SuperTrend and Gamma Unwind use), in the shared vocabulary:
Entry, SL, TSL, Exit, P&L, Qty, Capital.

Two rules hold throughout.

* Alerting never alters trading. Every producer swallows its own failure; the
  position lifecycle has already been committed by the time one runs.
* The number in the alert is the number in the ledger. P&L is the outcome row's
  ``actual_total_pnl`` -- option and hedge legs net of every cost -- and the entry is
  the executed fill, not the quote the signal was priced from.

Stops are percentages of the executed fill: the premium stop is
``premium_stop_pct`` below it, and a runner's TSL is ``runner_trail_pct`` below its
recorded end-of-day peak.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Iterable, Optional

log = logging.getLogger(__name__)

STRATEGY = "Snapback"

#: Exit reason (as stored on the outcome) -> (wording, severity).  Every exit is a
#: "POSITION CLOSED" event, exactly as for SuperTrend and Gamma Unwind; the reason
#: is what differs.
_EXIT_KINDS = {
    "PREMIUM_STOP": ("Stop loss hit", "WARNING"),
    "RUNNER_TRAIL_STOP": ("Trailing stop hit", "WARNING"),
    "HOLDING_HORIZON_EXPIRED": ("Holding period ended", "INFO"),
    "EXPIRY_APPROACHING": ("Closed ahead of expiry", "INFO"),
}


def _finite(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _money(value: Any, *, signed: bool = False) -> str:
    number = _finite(value)
    if number is None:
        return "—"
    sign = "-" if number < 0 else ("+" if signed else "")
    return f"{sign}₹{abs(number):,.2f}"


def build_detail(
    *, entry=None, sl=None, tsl=None, tsl_from=None, exit=None, pnl=None, qty=None,
    capital=None, reason: str = "", notes: Iterable[str] = (),
) -> str:
    """Canonical field order: Entry, SL, TSL, Exit, P&L, Qty, Capital, Reason, notes."""
    try:
        from app.services.notifications.lifecycle_format import detail

        return detail(entry=entry, sl=sl, tsl=tsl, tsl_from=tsl_from, exit=exit, pnl=pnl,
                      qty=qty, capital=capital, reason=reason, notes=tuple(notes))
    except Exception:  # noqa: BLE001 - the shared formatter is a nicety, not a dependency
        parts = []
        if entry is not None:
            parts.append(f"Entry {_money(entry)}")
        if sl is not None:
            parts.append(f"SL {_money(sl)}")
        if tsl is not None:
            parts.append(f"TSL {_money(tsl_from)} → {_money(tsl)}" if tsl_from is not None
                         else f"TSL {_money(tsl)}")
        if exit is not None:
            parts.append(f"Exit {_money(exit)}")
        if pnl is not None:
            parts.append(f"P&L {_money(pnl, signed=True)}")
        if qty is not None:
            parts.append(f"Qty {int(qty):,}")
        if capital is not None:
            parts.append(f"Capital {_money(capital)}")
        if reason:
            parts.append(f"Reason {reason}")
        parts.extend(str(n) for n in notes if str(n or "").strip())
        return " · ".join(parts)


def emit(*, dedupe_key: str, event_type: str, title: str, detail: str,
         severity: str = "INFO", created_at_ms: Optional[int] = None, uid: str = "") -> bool:
    """Queue one Snapback lifecycle alert. Never raises; True when it was accepted.

    An event of the virtual Simulation tenant (``uid`` is its sim uid) is queued under that
    tenant, so it is rendered with the simulation prefix and never as a live alert; every
    other event goes to the live alert user, exactly as before."""
    try:
        from app.services.notifications.telegram_lifecycle import enqueue_best_effort
        from app.services.simulation.identity import is_sim_uid
        from app.services.snapback_alert_telegram import alert_user_id

        return bool(enqueue_best_effort(
            uid if is_sim_uid(uid) else alert_user_id(), dedupe_key=dedupe_key, strategy=STRATEGY,
            event_type=event_type, title=title, detail=detail, severity=severity,
            created_at_ms=created_at_ms,
        ))
    except Exception as exc:  # noqa: BLE001 - alerting is never allowed to alter trading
        log.warning("Snapback lifecycle alert not queued [%s]: %s", event_type, exc)
        return False


def _never_raises(fn):
    """A producer runs after the ledger has committed; its failure must not surface."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            log.warning("Snapback lifecycle alert %s failed: %s", fn.__name__, exc)
            return False

    return wrapper


@_never_raises
def entry_filled(
    *, opportunity_id: str, option_symbol: str, fill_price: float, qty: int,
    stop_pct: float, hold_days: int, futures_symbol: str = "", hedge_lots: int = 0,
    futures_fill_price: Optional[float] = None,
) -> bool:
    fill = _finite(fill_price)
    if fill is None or fill <= 0 or int(qty) <= 0:
        return False
    notes = [f"Time stop after {int(hold_days)} sessions"]
    if int(hedge_lots) > 0 and futures_symbol:
        at = f" @ {_money(futures_fill_price)}" if _finite(futures_fill_price) else ""
        notes.insert(0, f"Hedge: long {int(hedge_lots)} lot(s) {futures_symbol}{at}")
    return emit(
        dedupe_key=f"snapback:{opportunity_id}:ENTRY", event_type="ENTRY FILLED",
        title=str(option_symbol),
        detail=build_detail(
            entry=fill, sl=round(fill * (1.0 - float(stop_pct) / 100.0), 2),
            qty=int(qty), capital=fill * int(qty), notes=notes,
        ),
    )


@_never_raises
def runner_armed(
    *, opportunity_id: str, option_symbol: str, entry_price: float, peak: float,
    trail_pct: float, qty: int,
) -> bool:
    """The horizon was reached with the premium well ahead: the time stop is replaced
    by a trailing stop, which is the first moment the owner has a TSL to watch."""
    return emit(
        dedupe_key=f"snapback:{opportunity_id}:RUNNER", event_type="TSL ARMED",
        title=str(option_symbol),
        detail=build_detail(
            entry=entry_price, tsl=round(peak * (1.0 - float(trail_pct) / 100.0), 2),
            qty=int(qty),
            notes=[f"Peak {_money(peak)}; trails {trail_pct:g}% below the closing peak",
                   "Time stop no longer applies"],
        ),
    )


@_never_raises
def tsl_moved(
    *, opportunity_id: str, option_symbol: str, session_date: str, old_peak: float,
    new_peak: float, trail_pct: float, qty: int,
) -> bool:
    """Raised only when the peak rises: a trail that can loosen is not reported."""
    old, new = _finite(old_peak), _finite(new_peak)
    if old is None or new is None or new <= old:
        return False
    factor = 1.0 - float(trail_pct) / 100.0
    return emit(
        dedupe_key=f"snapback:{opportunity_id}:TSL:{session_date}", event_type="TSL UPDATED",
        title=str(option_symbol),
        detail=build_detail(
            tsl=round(new * factor, 2), tsl_from=round(old * factor, 2), qty=int(qty),
            notes=[f"Peak {_money(old)} → {_money(new)}"]),
    )


@_never_raises
def position_closed(
    *, opportunity_id: str, option_symbol: str, reason: str, entry_price: float,
    exit_price: float, qty: int, option_pnl: float, futures_pnl: float,
    costs: float, net_pnl: float,
) -> bool:
    words, severity = _EXIT_KINDS.get(
        str(reason), (str(reason).replace("_", " ").capitalize(), "INFO"))
    return emit(
        dedupe_key=f"snapback:{opportunity_id}:EXIT", event_type="POSITION CLOSED",
        title=str(option_symbol), severity=severity,
        detail=build_detail(
            entry=entry_price, exit=exit_price, pnl=net_pnl, qty=int(qty), reason=words,
            notes=[f"Option {_money(option_pnl, signed=True)} · Hedge "
                   f"{_money(futures_pnl, signed=True)} · Costs {_money(-abs(costs), signed=True)}"],
        ),
    )
