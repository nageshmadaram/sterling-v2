"""Strategy adapters for the Trade Journal: each trading hook is ONE call into this module.

The three strategies keep very different position objects (Supertrend's ``OpenPosition``, Gamma's
``PositionState``, Snapback's ``LivePosition``) and report refusals in different ways. Everything
that translates them into journal calls lives here, so the hook sites in the trading code stay a
single line and know nothing about the journal's tables.

Rules every function obeys (they are what makes a one-line hook safe in live trading):

* **Never raises.** Each function is wrapped by :func:`_safe`; an unknown ``st_*``/``gm_*``/``sb_*``
  name resolves to a no-op (module ``__getattr__``), so even a mistyped hook cannot break a trade.
  ``tests/unit/test_trade_journal_hooks.py`` checks every hook name used in the trading code exists.
* **Never touches a broker, a lock or a registry.** It reads the object it is handed and writes only
  the journal's own tables.
* **Idempotent.** Each event has a natural key (order id, stop level, GTT id...), so the same fact
  reported from two code paths, a replayed postback or a restart is stored once.
* **Mode-blind.** ``mode`` comes from the uid inside :mod:`trade_journal`; a simulation tenant runs
  these same hooks and lands in SIMULATION.
"""
from __future__ import annotations

import functools
import hashlib
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Dict, Iterator, List, Optional

from app.core.logging import get_logger
from app.services import trade_journal as tj
from app.services.trade_journal_narrative import classify_skip, entry_reason, reason_for_code

log = get_logger(__name__)

SUPERTREND, GAMMA, SNAPBACK = "supertrend", "gamma", "snapback"
#: Virtual margin the simulated exchange charges for a future (design doc): 12% of notional.
FUTURES_MARGIN_PCT = 0.12
#: Refusal codes that only say the operator chose not to trade this strategy automatically.
#: They are a mode, not a declined signal: with Simulation on, the live tenant would otherwise
#: log "auto off" for every signal the virtual tenant is trading.
MODE_CODES = frozenset({"auto_off", "engine_disabled", "disabled"})


def _safe(fn: Callable) -> Callable:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - a hook must never reach the trading path
            tj._warn_once(fn.__name__, exc)
            return None
    return wrapper


def _noop(*_a: Any, **_k: Any) -> None:
    return None


def __getattr__(name: str) -> Callable:
    """An unknown hook name is a no-op, never an AttributeError inside a live trading path."""
    if name.startswith(("st_", "gm_", "sb_")):
        return _noop
    raise AttributeError(name)


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------

def _num(value: object) -> Optional[float]:
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return out if out == out and abs(out) != float("inf") else None


def _pos(value: object) -> Optional[float]:
    n = _num(value)
    return n if n is not None and n > 0 else None


def _int(value: object) -> Optional[int]:
    n = _num(value)
    return None if n is None else int(round(n))


def _now_ms() -> int:
    return int(time.time() * 1000)


def _h(text: str) -> str:
    return hashlib.sha1(str(text).encode("utf-8")).hexdigest()[:10]


def _kind_of(symbol: str, vehicle: str = "") -> str:
    if vehicle == "futures" or str(symbol).upper().endswith("FUT"):
        return "FUT"
    up = str(symbol).upper()
    return "CE" if up.endswith("CE") else ("PE" if up.endswith("PE") else "")


