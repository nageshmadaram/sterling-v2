"""Gamma Unwind reference alert formatters (0.8.6 specification).

Generates exact message formats matching the reference GAMMA_Buzzer output:
1. Watchlist Alert (BREAKDOWN-PE / BREAKOUT-CE)
2. Intraday Added Stocks Alert (11:15, 13:15, 14:15)
3. Day Summary Alert (15:35 IST)
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping, Sequence

DIVIDER = "━━━━━━━━━━━━"


def format_expiry(exp: str | date | datetime) -> str:
    """Format expiry date: '2026-10-27' -> '27 Oct'."""
    if isinstance(exp, (datetime, date)):
        d = exp.date() if isinstance(exp, datetime) else exp
    else:
        try:
            d = datetime.fromisoformat(str(exp)[:10]).date()
        except Exception:
            return str(exp)
    return f"{d.day} {d.strftime('%b')}"


def format_day_header(dt: date | datetime) -> str:
    """Format day header: 'Mon 12 Oct' or 'Fri 9 Oct'."""
    d = dt.date() if isinstance(dt, datetime) else dt
    return f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"


def format_zone_edge(val: float) -> str:
    """Format zone edge: e.g. 1047.3 -> '1,047.3', 1766.0 -> '1,766', 5109.0 -> '5,109'."""
    v = float(val)
    if abs(v - round(v)) < 0.05 and v >= 1000:
        return f"{int(round(v)):,}"
    return f"{v:,.1f}"


def format_strike(strike: float | int) -> str:
    """Format option strike without redundant decimals: 410.0 -> '410', 410.5 -> '410.5'."""
    s = float(strike)
    if abs(s - round(s)) < 0.01:
        return str(int(round(s)))
    return f"{s:g}"


def _format_stock_strikes(
    stock: Mapping[str, Any],
    option_type: str,
    expiry_str: str,
) -> list[str]:
    """Format the 1-2 strikes for a stock."""
    arrow = "▼" if option_type.upper() == "PE" else "▲"
    symbol = str(stock.get("symbol") or stock.get("underlying") or "")
    edge_val = float(stock.get("zone_edge") or 0.0)
    edge_str = format_zone_edge(edge_val)
    spot = float(stock.get("spot") or stock.get("ref_price") or edge_val)
    gap_pct = float(stock.get("gap_pct") or (abs(spot - edge_val) / edge_val * 100.0 if edge_val > 0 else 0.0))
    rel = str(stock.get("rel") or ("above" if spot >= edge_val else "below"))

    lines: list[str] = []
    strikes = stock.get("strikes") or [stock]
    for item in strikes:
        stk_val = item.get("strike") or stock.get("strike") or 0
        ltp = float(item.get("ltp") or item.get("last_price") or item.get("chain_ltp") or 0.0)
        lines.append(f"{arrow} {symbol} {format_strike(stk_val)} {option_type.upper()} · {expiry_str}")
        lines.append(f"₹{ltp:.2f} · {gap_pct:.1f}% {rel} {edge_str}")
        lines.append("")
    return lines


def format_watchlist_alert(
    list_name: str,
    expiry: str | date,
    day: date | datetime,
    stocks: Sequence[Mapping[str, Any]],
    *,
    max_shown: int = 6,
    max_strikes: int | None = None,
) -> str:
    """Format the 09:21 Watchlist Alert (BREAKDOWN-PE or BREAKOUT-CE)."""
    expiry_str = format_expiry(expiry)
    day_str = format_day_header(day)
    side = "PE" if "PE" in list_name.upper() else "CE"
    support_label = "SUPPORT" if side == "PE" else "RESISTANCE"
    total_stocks = len(stocks)

    lines = [
        f"🗂️ {list_name.upper()} WATCHLIST · 📅 {expiry_str}",
        f"{day_str} · live premiums",
        DIVIDER,
        f"NEAREST {support_label} · {total_stocks} stocks",
    ]

    shown = list(stocks)[:max_shown]
    if max_strikes is not None:
        bounded = []
        count = 0
        for stock in shown:
            n = len(stock.get("strikes") or [stock])
            if count + n > max_strikes: break
            bounded.append(stock)
            count += n
        shown = bounded
    for stock in shown:
        lines.extend(_format_stock_strikes(stock, side, expiry_str))

    total_strikes = sum(len(s.get("strikes") or [s]) for s in stocks)
    shown_strikes = sum(len(s.get("strikes") or [s]) for s in shown)
    remaining_strikes = total_strikes - shown_strikes

    lines.append(DIVIDER)
    more_text = (
        f"{remaining_strikes} more on the dashboard"
        if remaining_strikes > 0
        else "full list on the dashboard"
    )
    lines.append(
        f"% = spot's gap to {support_label.lower()} · two strikes per stock · {more_text}"
    )
    return "\n".join(lines)


def format_added_alert(
    time_str: str,
    prev_time_str: str,
    build_window: str,
    day: date | datetime,
    *,
    pe_stocks: Sequence[Mapping[str, Any]] = (),
    ce_stocks: Sequence[Mapping[str, Any]] = (),
    pe_expiry: str | date | None = None,
    ce_expiry: str | date | None = None,
    max_shown: int = 3,
) -> str:
    """Format intraday added stocks alert (11:15, 13:15, 14:15)."""
    total_new = len(pe_stocks) + len(ce_stocks)
    stock_word = "stock" if total_new == 1 else "stocks"
    day_str = format_day_header(day)

    lines = [
        f"🗂️ ADDED {time_str} · {total_new} new {stock_word}",
        f"📅 {day_str} · reached a zone since {prev_time_str}",
        f"premiums at the {build_window} build",
    ]

    shown_pe = list(pe_stocks)[:max_shown]
    shown_ce = list(ce_stocks)[:max_shown]
    total_strikes = sum(len(s.get("strikes") or [s]) for s in pe_stocks) + sum(
        len(s.get("strikes") or [s]) for s in ce_stocks
    )
    shown_strikes = sum(len(s.get("strikes") or [s]) for s in shown_pe) + sum(
        len(s.get("strikes") or [s]) for s in shown_ce
    )

    if pe_stocks:
        lines.append(DIVIDER)
        exp_pe = format_expiry(
            pe_expiry or pe_stocks[0].get("expiry") or day
        )
        pe_cnt = len(pe_stocks)
        pe_word = "stock" if pe_cnt == 1 else "stocks"
        lines.append(f"BREAKDOWN-PE · 📅 {exp_pe} · {pe_cnt} {pe_word} near support")
        for stock in shown_pe:
            lines.extend(_format_stock_strikes(stock, "PE", exp_pe))

    if ce_stocks:
        lines.append(DIVIDER)
        exp_ce = format_expiry(
            ce_expiry or ce_stocks[0].get("expiry") or day
        )
        ce_cnt = len(ce_stocks)
        ce_word = "stock" if ce_cnt == 1 else "stocks"
        lines.append(f"BREAKOUT-CE · 📅 {exp_ce} · {ce_cnt} {ce_word} near resistance")
        for stock in shown_ce:
            lines.extend(_format_stock_strikes(stock, "CE", exp_ce))

    lines.append(DIVIDER)
    rem = total_strikes - shown_strikes
    more_text = f"{rem} more on the dashboard" if rem > 0 else "full list on the dashboard"
    lines.append(
        f"% = spot's gap to the zone edge · two strikes per stock · {more_text}"
    )
    return "\n".join(lines)


def format_day_summary_alert(
    day: date | datetime,
    entries: Sequence[Mapping[str, Any]] = (),
    near_entries: Sequence[Mapping[str, Any]] = (),
) -> str:
    """Format the 15:35 Day Summary Alert."""
    day_str = format_day_header(day)
    n_entries = len(entries)
    n_near = len(near_entries)
    entry_word = "entry" if n_entries == 1 else "entries"
    near_word = "near entry" if n_near <= 1 else "near entries"

    lines = [
        "🧾 DAY SUMMARY",
        f"📅 {day_str}",
        DIVIDER,
        "TODAY",
        f"⚡ {n_entries} {entry_word} · 👀 {n_near} {near_word}",
    ]

    if n_entries == 0 and n_near == 0:
        lines.append("No entries today.")
        return "\n".join(lines)

    if near_entries:
        lines.append(DIVIDER)
        lines.append("NEAR ENTRY")
        lines.append("🔵 not entries, not tracked")
        for item in near_entries:
            side = str(item.get("option_type") or "PE").upper()
            arrow = "▼" if side == "PE" else "▲"
            sym = str(item.get("symbol") or item.get("underlying") or "")
            stk = format_strike(item.get("strike") or 0)
            exp = format_expiry(item.get("expiry") or day)
            time_str = str(item.get("time") or "")
            if not time_str and item.get("at_ms"):
                try:
                    time_str = datetime.fromtimestamp(int(item["at_ms"]) / 1000).strftime("%H:%M")
                except Exception:
                    time_str = "13:05"
            price = float(item.get("price") or item.get("ltp") or 0.0)
            lines.append(f"{arrow} {sym} {stk} {side} · {exp}")
            lines.append(f"🕒 {time_str} · ₹{price:.2f}")
            lines.append("")

    if entries:
        lines.append(DIVIDER)
        lines.append("ENTRIES")
        for item in entries:
            side = str(item.get("option_type") or "CE").upper()
            arrow = "▼" if side == "PE" else "▲"
            sym = str(item.get("symbol") or item.get("underlying") or "")
            stk = format_strike(item.get("strike") or 0)
            exp = format_expiry(item.get("expiry") or day)
            time_str = str(item.get("time") or "")
            if not time_str and item.get("at_ms"):
                try:
                    time_str = datetime.fromtimestamp(int(item["at_ms"]) / 1000).strftime("%H:%M")
                except Exception:
                    time_str = "09:30"
            price = float(item.get("price") or item.get("ltp") or 0.0)
            lines.append(f"{arrow} {sym} {stk} {side} · {exp}")
            lines.append(f"🕒 {time_str} · ₹{price:.2f}")
            lines.append("")

    lines.append("")
    lines.append("Results from the trade tracker. Alert only, not advice.")
    return "\n".join(lines)


def extract_stocks_from_runtimes(
    runtimes: Mapping[int, Any],
    side: str,
) -> tuple[str, list[dict[str, Any]]]:
    """Group runtime watch contracts by underlying stock for a given side ('PE' or 'CE').

    Returns (expiry_str, list_of_stock_dicts) sorted by proximity to zone edge ascending.
    """
    by_stock: dict[str, dict[str, Any]] = {}
    exp_found = ""

    for rt in runtimes.values():
        contract = getattr(rt, "contract", None)
        if contract is None:
            continue
        inst = getattr(contract, "instrument", None)
        if inst is None or str(inst.option_type).upper() != side.upper():
            continue

        sym = str(contract.underlying)
        if not exp_found and inst.expiry:
            exp_found = str(inst.expiry)

        spot = float(getattr(rt, "spot", 0.0) or contract.ref_price or 0.0)
        edge = float(contract.zone_edge or 0.0)
        gap = abs(spot - edge) / edge * 100.0 if edge > 0 else 0.0
        rel = "above" if spot >= edge else "below"

        bars = getattr(rt, "closed_bars", None)
        ltp = float(bars[-1].close if bars else (contract.chain_ltp or 0.0))

        if sym not in by_stock:
            by_stock[sym] = {
                "symbol": sym,
                "spot": spot,
                "zone_edge": edge,
                "gap_pct": gap,
                "rel": rel,
                "strikes": [],
            }

        by_stock[sym]["strikes"].append({
            "strike": float(inst.strike),
            "ltp": ltp,
            "tag": str(contract.tag or "HOI-1"),
            "oi": int(contract.oi or 0),
        })

    for s_data in by_stock.values():
        s_data["strikes"].sort(
            key=lambda x: (x.get("tag") != "HOI-1", -x.get("oi", 0))
        )

    stock_list = sorted(by_stock.values(), key=lambda x: x["gap_pct"])
    return exp_found, stock_list


ADDED_SLOT_META: dict[str, tuple[str, str]] = {
    "11:15": ("09:20", "09:30–11:15"),
    "13:15": ("11:15", "11:30–13:00"),
    "14:15": ("13:15", "14:15"),
}

_seen_symbols: dict[tuple[str, str], set[str]] = {}
_pending_alerts: dict[str, list[Any]] = {}


def record_initial_seen(uid: str, day_iso: str, symbols: set[str]) -> None:
    _seen_symbols.setdefault((uid, day_iso), set()).update(symbols)


def get_seen_symbols(uid: str, day_iso: str) -> set[str]:
    return set(_seen_symbols.get((uid, day_iso), set()))


def queue_gamma_alert(uid: str, alert: Any) -> None:
    _pending_alerts.setdefault(uid, []).append(alert)


def pop_queued_gamma_alerts(uid: str) -> list[Any]:
    return _pending_alerts.pop(uid, [])


def check_and_queue_added_alerts(
    uid: str,
    slot: str,
    day: date,
    runtimes: Mapping[int, Any],
) -> Any | None:
    """If slot is an intraday build slot (11:15, 13:15, 14:15), find new stocks and queue an Added alert."""
    meta = ADDED_SLOT_META.get(slot)
    if not meta:
        return None
    prev_slot, build_window = meta
    day_iso = day.isoformat()
    seen = get_seen_symbols(uid, day_iso)

    pe_exp, all_pe = extract_stocks_from_runtimes(runtimes, "PE")
    ce_exp, all_ce = extract_stocks_from_runtimes(runtimes, "CE")

    new_pe = [s for s in all_pe if s["symbol"] not in seen]
    new_ce = [s for s in all_ce if s["symbol"] not in seen]

    if not new_pe and not new_ce:
        return None

    _seen_symbols.setdefault((uid, day_iso), set()).update(
        s["symbol"] for s in (*new_pe, *new_ce)
    )

    text = format_added_alert(
        slot,
        prev_slot,
        build_window,
        day,
        pe_stocks=new_pe,
        ce_stocks=new_ce,
        pe_expiry=pe_exp or day,
        ce_expiry=ce_exp or day,
    )

    try:
        from app.services.notifications.telegram_kite import StrategyAlert
        import time
        alert = StrategyAlert(
            strategy="gamma_move_added",
            strategy_label="Gamma Unwind",
            dedupe_key=f"gamma_added:{slot}:{uid}:{day_iso}",
            timestamp_ms=int(time.time() * 1000),
            title=f"ADDED {slot}",
            direction="NEUTRAL",
            detail=text,
        )
        queue_gamma_alert(uid, alert)
        return alert
    except Exception:
        return None
