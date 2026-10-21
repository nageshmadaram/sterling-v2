"""Plain-English side of the Trade Journal: no database, no broker, no clock.

Three jobs, all deterministic functions of the fields stored on a journal row:

* :func:`canonical_exit_reason` folds every strategy's own exit strings into one small
  vocabulary, so "why do trades end" can be counted across strategies;
* :func:`build_narrative` tells one trade as a short story a family member can read;
* :func:`classify_skip` turns a refusal (a free-text log line, a blocker code) into a stable
  code plus a sentence, so "why didn't it trade" can be answered and counted.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

IST = timezone(timedelta(hours=5, minutes=30))

STRATEGY_LABELS = {"supertrend": "Supertrend", "snapback": "Snapback", "gamma": "Gamma Unwind"}

# --------------------------------------------------------------------------
# Exit reasons
# --------------------------------------------------------------------------

#: The one vocabulary every journal row's ``exit_reason`` is drawn from.
CANONICAL_EXIT_REASONS = (
    "SL", "TSL", "TARGET", "EOD", "TIME_STOP", "SIGNAL_EXIT", "MANUAL", "SQUARE_OFF",
    "EXPIRY", "HEDGE", "BROKER_STOP", "RECONCILED", "OTHER",
)

EXIT_REASON_LABELS = {
    "SL": "Stop-loss hit",
    "TSL": "Trailing stop hit",
    "TARGET": "Target reached",
    "EOD": "End-of-day exit",
    "TIME_STOP": "Time stop",
    "SIGNAL_EXIT": "Exit signal",
    "MANUAL": "Manual exit",
    "SQUARE_OFF": "Square-off",
    "EXPIRY": "Expiry exit",
    "HEDGE": "Hedge problem",
    "BROKER_STOP": "Broker stop fired",
    "RECONCILED": "Closed outside Sterling",
    "OTHER": "Other",
}

_EXIT_PHRASE = {
    "SL": "when the stop-loss was hit",
    "TSL": "when the trailing stop was hit",
    "TARGET": "on target",
    "EOD": "at the end-of-day exit",
    "TIME_STOP": "on the time stop",
    "SIGNAL_EXIT": "when the exit signal fired",
    "MANUAL": "on a manual exit",
    "SQUARE_OFF": "on a square-off",
    "EXPIRY": "ahead of expiry",
    "HEDGE": "after a hedge problem",
    "BROKER_STOP": "when the broker's stop fired",
    "RECONCILED": "after it was closed outside Sterling",
    "OTHER": "",
}

_STOP_WORDS = re.compile(r"\b(?:sl|stop|gtt)\b")


def canonical_exit_reason(strategy: str, raw: object, *, trailed: Optional[bool] = None) -> str:
    """Map one strategy's own exit string onto :data:`CANONICAL_EXIT_REASONS`.

    ``trailed`` says whether the stop had been raised above its initial level when it fired:
    a stop that never moved is ``SL``, one that had trailed is ``TSL``. When that is not
    known a broker-fired stop stays ``BROKER_STOP`` rather than guessing.
    """
    text = str(raw or "").strip()
    if not text:
        return "OTHER"
    upper = text.upper()
    if upper in CANONICAL_EXIT_REASONS and upper not in ("SL", "BROKER_STOP"):
        return upper
    low = text.lower().replace("_", " ")

    if "expiry" in low:
        return "EXPIRY"
    if "hedge" in low:
        return "HEDGE"
    if "reconcil" in low or "not at broker" in low or "not at the broker" in low or "vanished" in low:
        return "RECONCILED"
    if "session square" in low or "end of day" in low or "eod" in low.split():
        return "EOD"
    if low.startswith("flatten") or "emergency" in low or "square off" in low or "square-off" in low \
            or "kill switch" in low or low.strip() == "operator":
        return "SQUARE_OFF"
    if "manual" in low:
        return "MANUAL"
    if "red count" in low or "signal" in low or "counter" in low:
        return "SIGNAL_EXIT"
    if "target" in low or low.strip() in ("t2", "t1"):
        return "TARGET"
    if "runner trail" in low or low.strip() == "trail":
        return "TSL"
    if "time" in low or "horizon" in low or "day5" in low or "held" in low or "hold" in low or "session_limit" in low:
        return "TIME_STOP"
    if low.strip() == "gap":                           # Gamma overnight: closed before a weekend/holiday
        return "EOD"
    if "trail" in low:
        return "SL" if trailed is False else "TSL"
    if re.match(r"^sl\b", low):                      # Gamma: "SL", "SL (broker stop; fill unconfirmed)"
        return "TSL" if trailed else "SL"
    if _STOP_WORDS.search(low) or upper == "SL":
        if trailed is True:
            return "TSL"
        if trailed is False:
            return "SL"
        return "BROKER_STOP" if ("broker" in low or "gtt" in low or upper == "BROKER_STOP") else "SL"
    return "OTHER"


# --------------------------------------------------------------------------
# Number / time formatting
# --------------------------------------------------------------------------

def _num(value: object) -> Optional[float]:
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return out if out == out and out not in (float("inf"), float("-inf")) else None


def _px(value: object) -> str:
    n = _num(value)
    return "?" if n is None else f"{n:.2f}"


def _money(value: object, *, signed: bool = True) -> str:
    n = _num(value)
    if n is None:
        return "?"
    if round(n) == 0:
        n = 0.0
    sign = ("-" if n < 0 else ("+" if signed else ""))
    return f"{sign}₹{abs(n):,.0f}"


def _ist(ts_ms: object) -> Optional[datetime]:
    n = _num(ts_ms)
    if not n or n <= 0:
        return None
    return datetime.fromtimestamp(n / 1000.0, tz=IST)


def _when(ts_ms: object, ref_ms: object = None) -> str:
    """``11:20`` on the entry day, ``14 Oct 11:20`` on any other."""
    t = _ist(ts_ms)
    if t is None:
        return ""
    ref = _ist(ref_ms)
    if ref is not None and ref.date() != t.date():
        return f"{t.day} {t.strftime('%b')} {t.strftime('%H:%M')}"
    return t.strftime("%H:%M")


def _int(value: object) -> Optional[int]:
    n = _num(value)
    return None if n is None else int(round(n))


# --------------------------------------------------------------------------
# Narrative
# --------------------------------------------------------------------------

def _instrument(t: Dict[str, Any]) -> str:
    under = str(t.get("underlying") or "").strip()
    kind = str(t.get("option_type") or "").upper()
    strike = _num(t.get("strike"))
    symbol = str(t.get("symbol") or "").strip()
    if kind in ("CE", "PE") and under and strike:
        return f"{under} {strike:g} {kind}"
    if kind == "FUT" and under:
        return f"{under} futures"
    return symbol or under or "the contract"


def _size(t: Dict[str, Any]) -> str:
    lots = _int(t.get("lots"))
    qty = _int(t.get("qty"))
    lot_size = _int(t.get("lot_size"))
    if not lots and qty and lot_size:
        lots = max(1, qty // lot_size)
    if lots:
        return f"{lots} lot" + ("" if lots == 1 else "s")
    return f"{qty} units" if qty else "a position"


def _supertrend_reason(ctx: Dict[str, Any]) -> str:
    bull = str(ctx.get("regime") or "").upper() == "BULL" or str(ctx.get("direction") or "").lower() in (
        "long", "bull", "bullish")
    bear = str(ctx.get("regime") or "").upper() == "BEAR" or str(ctx.get("direction") or "").lower() in (
        "short", "bear", "bearish")
    word = "bullish" if bull and not bear else ("bearish" if bear and not bull else "")
    aligned = _int(ctx.get("aligned"))
    own = " on the option's own chart" if str(ctx.get("source") or "") == "derivatives" else ""
    if aligned:
        base = f"{aligned} aligned {word + ' ' if word else ''}SuperTrends{own}"
    else:
        base = f"a {word + ' ' if word else ''}SuperTrend signal{own}"
    bits = []
    adx = _num(ctx.get("adx"))
    if adx is not None:
        bits.append(f"ADX {adx:.0f}")
    score = _num(ctx.get("score"))
    if score is not None:
        bits.append(f"score {score:.0f}")
    return base + (f" ({', '.join(bits)})" if bits else "")


def _gamma_reason(ctx: Dict[str, Any]) -> str:
    tier = str(ctx.get("tier") or "ENTRY").upper()
    label = "E1 entry" if tier in ("ENTRY", "E1") else f"{tier} alert"
    bits = []
    oi = _num(ctx.get("oi_drop_pct"))
    if oi is not None:
        bits.append(f"open interest fell {oi:.1f}%")
    vol = _num(ctx.get("vol_ratio"))
    if vol is not None:
        bits.append(f"volume {vol:.1f}x average")
    zone = _num(ctx.get("zone_edge"))
    if zone:
        bits.append(f"zone edge {zone:g}")
    gates = [str(g) for g in (ctx.get("gates_passed") or []) if str(g)]
    if gates:
        bits.append("gates passed: " + ", ".join(gates))
    hold = str(ctx.get("hold") or "")
    if hold:
        bits.append(f"{hold} hold")
    return f"a Gamma Unwind {label} signal" + (f" ({'; '.join(bits)})" if bits else "")


def _snapback_reason(ctx: Dict[str, Any]) -> str:
    lane = str(ctx.get("lane") or "").lower()
    side = str(ctx.get("side") or "")
    fade = {"fade_up": "fading an up-move", "fade_down": "fading a down-move"}.get(side, "")
    bits = []
    strength = str(ctx.get("strength") or "")
    if strength:
        bits.append(strength)
    stretch = _num(ctx.get("stretch"))
    if stretch is not None:
        bits.append(f"stretch {stretch:g}")
    dist = _num(ctx.get("distance_pct"))
    if dist is not None:
        bits.append(f"{dist:g}% from the mean")
    out = f"a Snapback {lane + ' ' if lane else ''}signal" + (f" {fade}" if fade else "")
    if bits:
        out += f" ({', '.join(bits)})"
    hedge = ctx.get("hedge") or {}
    hedge_symbol = str(hedge.get("symbol") or "")
    if hedge_symbol:
        qty = _int(hedge.get("qty"))
        out += f", hedged with {hedge_symbol}" + (f" x{qty}" if qty else "")
    return out


_REASONS = {"supertrend": _supertrend_reason, "gamma": _gamma_reason, "snapback": _snapback_reason}


def entry_reason(strategy: str, ctx: Dict[str, Any]) -> str:
    """The "on ..." clause: what made the strategy enter. Empty when nothing was recorded."""
    if not ctx:
        return ""
    fn = _REASONS.get(strategy)
    try:
        return fn(ctx) if fn else ""
    except Exception:  # noqa: BLE001 - a malformed context must not lose the whole story
        return ""


def _levels_sentence(t: Dict[str, Any]) -> str:
    sl, target = _num(t.get("initial_sl")), _num(t.get("target"))
    if sl and target:
        return f"Stop-loss {_px(sl)}, target {_px(target)}."
    if sl:
        return f"Stop-loss {_px(sl)}, no fixed target."
    if target:
        return f"Target {_px(target)}, no stop-loss recorded."
    return ""


def _stop_sentence(t: Dict[str, Any]) -> str:
    moves = [m for m in (t.get("tsl_history") or []) if _num((m or {}).get("to"))]
    if not moves:
        return ""
    last = moves[-1]
    when = _when(last.get("ts_ms"), t.get("entry_ts_ms"))
    at = f" at {when}" if when else ""
    if len(moves) == 1:
        return f"Stop was raised to {_px(last['to'])}{at}."
    return f"Stop was raised {len(moves)} times, last to {_px(last['to'])}{at}."


def _exit_sentence(t: Dict[str, Any]) -> str:
    status = str(t.get("status") or "").upper()
    if status == "PENDING":
        return "Order placed; waiting for the fill."
    if status == "REJECTED":
        note = str((t.get("extra") or {}).get("reject_reason") or "").strip()
        return "The order was not filled" + (f" ({note})." if note else ".")
    if status != "CLOSED":
        return "Still open."
    reason = str(t.get("exit_reason") or "OTHER")
    phrase = _EXIT_PHRASE.get(reason, "")
    side = str(t.get("side") or "BUY").upper()
    verb = "Sold" if side == "BUY" else "Bought back"
    exit_price = _num(t.get("exit_price"))
    net, gross = _num(t.get("net_pnl")), _num(t.get("gross_pnl"))
    pnl_value = net if net is not None else gross
    src = str(t.get("charges_source") or "")
    if net is not None and src:
        pnl = f"{_money(net)} after {'estimated ' if src == 'estimate' else ''}charges"
    elif pnl_value is not None:
        pnl = f"{_money(pnl_value)} before charges"
    else:
        pnl = "P&L not recorded"
    when = ""
    entry, exit_ = _ist(t.get("entry_ts_ms")), _ist(t.get("exit_ts_ms"))
    if entry and exit_ and entry.date() != exit_.date():
        when = f" ({_when(t.get('exit_ts_ms'), t.get('entry_ts_ms'))})"
    if exit_price is None:
        return f"Closed{(' ' + phrase) if phrase else ''}; exit price not recorded. {pnl}."
    return f"{verb} at {_px(exit_price)}{(' ' + phrase) if phrase else ''}{when}, {pnl}."


def build_narrative(trade: Dict[str, Any]) -> str:
    """One trade told in plain English, from the stored fields only.

    ``Bought 1 lot NIFTY 25000 CE at 120.00 on 3 aligned bullish SuperTrends (ADX 28).
    Stop-loss 96.00, target 150.00. Stop was raised to 118.00 at 11:20.
    Sold at 150.00 on target, +₹2,250 after charges.``
    """
    t = dict(trade or {})
    side = str(t.get("side") or "BUY").upper()
    verb = "Bought" if side == "BUY" else "Sold short"
    entry = _num(t.get("entry_price"))
    ctx = t.get("signal_context") or {}
    why = entry_reason(str(t.get("strategy") or ""), ctx if isinstance(ctx, dict) else {})
    if entry is None:
        head = f"Order for {_size(t)} {_instrument(t)} placed"
    else:
        head = f"{verb} {_size(t)} {_instrument(t)} at {_px(entry)}"
    head += f" on {why}." if why else "."
    parts: List[str] = [head]
    for piece in (_levels_sentence(t), _stop_sentence(t), _exit_sentence(t)):
        if piece:
            parts.append(piece)
    return " ".join(parts)


# --------------------------------------------------------------------------
# Skips: why a signal was not traded
# --------------------------------------------------------------------------

#: Plain-English text for the codes Snapback and the shared gates report.
SKIP_REASONS: Dict[str, str] = {
    "engine_disabled": "The strategy is switched off.",
    "auto_off": "Automatic trading is off for this strategy.",
    "kill_switch": "The live-execution kill switch is engaged.",
    "no_broker_session": "Not logged in to the broker.",
    "broker_not_reconciled": "Broker positions have not been reconciled yet.",
    "hedge_incomplete": "A previous entry is still waiting for its hedge.",
    "dispatcher_not_running": "The live trading loop is not running.",
    "clock_unsynchronised": "The computer clock is not synchronised.",
    "disk_low": "The disk is nearly full.",
    "system_not_healthy": "A system health check is failing.",
    "lane_not_supported": "The selected mode has no supported trading lane.",
    "quote_not_executable": "The option had no usable two-sided quote.",
    "signal_stale": "The signal was too old to trade.",
    "stale_or_unclosed_signal": "The signal was not from a freshly closed bar.",
    "tier_alert_only": "Alert-only signal: only a full ENTRY signal may trade.",
    "daily_loss_halt": "The daily loss limit is reached; new entries are halted for today.",
    "unreconciled_exit_or_pnl": "A previous exit or its P&L is not reconciled yet.",
    "broker_mode_unknown": "The account mode (live or virtual) could not be determined.",
    "expiry_window": "The contract is inside the expiry square-off window.",
    "invalid_execution_inputs": "The price, protective stop or lot size was not usable.",
    "insufficient_capital": "There was not enough capital for this entry.",
    "capital_unavailable": "Available capital could not be read.",
    "drawdown_breaker": "The drawdown circuit breaker halted new entries.",
    "adx_below_min": "Trend strength (ADX) was below the minimum.",
    "atr_below_min": "Volatility (ATR percentile) was below the minimum.",
    "liquidity_filter": "The option was too thinly traded or its spread too wide.",
    "contract_unresolved": "The tradable contract could not be resolved.",
    "no_premium_stop": "No protective stop could be worked out, so no trade is taken.",
    "stop_mode_requires_broker": "Live entries need a broker-backed stop.",
    "daily_loss_not_configured": "No daily-loss circuit breaker is configured.",
    "duplicate_underlying": "Already holding a position on this underlying.",
    "already_holding": "Already holding this contract.",
    "execution_refused": "The order service refused the entry.",
    "safety_blocked": "A safety check blocked the order.",
    "session_closed": "The market session is not open for new entries.",
    "entry_close_buffer": "Too close to the market close for a new entry.",
    "cash_signal_auction": "The index feed is in its closing auction.",
    "lot_size_unavailable": "The contract's lot size was not available.",
    "zero_size": "The position size worked out to zero.",
    "disabled": "The strategy is switched off.",
    "swing_not_allowed_live": "Swing holds are not entered live: no broker stop before the first target.",
    "intraday_entry_cutoff": "Intraday entries stop at 15:20.",
    "live_intraday_broker_protection_required": "Live intraday entries need a broker-held stop.",
    "live_execution_disabled": "Live execution is not enabled.",
    "reference_swing_protection_unavailable": "Swing holds have no broker stop, so live Gamma skips them.",
    "pnl_reconciliation_required": "A previous trade's P&L is not reconciled yet.",
    "legacy_position_reconciliation_required": "An old position record needs reconciling first.",
    "gamma_registry_unreadable": "The position records could not be read.",
    "protection_not_confirmed": "The entry filled but its protective stop could not be confirmed.",
    "order_failed": "The order could not be completed.",
    "blocked": "The entry was blocked.",
}

_SKIP_PATTERNS: List[Tuple[str, str]] = [
    (r"daily loss|daily-loss.*halt|new entries halted for today", "daily_loss_halt"),
    (r"daily-loss circuit breaker", "daily_loss_not_configured"),
    (r"unreconciled_exit_or_pnl", "unreconciled_exit_or_pnl"),
    (r"broker mode is unknown", "broker_mode_unknown"),
    (r"expiry square-off window|expires .*inside the t-", "expiry_window"),
    (r"invalid execution inputs", "invalid_execution_inputs"),
    (r"broker_capital_unavailable_or_insufficient|broker_margin_unavailable_or_insufficient|"
     r"fill envelope exceeds|exceeds risk budget", "insufficient_capital"),
    (r"capital is unavailable", "capital_unavailable"),
    (r"circuit breaker \[", "drawdown_breaker"),
    (r"adx .* <", "adx_below_min"),
    (r"atr %ile", "atr_below_min"),
    (r"spread|open interest|liquidity|min_oi|thin", "liquidity_filter"),
    (r"unresolved|no futures quote", "contract_unresolved"),
    (r"no premium stop|carries no premium", "no_premium_stop"),
    (r"broker-backed stop mode", "stop_mode_requires_broker"),
    (r"cross guard|already holding a position on this underlying", "duplicate_underlying"),
    (r"scale_in_blocked|already holding", "already_holding"),
    (r"stale_or_unclosed_signal", "stale_or_unclosed_signal"),
    (r"session_|entry_close_buffer|cash_signal_auction", "session_closed"),
    (r"canonical execution|canonical entry", "execution_refused"),
    (r"blocked:|safety", "safety_blocked"),
    (r"sizing|no tradable size|risk budget|entry skipped", "invalid_execution_inputs"),
]


def humanize_code(code: str) -> str:
    text = re.sub(r"[_:]+", " ", str(code or "")).strip()
    return (text[:1].upper() + text[1:] + ".") if text else ""


def reason_for_code(code: str, fallback: str = "") -> str:
    """The sentence for a refusal code, preferring the strategy's own words when it gave any."""
    base = str(code or "").split(":", 1)[0]
    known = SKIP_REASONS.get(code) or SKIP_REASONS.get(base)
    if known:
        return known
    if fallback:
        return fallback
    lane = re.match(r"lane_(.+)", base)
    if lane:
        return f"The strategy's lane is not open to originate ({lane.group(1).replace('_', ' ')})."
    return humanize_code(code)


def classify_skip(strategy: str, text: object) -> Tuple[str, str]:
    """``(code, reason)`` for a free-text refusal line (Supertrend logs its refusals as prose).

    Anything not recognised keeps its own words as the reason under the generic ``blocked``
    code, so nothing is lost; the dedupe key is the code, so unrecognised lines still merge.
    """
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    low = raw.lower()
    for pattern, code in _SKIP_PATTERNS:
        if re.search(pattern, low):
            return code, SKIP_REASONS.get(code) or raw
    return "blocked", raw or SKIP_REASONS["blocked"]