def _lots(qty: object, lot_size: object) -> Optional[int]:
    q, ls = _int(qty), _int(lot_size)
    return max(1, q // ls) if q and ls else None


def _rupees(value: object) -> str:
    n = _num(value)
    return "?" if n is None else f"{n:,.2f}"


# --------------------------------------------------------------------------
# Supertrend (kite_engine OpenPosition)
# --------------------------------------------------------------------------

#: While the engine decides on one signal (``service._make_place_cb``), refusals it logs are
#: attributed to that signal. Set by :func:`st_scope`; empty everywhere else, so a log line from
#: an exit or a recovery pass is never mistaken for a refusal.
_scope: ContextVar[Optional[Dict[str, Any]]] = ContextVar("trade_journal_scope", default=None)


@contextmanager
def st_scope(uid: str, row: Any) -> Iterator[None]:
    """``with st_scope(uid, row): await decide(row)``: refusals logged inside become skips."""
    token = None
    try:
        sig = f"{getattr(row, 'source', '')}:{getattr(row, 'timestamp_ms', '')}:{getattr(row, 'direction', '')}"
        token = _scope.set({
            "uid": uid, "signal_id": sig, "underlying": str(getattr(row, "underlying", "") or ""),
            "context": _st_context(row, {}), "trade_id": None})
    except Exception:  # noqa: BLE001
        token = None
    try:
        yield
    finally:
        if token is not None:
            try:
                _scope.reset(token)
            except Exception:  # noqa: BLE001
                pass


@_safe
def st_note_log(uid: str, kind: str, message: str) -> None:
    """Tap on the engine's activity log: a refusal inside a signal's scope becomes a skip; a
    failure after the order went out becomes a timeline note on that trade."""
    scope = _scope.get()
    if not scope or scope.get("uid") != uid:
        return
    text = str(message or "")
    low = text.lower()
    if kind == "order_blocked" or (kind == "info" and "skipped" in low):
        code, reason = classify_skip(SUPERTREND, text)
        if code in MODE_CODES:
            return
        tj.record_skip(uid=uid, strategy=SUPERTREND, code=code, reason=reason, symbol=scope["underlying"],
                       underlying=scope["underlying"], signal_id=scope["signal_id"],
                       context={**scope["context"], "detail": text})
    elif kind == "order_failed":
        if scope.get("trade_id"):
            tj.record_event(trade_id=scope["trade_id"], event_type=tj.FAILED, detail=text,
                            event_key=f"fail:{_h(text)}")
        else:
            tj.record_skip(uid=uid, strategy=SUPERTREND, code="order_failed", symbol=scope["underlying"],
                           underlying=scope["underlying"], signal_id=scope["signal_id"],
                           reason=reason_for_code("order_failed"), context={**scope["context"], "detail": text})


def _st_context(row: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    """What made the engine enter: alignment, trend strength, regime, score, source, sizing hints."""
    al = getattr(row, "alignment", None)
    vals = [getattr(al, n, 0) for n in ("fast", "mid", "slow")] if al is not None else []
    direction = str(getattr(row, "direction", "") or "")
    want = 1 if direction == "long" else -1
    ctx: Dict[str, Any] = {
        "regime": getattr(row, "regime", None), "direction": direction or None,
        "alignment": ({"fast": vals[0], "mid": vals[1], "slow": vals[2]} if vals else None),
        "aligned": (sum(1 for v in vals if v == want) if vals else None),
        "adx": _num(getattr(row, "adx", None)), "atr_pct": _num(getattr(row, "atr_pct", None)),
        "score": _num(getattr(row, "score", None)), "source": getattr(row, "source", None),
        "signal_ts_ms": _int(getattr(row, "timestamp_ms", None)),
        "underlying_trail": _num(getattr(row, "stop_loss", None)),
        "underlying_spot": _num(getattr(row, "underlying_spot", None) or getattr(row, "spot", None)),
        "exit_mode": payload.get("exit_mode"), "vehicle": payload.get("vehicle"),
        "entry_delta": _num(payload.get("entry_delta")), "stop_mode": payload.get("stop_mode"),
    }
    return {k: v for k, v in ctx.items() if v is not None}


def _st_key(p: Any) -> str:
    return str(getattr(p, "order_id", "") or f"t{int(getattr(p, 'opened_ms', 0) or 0)}")


def _st_trailed(p: Any) -> bool:
    stop, initial = _num(getattr(p, "stop_premium", 0)) or 0.0, _num(getattr(p, "initial_stop_premium", 0)) or 0.0
    if initial <= 0 or stop <= 0:
        return False
    return stop < initial - 1e-9 if getattr(p, "direction", "long") == "short" else stop > initial + 1e-9


def _st_fields(p: Any, *, filled: bool) -> Dict[str, Any]:
    fill = _pos(getattr(p, "fill_price", 0))
    qty = _int(getattr(p, "qty", 0)) or _int(getattr(p, "entry_requested_qty", 0))
    futures = getattr(p, "vehicle", "") == "futures"
    price = fill if filled and fill else None
    capital = None
    if price and qty:
        capital = round(price * qty * (FUTURES_MARGIN_PCT if futures else 1.0), 2)
    return dict(
        account_id=str(getattr(p, "account_id", "") or ""), underlying=str(getattr(p, "underlying", "") or ""),
        option_type=_kind_of(p.symbol, getattr(p, "vehicle", "")), strike=_pos(getattr(p, "strike", 0)),
        expiry=str(getattr(p, "expiry", "") or ""), side="SELL" if getattr(p, "direction", "long") == "short" else "BUY",
        lot_size=_int(getattr(p, "lot_size", 0)), lots=_lots(qty, getattr(p, "lot_size", 0)), qty=qty,
        entry_order_id=str(getattr(p, "order_id", "") or ""), entry_price=price,
        initial_sl=_pos(getattr(p, "initial_stop_premium", 0)) or _pos(getattr(p, "stop_premium", 0)),
        target=_pos(getattr(p, "target_premium", 0)), capital_used=capital)


def _st_extra(p: Any) -> Dict[str, Any]:
    return {"exchange": getattr(p, "exchange", None), "vehicle": getattr(p, "vehicle", None),
            "stop_mode": getattr(p, "stop_mode", None), "exit_mode": getattr(p, "exit_mode", None),
            "product": getattr(p, "product", None)}


def _st_ensure(uid: str, p: Any) -> Optional[str]:
    """The trade's row, created from the position if nothing has reported it yet."""
    filled = _pos(getattr(p, "fill_price", 0)) is not None
    return tj.record_entry(uid=uid, strategy=SUPERTREND, symbol=p.symbol, key=_st_key(p), create_only=True,
                           status="OPEN" if filled else "PENDING", extra=_st_extra(p),
                           **_st_fields(p, filled=filled))


@_safe
def st_submitted(uid: str, row: Any, order_id: str, payload: Dict[str, Any], account_id: str,
                 lots: int, signal_id: str = "") -> None:
    """The entry order was accepted: the trade exists (PENDING) with the signal that caused it."""
    symbol = str(payload.get("symbol") or "")
    ctx = _st_context(row, payload)
    side = "SELL" if payload.get("direction") == "short" else "BUY"
    qty = _int(payload.get("quantity"))
    sig_ts = _int(getattr(row, "timestamp_ms", None))
    tid = tj.record_entry(
        uid=uid, strategy=SUPERTREND, symbol=symbol, key=order_id, status="PENDING", account_id=account_id,
        underlying=str(payload.get("underlying") or getattr(row, "underlying", "") or ""),
        option_type=_kind_of(symbol, str(payload.get("vehicle") or "")), strike=_pos(payload.get("strike")),
        expiry=str(payload.get("expiry") or ""), side=side, lot_size=_int(payload.get("lot_size")), lots=lots,
        qty=qty, signal_id=signal_id, entry_order_id=order_id, signal_ts_ms=sig_ts,
        initial_sl=_pos(payload.get("stop_premium")), target=_pos(payload.get("target_premium")),
        signal_context=ctx,
        extra={"exchange": payload.get("exchange"), "vehicle": payload.get("vehicle"),
               "stop_mode": payload.get("stop_mode"), "exit_mode": payload.get("exit_mode"),
               "intended_entry": _pos(payload.get("entry_premium"))})
    if not tid:
        return
    scope = _scope.get()
    if scope and scope.get("uid") == uid:
        scope["trade_id"] = tid
    now = _now_ms()
    why = entry_reason(SUPERTREND, ctx)
    tj.record_event(trade_id=tid, event_type=tj.SIGNAL, ts_ms=min(now, (sig_ts + 3_600_000) if sig_ts else now),
                    event_key=f"signal:{signal_id or order_id}",
                    detail=f"SuperTrend signal on {payload.get('underlying') or symbol}" + (f": {why}" if why else ""),
                    data={"signal_id": signal_id})
    tj.record_event(trade_id=tid, event_type=tj.ORDER_SUBMITTED, ts_ms=now, price=_pos(payload.get("entry_premium")),
                    qty=qty, event_key=f"order:{order_id}",
                    detail=f"{'Sell' if side == 'SELL' else 'Buy'} order for {qty} {symbol} placed (#{order_id})"
                           + (f"; stop-loss {_rupees(payload.get('stop_premium'))}" if _pos(payload.get("stop_premium")) else ""))


@_safe
def st_filled(uid: str, p: Any, order_id: str = "") -> None:
    """An entry fill reached the registry (live postback, legacy postback or reconciliation)."""
    fill = _pos(getattr(p, "fill_price", 0))
    if not fill or not _int(getattr(p, "qty", 0)):
        return
    tid = tj.record_entry(uid=uid, strategy=SUPERTREND, symbol=p.symbol, key=_st_key(p), status="OPEN",
                          extra=_st_extra(p), **_st_fields(p, filled=True))
    if tid:
        qty = _int(p.qty)
        tj.record_event(trade_id=tid, event_type=tj.FILLED, price=fill, qty=qty,
                        event_key=f"fill:{order_id or p.order_id}:{qty}:{fill:.2f}",
                        detail=f"Bought {qty} {p.symbol} at {fill:.2f}")


@_safe
def st_stop(uid: str, p: Any, old_stop: float, old_gtt: int = 0) -> None:
    """``positions.update_stop`` changed the stop and/or armed a broker trigger."""
    tid = _st_ensure(uid, p)
    if not tid:
        return
    new, old = _num(getattr(p, "stop_premium", 0)) or 0.0, _num(old_stop) or 0.0
    gtt = int(getattr(p, "gtt_id", 0) or 0)
    if new > 0 and abs(new - old) >= 0.01:
        if old > 0:
            tj.record_event(trade_id=tid, event_type=tj.TSL_MOVED, price=new, event_key=f"tsl:{new:.2f}",
                            data={"from": old, "to": new, "gtt_id": gtt or None},
                            detail=f"Stop moved from {old:.2f} to {new:.2f}" + (f" (broker GTT #{gtt})" if gtt else ""))
        else:
            tj.record_event(trade_id=tid, event_type=tj.PROTECTION_PLACED, price=new, event_key=f"prot:{gtt}:{new:.2f}",
                            detail=f"Stop-loss set at {new:.2f}" + (f" (broker GTT #{gtt})" if gtt else ""))
    if gtt and gtt != int(old_gtt or 0) and new > 0 and old > 0 and abs(new - old) < 0.01:
        st_protection(uid, p)


@_safe
def st_protection(uid: str, p: Any) -> None:
    """A broker stop (GTT) was placed for the position."""
    tid = _st_ensure(uid, p)
    stop, gtt = _num(getattr(p, "stop_premium", 0)) or 0.0, int(getattr(p, "gtt_id", 0) or 0)
    if tid and stop > 0:
        tj.record_event(trade_id=tid, event_type=tj.PROTECTION_PLACED, price=stop, event_key=f"prot:{gtt}:{stop:.2f}",
                        detail=f"Broker stop placed at {stop:.2f}" + (f" (GTT #{gtt})" if gtt else ""))


@_safe
def st_exit_submitted(uid: str, p: Any, reason: str, order_id: str = "") -> None:
    tid = _st_ensure(uid, p)
    if tid:
        tj.record_event(trade_id=tid, event_type=tj.EXIT_SUBMITTED, event_key=f"exitreq:{order_id or reason}",
                        detail=f"Exit order sent: {reason}" if reason else "Exit order sent",
                        data={"reason": reason, "order_id": order_id})


@_safe
def st_partial(uid: str, p: Any, qty: int, price: float, order_id: str, reason: str = "") -> None:
    """Part of the position was sold; the trade stays open."""
    if _st_ensure(uid, p):
        tj.record_exit(uid=uid, strategy=SUPERTREND, symbol=p.symbol, key=_st_key(p), exit_price=price, qty=qty,
                       partial=True, fill_key=f"exit:{order_id}", cumulative=True, reason=reason)


@_safe
def st_closed(uid: str, p: Any, price: Optional[float], reason: str, qty: Optional[int] = None,
              order_id: str = "") -> None:
    """The position is closed. Called BEFORE the registry closes it so the order-level figures
    (id, cumulative quantity, average price) are not lost; :func:`st_closed_fallback` covers every
    other closer."""
    if not _st_ensure(uid, p):
        return
    tj.record_exit(uid=uid, strategy=SUPERTREND, symbol=p.symbol, key=_st_key(p), exit_price=_pos(price),
                   qty=qty if qty else _int(getattr(p, "qty", 0)), reason=reason, trailed=_st_trailed(p),
                   fill_key=f"exit:{order_id}" if order_id else None, cumulative=bool(order_id))


@_safe
def st_closed_fallback(uid: str, p: Any, price: Optional[float], reason: str) -> None:
    """Registry-level close (``positions.close``): journals the exit unless an order-level hook
    already did."""
    tid = tj.trade_id_for(SUPERTREND, uid, p.symbol, _st_key(p))
    if tj.trade_status(tid) == tj.CLOSED:
        return
    st_closed(uid, p, price, reason)


@_safe
def st_rejected(uid: str, p: Any, reason: str) -> None:
    tid = _st_ensure(uid, p)
    if tid:
        tj.record_event(trade_id=tid, event_type=tj.REJECTED, event_key=f"rejected:{_st_key(p)}",
                        detail=f"Entry order not filled: {reason}" if reason else "Entry order not filled",
                        data={"reason": reason})


@_safe
def st_charges(uid: str, p: Any, order_id: str, charges: float, is_entry: bool) -> None:
    """Real broker charges for one of the position's orders."""
    if _st_ensure(uid, p):
        tj.record_charges(uid=uid, strategy=SUPERTREND, symbol=p.symbol, key=_st_key(p), charges=charges,
                          order_key=f"{'entry' if is_entry else 'exit'}:{order_id}", source="broker")


# --------------------------------------------------------------------------
# Gamma Unwind (gamma_move PositionState)
# --------------------------------------------------------------------------

def _gm_symbol(pos: Any) -> str:
    return str(pos.instrument.tradingsymbol)


def _gm_fields(pos: Any, *, filled: bool) -> Dict[str, Any]:
    inst = pos.instrument
    price = _pos(pos.effective_entry) if filled else None
    qty = _int(pos.quantity)
    return dict(
        account_id=str(pos.account_id or ""), underlying=str(pos.underlying or ""),
        option_type=str(inst.option_type or ""), strike=_pos(inst.strike), expiry=str(inst.expiry or ""), side="BUY",
        lot_size=_int(inst.lot_size), lots=_int(pos.lots), qty=qty, signal_id=str(pos.signal_id or ""),
        entry_order_id=str(pos.order_id or ""), entry_price=price, initial_sl=_pos(pos.stop), target=_pos(pos.t2),
        capital_used=round(price * qty, 2) if price and qty else None)


def _gm_extra(pos: Any) -> Dict[str, Any]:
    return {"exchange": pos.instrument.exchange, "t1": _pos(pos.t1), "t2": _pos(pos.t2), "hold": pos.hold,
            "stop_mode": pos.stop_mode, "product": pos.product, "high_water": _pos(pos.high_water)}


def _gm_context(sig: Any, hold: str = "") -> Dict[str, Any]:
    c = getattr(sig, "contract", None)
    g = getattr(sig, "gates", None)
    ctx: Dict[str, Any] = {
        "tier": str(getattr(sig, "tier", "") or ""), "reason": getattr(sig, "reason", None),
        "oi_drop_pct": _num(getattr(sig, "oi_drop_pct", None)), "vol_ratio": _num(getattr(sig, "vol_ratio", None)),
        "strong": getattr(sig, "strong", None), "base_low": _num(getattr(sig, "base_low", None)),
        "base_high": _num(getattr(sig, "base_high", None)), "signal_ltp": _num(getattr(sig, "ltp", None)),
        "zone_edge": _num(getattr(c, "zone_edge", None)), "dte": getattr(c, "dte", None),
        "list_name": getattr(c, "list_name", None), "tag": getattr(c, "tag", None), "hold": hold or None,
        "gates_passed": list(getattr(g, "passes", ()) or ()), "gate_reasons": list(getattr(g, "gate_reasons", ()) or ()),
        "f1": getattr(g, "f1", None), "f2": getattr(g, "f2", None), "unwind": getattr(g, "unwind", None),
        "zone_gap_pct": _num(getattr(g, "zone_gap_pct", None)), "zone_limit_pct": _num(getattr(g, "zone_limit_pct", None)),
        "quadrant": getattr(g, "quadrant", None), "warnings": list(getattr(sig, "warnings", ()) or ()),
    }
    return {k: v for k, v in ctx.items() if v not in (None, [], "")}


@_safe
def gm_submitted(uid: str, sig: Any, pos: Any, order_id: str, limit: float) -> None:
    """The E1 entry order was accepted: the trade exists (PENDING) with the signal behind it."""
    ctx = _gm_context(sig, getattr(pos, "hold", ""))
    tid = tj.record_entry(uid=uid, strategy=GAMMA, symbol=_gm_symbol(pos), key=pos.signal_id, status="PENDING",
                          signal_ts_ms=_int(sig.at_ms), signal_context=ctx, extra=_gm_extra(pos),
                          **_gm_fields(pos, filled=False))
    if not tid:
        return
    tj.record_event(trade_id=tid, event_type=tj.SIGNAL, ts_ms=_int(sig.at_ms), event_key=f"signal:{pos.signal_id}",
                    detail=f"Gamma Unwind E1 ENTRY signal on {_gm_symbol(pos)}"
                           + (f": {sig.reason}" if getattr(sig, "reason", "") else ""), data={"signal_id": pos.signal_id})
    tj.record_event(trade_id=tid, event_type=tj.ORDER_SUBMITTED, price=_pos(limit), qty=_int(pos.quantity),
                    event_key=f"order:{order_id}",
                    detail=f"Buy limit order for {_int(pos.quantity)} {_gm_symbol(pos)} at {_rupees(limit)} placed (#{order_id})"
                           + (f"; stop-loss {_rupees(pos.stop)}" if _pos(pos.stop) else ""))


def _gm_ensure(uid: str, pos: Any) -> Optional[str]:
    filled = getattr(pos, "status", "") == "open"
    return tj.record_entry(uid=uid, strategy=GAMMA, symbol=_gm_symbol(pos), key=pos.signal_id, create_only=True,
                           status="OPEN" if filled else "PENDING", extra=_gm_extra(pos), **_gm_fields(pos, filled=filled))


@_safe
def gm_filled(uid: str, pos: Any) -> None:
    price = _pos(pos.effective_entry)
    if not price or not _int(pos.quantity):
        return
    tid = tj.record_entry(uid=uid, strategy=GAMMA, symbol=_gm_symbol(pos), key=pos.signal_id, status="OPEN",
                          extra=_gm_extra(pos), **_gm_fields(pos, filled=True))
    if tid:
        qty = _int(pos.quantity)
        tj.record_event(trade_id=tid, event_type=tj.FILLED, price=price, qty=qty,
                        event_key=f"fill:{pos.order_id or pos.entered_ms}:{qty}:{price:.2f}",
                        detail=f"Bought {qty} {_gm_symbol(pos)} at {price:.2f}")


@_safe
def gm_protection(uid: str, pos: Any, old_stop: float = 0.0) -> None:
    """The broker stop was placed (``old_stop`` 0) or moved up to ``pos.stop``."""
    tid = _gm_ensure(uid, pos)
    new, old, gtt = _num(pos.stop) or 0.0, _num(old_stop) or 0.0, int(pos.gtt_id or 0)
    if not tid or new <= 0:
        return
    if old > 0 and new > old + 0.005:
        tj.record_event(trade_id=tid, event_type=tj.TSL_MOVED, price=new, event_key=f"tsl:{new:.2f}",
                        data={"from": old, "to": new, "gtt_id": gtt or None},
                        detail=f"Stop moved from {old:.2f} to {new:.2f} (broker GTT #{gtt})")
    else:
        tj.record_event(trade_id=tid, event_type=tj.PROTECTION_PLACED, price=new, event_key=f"prot:{gtt}:{new:.2f}",
                        detail=f"Broker stop placed at {new:.2f} (GTT #{gtt})")


@_safe
def gm_t1(uid: str, pos: Any, old_stop: float) -> None:
    """T1 reached: the stop moves to the entry price and the trade is now in its trailing phase."""
    tid = _gm_ensure(uid, pos)
    if not tid:
        return
    tj.record_event(trade_id=tid, event_type=tj.TARGET_HIT, price=_pos(pos.t1), event_key=f"t1:{pos.signal_id}",
                    detail=f"First target {_rupees(pos.t1)} reached; the stop moves to the entry price",
                    data={"target": "T1", "next_target": _pos(pos.t2)})
    new, old = _num(pos.stop) or 0.0, _num(old_stop) or 0.0
    if new > 0 and new > old + 0.005:
        tj.record_event(trade_id=tid, event_type=tj.TSL_MOVED, price=new, event_key=f"tsl:{new:.2f}",
                        data={"from": old or None, "to": new}, detail=f"Stop moved to {new:.2f} (entry price)")


@_safe
def gm_exit_submitted(uid: str, pos: Any, reason: str) -> None:
    tid = _gm_ensure(uid, pos)
    if tid:
        tj.record_event(trade_id=tid, event_type=tj.EXIT_SUBMITTED,
                        event_key=f"exitreq:{pos.exit_tag or pos.exit_order_id or reason}",
                        detail=f"Exit order sent: {reason}", data={"reason": reason, "tag": pos.exit_tag})


@_safe
def gm_closed(uid: str, pos: Any, reason: str, exit_price: float = 0.0, quantity_closed: int = 0,
              pnl_inr: Optional[float] = None) -> None:
    """Every Gamma exit funnels through ``positions_store.close``: ``pnl_inr`` is the whole trade's
    gross P&L (earlier partial exits included), so it is passed as the override."""
    if not _gm_ensure(uid, pos):
        return
    oid = str(pos.exit_order_id or "")
    qty = _int(quantity_closed) or _int(pos.quantity)
    tj.record_exit(
        uid=uid, strategy=GAMMA, symbol=_gm_symbol(pos), key=pos.signal_id, exit_price=_pos(exit_price), qty=qty,
        reason=reason, gross_pnl=_num(pnl_inr),
        fill_key=f"exit:{oid}" if oid and oid not in ("submitting", "unknown") else None,
        extra={"high_water": _pos(pos.high_water), "pnl_reconciliation": bool(pos.pnl_reconciliation_required)})


@_safe
def gm_rejected(uid: str, pos: Any, reason: str) -> None:
    tid = _gm_ensure(uid, pos)
    if tid:
        tj.record_event(trade_id=tid, event_type=tj.REJECTED, event_key=f"rejected:{pos.signal_id}",
                        detail=f"Entry order not filled: {reason}" if reason else "Entry order not filled",
                        data={"reason": reason})


@_safe
def gm_skip(uid: str, sig: Any, code: str = "", message: str = "") -> None:
    """A Gamma signal that was not traded: ``code``/``message`` are what ``arm()`` answered."""
    text = str(message or "")
    if not code:
        code, _ = classify_skip(GAMMA, text)
        if re.search(r"switched off", text, re.I):
            code = "disabled"
        elif re.search(r"alert-only", text, re.I):
            code = "tier_alert_only"
        elif re.search(r"already holding", text, re.I):
            code = "already_holding"
    if code in MODE_CODES:
        return
    c = getattr(sig, "contract", None)
    inst = getattr(c, "instrument", None)
    tj.record_skip(uid=uid, strategy=GAMMA, code=code, reason=reason_for_code(code, text),
                   symbol=str(getattr(inst, "tradingsymbol", "") or ""), underlying=str(getattr(c, "underlying", "") or ""),
                   signal_id=str(getattr(sig, "id", "") or ""),
                   context={**_gm_context(sig), "detail": text})


# --------------------------------------------------------------------------
# Snapback (LivePosition, plans and the dispatcher's own decisions)
# --------------------------------------------------------------------------

_sb_seen: Dict[str, tuple] = {}
_sb_prev_stop: Dict[str, float] = {}
_SB_LIVE = frozenset({"ENTERING", "OPEN", "EXITING", "RECONCILING"})


def _sb_trailed(pos: Any) -> bool:
    tick = _num(getattr(pos, "tick_size", 0.05)) or 0.05
    initial = _num(pos.stop_premium) or 0.0
    return bool(getattr(pos, "is_runner", False)
                or (initial > 0 and ((_num(pos.gtt_stop) or 0) > initial + tick / 2
                                     or (_num(pos.desired_stop) or 0) > initial + tick / 2)))


def _sb_rules_context(rules: Dict[str, Any]) -> Dict[str, Any]:
    keep = ("lane", "premium_stop_pct", "hold_days", "runner_mult", "runner_trail_pct", "expiry_buffer_calendar_days",
            "stop_points", "target_premium", "lock_points", "trail_points", "max_hold_bars", "timeframe_minutes")
    return {k: rules[k] for k in keep if rules.get(k) not in (None, "")}


def _sb_plan_context(plan: Dict[str, Any]) -> Dict[str, Any]:
    hedge = None
    if plan.get("futures_symbol"):
        hedge = {"symbol": plan.get("futures_symbol"), "qty": _int(plan.get("target_futures_quantity")),
                 "side": plan.get("hedge_side"), "beta": plan.get("hedge_beta"), "delta": plan.get("hedge_delta"),
                 "error_pct": plan.get("hedge_error_pct")}
    ctx = {"lane": plan.get("lane"), "side": plan.get("signal_side"), "strength": plan.get("strength"),
           "hedge": hedge, "rules": _sb_rules_context(dict(plan.get("rules") or {})),
           "entry_quote": plan.get("entry_quote"), "limit_price": _num(plan.get("limit_price")),
           "risk_amount": _num(plan.get("risk_amount")), "cash_required": _num(plan.get("cash_required")),
           "margin_required": _num(plan.get("margin_required"))}
    return {k: v for k, v in ctx.items() if v not in (None, {}, [])}


def _sb_row_context(row: Dict[str, Any]) -> Dict[str, Any]:
    ctx = {"side": row.get("side"), "direction": row.get("direction"), "strength": row.get("strength"),
           "stretch": _num(row.get("stretch")), "distance_pct": _num(row.get("distance_pct")),
           "mean_target": _num(row.get("mean_target")), "spot": _num(row.get("spot")),
           "assumed_iv_pct": _num(row.get("assumed_iv_pct")), "hold_days": row.get("hold_days"),
           "origin": row.get("origin"), "signal_ts_ms": _int(row.get("timestamp_ms"))}
    return {k: v for k, v in ctx.items() if v is not None}


def _sb_position_context(pos: Any) -> Dict[str, Any]:
    """What the position row alone says about why it exists (the board row and the plan add more
    when the dispatcher originated it: :func:`sb_executed`)."""
    hedge = None
    if pos.hedge_symbol and _int(pos.hedge_qty):
        hedge = {"symbol": pos.hedge_symbol, "qty": _int(pos.hedge_qty), "side": str(pos.hedge_side or "").upper() or None}
    ctx = {"lane": pos.lane, "side": pos.side, "hedge": hedge, "rules": _sb_rules_context(dict(pos.rules or {}))}
    return {k: v for k, v in ctx.items() if v not in (None, {}, "")}


@_safe
def sb_sync(pos: Any) -> None:
    """Called after every write to a Snapback position: derive the journal from its current state.

    Snapback's position row carries everything (fill, hedge, stop, exit) and every write goes
    through one store, so the journal is a function of that row: only the first sight, a changed
    fingerprint or the close writes anything, and every event is keyed by the fact it records.
    """
    key = str(pos.position_id)
    uid = str(pos.uid)
    fp = (pos.status, pos.option_qty, pos.option_entry_price, pos.hedge_qty, pos.hedge_entry_price,
          pos.protection_state, pos.gtt_id, pos.gtt_stop, pos.is_runner, round(float(pos.peak_close or 0), 2),
          pos.exit_reason, pos.option_exit_qty, pos.option_exit_value, pos.hedge_exit_qty, pos.realised_pnl,
          pos.closed_ms, tuple(pos.flags or ()))
    if _sb_seen.get(key) == fp:
        return
    if len(_sb_seen) > 5000:
        _sb_seen.clear()
    symbol = str(pos.option_symbol)
    status = str(pos.status)
    if status not in _SB_LIVE and status != "CLOSED":
        return
    qty, entry = _int(pos.option_qty), _pos(pos.option_entry_price)
    if not qty or not entry:
        return
    rules = dict(pos.rules or {})
    hedge = None
    if pos.hedge_symbol and _int(pos.hedge_qty):
        hedge = {"symbol": pos.hedge_symbol, "side": str(pos.hedge_side or "").upper() or None,
                 "qty": _int(pos.hedge_qty), "entry_price": _pos(pos.hedge_entry_price),
                 "exit_price": _pos(pos.hedge_exit_price)}
    ctx = _sb_position_context(pos)
    extra = {"exchange": pos.option_exchange, "lane": pos.lane, "plan_id": key, "hedge": hedge,
             "high_water": _pos(pos.high_water), "stop_mode": "broker", "product": pos.product}
    tid = tj.record_entry(
        uid=uid, strategy=SNAPBACK, symbol=symbol, key=key, status="OPEN", account_id=str(pos.account_id or ""),
        underlying=str(pos.underlying or ""), option_type=str(pos.option_type or ""), expiry=str(pos.option_expiry or ""),
        side="BUY", lot_size=_int(pos.lot_size), lots=_lots(qty, pos.lot_size), qty=qty,
        signal_id=str(pos.signal_id or ""), entry_order_id=str(pos.option_entry_order_id or ""),
        entry_price=entry, entry_ts_ms=_int(pos.opened_ms), initial_sl=_pos(pos.stop_premium),
        target=_pos(rules.get("target_premium")), signal_context=ctx or None, extra={k: v for k, v in extra.items() if v is not None})
    if not tid:
        return
    ev = lambda etype, ekey, detail, **kw: tj.record_event(  # noqa: E731
        trade_id=tid, event_type=etype, event_key=ekey, detail=detail, **kw)
    ev(tj.FILLED, f"fill:{pos.option_entry_order_id}:{qty}:{entry:.2f}", f"Bought {qty} {symbol} at {entry:.2f}",
       ts_ms=_int(pos.opened_ms), price=entry, qty=qty)
    if hedge and hedge["entry_price"]:
        ev(tj.HEDGE_FILLED, f"hedge:{pos.hedge_entry_order_id}:{hedge['qty']}",
           f"Hedge: {hedge['side'] or ''} {hedge['qty']} {pos.hedge_symbol} at {hedge['entry_price']:.2f}".replace("  ", " "),
           price=hedge["entry_price"], qty=hedge["qty"])
    stop, prev = _num(pos.gtt_stop) or 0.0, _sb_prev_stop.get(key, 0.0)
    if str(pos.protection_state) == "ACTIVE" and pos.gtt_id and stop > 0:
        initial = _num(pos.stop_premium) or 0.0
        if prev > 0 and stop > prev + 1e-9:
            ev(tj.TSL_MOVED, f"tsl:{stop:.2f}", f"Stop moved from {prev:.2f} to {stop:.2f} (broker GTT #{pos.gtt_id})",
               price=stop, data={"from": prev, "to": stop, "gtt_id": int(pos.gtt_id)})
        elif prev <= 0 and initial > 0 and stop > initial + 1e-9:
            ev(tj.PROTECTION_PLACED, f"prot:{pos.gtt_id}:{initial:.2f}", f"Broker stop placed at {initial:.2f}", price=initial)
            ev(tj.TSL_MOVED, f"tsl:{stop:.2f}", f"Stop moved from {initial:.2f} to {stop:.2f} (broker GTT #{pos.gtt_id})",
               price=stop, data={"from": initial, "to": stop, "gtt_id": int(pos.gtt_id)})
        else:
            ev(tj.PROTECTION_PLACED, f"prot:{pos.gtt_id}:{stop:.2f}",
               f"Broker stop placed at {stop:.2f} (GTT #{pos.gtt_id})", price=stop)
        _sb_prev_stop[key] = max(stop, prev)
    if getattr(pos, "is_runner", False):
        trail_pct = _num(rules.get("runner_trail_pct")) or 25.0
        peak = _num(pos.peak_close) or 0.0
        level = round(peak * (1.0 - trail_pct / 100.0), 2) if peak else 0.0
        ev(tj.NOTE, f"runner:{key}", f"Runner armed: the exit now trails {trail_pct:g}% below the closing peak",
           data={"runner": True})
        if level > 0:
            ev(tj.TSL_MOVED, f"tsl:{level:.2f}", f"Trailing level now {level:.2f} (peak {peak:.2f})", price=level,
               data={"from": None, "to": level, "runner": True})
    if status == "EXITING":
        ev(tj.EXIT_SUBMITTED, f"exitreq:{key}", f"Exit started: {str(pos.exit_reason).replace('_', ' ').lower()}",
           ts_ms=_int(pos.exit_started_ms) or None, data={"reason": pos.exit_reason})
    if status == "RECONCILING":
        note = str(pos.note or "needs reconciliation")
        ev(tj.NOTE, f"recon:{note}", f"Needs reconciliation: {note.replace('_', ' ')}", data={"flags": list(pos.flags or [])})
    if status == "CLOSED":
        priced = max(0, int(pos.option_exit_qty or 0) - int(pos.option_exit_unknown_qty or 0))
        price = _pos(pos.option_exit_price)
        tj.record_exit(
            uid=uid, strategy=SNAPBACK, symbol=symbol, key=key, exit_price=price, qty=priced or None,
            ts_ms=_int(pos.closed_ms) or None, reason=str(pos.exit_reason or ""), trailed=_sb_trailed(pos),
            gross_pnl=_num(pos.realised_pnl) if price else None, fill_key=f"exit:{key}",
            extra={"hedge": hedge, "high_water": _pos(pos.high_water),
                   "exit_unknown_qty": int(pos.option_exit_unknown_qty or 0), "flags": list(pos.flags or [])})
        _sb_prev_stop.pop(key, None)
    _sb_seen[key] = fp


def _sb_contract(row: Dict[str, Any]) -> Dict[str, Any]:
    return dict(row.get("contract") or {})


@_safe
def sb_executed(uid: str, row: Dict[str, Any], plan: Dict[str, Any], status: str, reasons: List[str],
                manual: bool = False) -> None:
    """The dispatcher published, armed and executed a plan: attach the signal that caused it.

    A position that exists was already journalled from its own row (:func:`sb_sync`); this adds the
    signal context only the board row has. A broker REJECTION leaves a rejected trade row; a
    pre-flight REFUSAL never sent an order and is a skip instead.
    """
    plan = dict(plan or {})
    key = str(plan.get("plan_id") or "")
    symbol = str(plan.get("option_symbol") or _sb_contract(row).get("symbol") or "")
    if not key or not symbol:
        return
    ctx = {**_sb_plan_context(plan), **_sb_row_context(row)}
    exposure = status in ("OPEN", "HEDGE_FAILED", "RECONCILING", "PROTECTION_FAILED")
    if not exposure and status != "REJECTED":
        sb_refused(uid, row, (reasons or [status or "refused"])[0], "", manual)
        return
    contract = _sb_contract(row)
    tid = tj.record_entry(
        uid=uid, strategy=SNAPBACK, symbol=symbol, key=key, status=None if exposure else "PENDING",
        account_id=str(plan.get("account_id") or ""), underlying=str(plan.get("underlying") or row.get("symbol") or ""),
        option_type=str(plan.get("option_type") or contract.get("option_type") or ""),
        strike=_pos(contract.get("strike")), expiry=str(plan.get("option_expiry") or contract.get("expiry") or ""),
        side="BUY", lot_size=_int(plan.get("lot_size")), lots=_int(plan.get("lots")), qty=_int(plan.get("option_quantity")),
        signal_id=str(plan.get("opportunity_id") or row.get("signal_id") or ""),
        signal_ts_ms=_int(plan.get("signal_ts_ms")) or _int(row.get("timestamp_ms")),
        initial_sl=_pos(plan.get("stop_premium")), target=_pos(plan.get("target_premium")),
        capital_used=round((_num(plan.get("cash_required")) or 0) + (_num(plan.get("margin_required")) or 0), 2) or None,
        signal_context=ctx, extra={"exchange": plan.get("option_exchange"), "lane": plan.get("lane"), "plan_id": key})
    if not tid:
        return
    why = re.sub(r"^a Snapback ", "", entry_reason(SNAPBACK, ctx) or "")
    under = plan.get("underlying") or row.get("symbol")
    tj.record_event(trade_id=tid, event_type=tj.SIGNAL, ts_ms=_int(plan.get("signal_ts_ms")) or _int(row.get("timestamp_ms")),
                    event_key=f"signal:{plan.get('opportunity_id') or key}",
                    detail=f"Snapback signal on {under}" + (f": {why}" if why else ""),
                    data={"signal_id": plan.get("opportunity_id")})
    if status == "REJECTED":
        why = str((reasons or [""])[0])
        tj.record_event(trade_id=tid, event_type=tj.REJECTED, event_key=f"rejected:{key}",
                        detail=f"Entry order rejected: {why}" if why else "Entry order rejected", data={"reason": why})


@_safe
def sb_refused(uid: str, row: Dict[str, Any], reason: str, detail: str = "", manual: bool = False) -> None:
    """The dispatcher did not trade an armed board row (plan refusal or pre-flight refusal)."""
    code = str(reason or "").strip()
    if manual or not code or code.startswith("plan_exists") or code in MODE_CODES or code == "ALREADY_CONSUMED":
        return
    contract = _sb_contract(row)
    tj.record_skip(uid=uid, strategy=SNAPBACK, code=code, reason=reason_for_code(code), symbol=str(contract.get("symbol") or ""),
                   underlying=str(row.get("symbol") or ""), signal_id=str(row.get("signal_id") or ""),
                   context={**_sb_row_context(row), "detail": detail} if detail else _sb_row_context(row))


@_safe
def sb_blocked(uid: str, rows: List[Dict[str, Any]], blockers: List[str]) -> None:
    """A cycle with armed board rows was stopped by origination blockers: say so, per signal."""
    codes = [b for b in blockers if b not in MODE_CODES]
    if not codes:
        return
    for row in rows:
        if row.get("state") != "armed":
            continue
        contract = _sb_contract(row)
        tj.record_skip(uid=uid, strategy=SNAPBACK, code=codes[0], reason=reason_for_code(codes[0]),
                       symbol=str(contract.get("symbol") or ""), underlying=str(row.get("symbol") or ""),
                       signal_id=str(row.get("signal_id") or ""), context={**_sb_row_context(row), "blockers": codes})
