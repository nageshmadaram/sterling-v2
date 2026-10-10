"""SuperTrend Telegram lifecycle alerts: one place that decides what each number means.

The alert text is built in the canonical vocabulary every Sterling board and alert
uses -- Entry, LTP, SL, TSL, Target, Exit, P&L, Qty, Capital -- with premium per unit
in rupees, never the underlying's index level. ``Qty`` is units (lots x lot size),
the same figure the positions table shows.

Producers call :func:`emit`; it never raises, because a notification failure must
not change how a position is managed.
"""
from __future__ import annotations

import math
from typing import Iterable, Optional

STRATEGY = "SuperTrend"
_SEP = " · "


def _num(value: object) -> Optional[float]:
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def inr(value: object, *, signed: bool = False) -> str:
    number = _num(value)
    if number is None:
        return "—"
    if round(number, 2) == 0:
        number = 0.0
    sign = "-" if number < 0 else ("+" if signed else "")
    return f"{sign}₹{abs(number):,.2f}"


def build_detail(
    *, entry: object = None, ltp: object = None, sl: object = None, tsl: object = None,
    tsl_from: object = None, exit: object = None, pnl: object = None,  # noqa: A002
    qty: object = None, qty_total: object = None, reason: str = "",
    notes: Iterable[str] = (),
) -> str:
    """Canonical order: Entry, LTP, SL, TSL, Exit, P&L, Qty, Reason, notes.

    A field that is ``None`` is omitted. Zero is never printed as a stand-in for
    "unknown": an unset stop is simply absent.
    """
    parts: list[str] = []
    if _num(entry) is not None and float(entry) > 0:  # type: ignore[arg-type]
        parts.append(f"Entry {inr(entry)}")
    if _num(ltp) is not None and float(ltp) > 0:  # type: ignore[arg-type]
        parts.append(f"LTP {inr(ltp)}")
    if _num(sl) is not None and float(sl) > 0:  # type: ignore[arg-type]
        parts.append(f"SL {inr(sl)}")
    if _num(tsl) is not None and float(tsl) > 0:  # type: ignore[arg-type]
        if _num(tsl_from) is not None and float(tsl_from) > 0:  # type: ignore[arg-type]
            parts.append(f"TSL {inr(tsl_from)} → {inr(tsl)}")
        else:
            parts.append(f"TSL {inr(tsl)}")
    if _num(exit) is not None and float(exit) > 0:  # type: ignore[arg-type]
        parts.append(f"Exit {inr(exit)}")
    if _num(pnl) is not None:
        parts.append(f"P&L {inr(pnl, signed=True)}")
    if _num(qty) is not None:
        done = int(float(qty))  # type: ignore[arg-type]
        total = int(float(qty_total)) if _num(qty_total) is not None else None  # type: ignore[arg-type]
        parts.append(f"Qty {done:,}/{total:,}" if total is not None and total != done
                     else f"Qty {done:,}")
    if reason:
        parts.append(f"Reason {reason}")
    parts.extend(str(n) for n in notes if str(n or "").strip())
    return _SEP.join(parts)


def realized_pnl(entry: object, exit: object, qty: object, direction: str = "long") -> Optional[float]:  # noqa: A002
    """Gross realized P&L in rupees for ``qty`` units; None when any input is unknown.

    Long: (exit - entry) x qty. A short futures position is the negation. Options
    are always long premium, whichever way the underlying signal pointed.
    """
    e, x, q = _num(entry), _num(exit), _num(qty)
    if e is None or x is None or q is None or e <= 0 or x <= 0 or q <= 0:
        return None
    sign = -1.0 if str(direction).lower() == "short" else 1.0
    return (x - e) * q * sign


def stop_kind(position) -> str:
    """``SL`` while the stop is still the one set at entry, ``TSL`` once it has trailed."""
    initial = _num(getattr(position, "initial_stop_premium", 0.0)) or 0.0
    current = _num(getattr(position, "stop_premium", 0.0)) or 0.0
    if initial > 0 and current > initial + 1e-9:
        return "TSL"
    return "SL"


def stop_fields(position) -> dict:
    """``{"sl": stop}`` before the stop has trailed, ``{"tsl": stop}`` after, so a
    resting stop is always named the way the board names it."""
    stop = _num(getattr(position, "stop_premium", 0.0)) or 0.0
    if stop <= 0:
        return {}
    return {"tsl": stop} if stop_kind(position) == "TSL" else {"sl": stop}


def emit(uid: str, *, dedupe_key: str, event_type: str, title: str, detail: str,
         severity: str = "INFO", created_at_ms: int | None = None) -> None:
    """Queue one SuperTrend alert. Never raises."""
    try:
        from app.services.notifications.telegram_lifecycle import enqueue_best_effort
        enqueue_best_effort(
            uid, dedupe_key=dedupe_key, strategy=STRATEGY, event_type=event_type,
            title=title, detail=detail, severity=severity, created_at_ms=created_at_ms,
        )
    except Exception:  # noqa: BLE001 - alerting is never allowed to alter trading
        pass
