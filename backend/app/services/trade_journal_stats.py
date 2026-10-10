"""Trade Journal statistics: pure functions over journal rows (plain dicts).

Nothing here reads a database or a clock, so every figure can be checked by hand.

Conventions, stated once because they decide the numbers:

* A trade's P&L is its **net** P&L when known, else its gross P&L; a closed trade with
  neither (it ended outside Sterling and no exit price was seen) is counted as a trade but
  stays out of every win/loss figure (``unknown_pnl``).
* ``win`` = P&L > 0, ``loss`` = P&L < 0, a P&L of exactly 0 is ``breakeven``.
  ``win_rate_pct`` is wins over every trade that has a P&L (breakevens included).
* ``profit_factor`` = total of the winning trades / total of the losing trades (as a
  positive number); ``None`` when there are no losses (it would be infinite).
* ``expectancy`` = average P&L per trade (rupees); ``expectancy_r`` = average R-multiple
  over the trades whose initial risk is known.
* ``max_drawdown`` = the deepest fall of cumulative P&L below its running peak, in rupees,
  walking the trades in the order they closed; the peak starts at zero, so a losing start
  counts. Reported as a positive number.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional

from app.services.trade_journal_narrative import EXIT_REASON_LABELS, STRATEGY_LABELS

KNOWN_STRATEGIES = ("supertrend", "snapback", "gamma")


def _f(value: object) -> Optional[float]:
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def row_pnl(row: Dict[str, Any]) -> Optional[float]:
    """The P&L a trade counts for: net when known, else gross, else nothing."""
    net = _f(row.get("net_pnl"))
    return net if net is not None else _f(row.get("gross_pnl"))


def _r2(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 2)


def _closed(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [r for r in rows if str(r.get("status") or "").upper() == "CLOSED"]


def _close_order(row: Dict[str, Any]) -> tuple:
    return (_f(row.get("exit_ts_ms")) or 0.0, str(row.get("trade_id") or ""))


def max_drawdown(pnls_in_order: Iterable[float]) -> float:
    peak = cum = worst = 0.0
    for pnl in pnls_in_order:
        cum += float(pnl)
        peak = max(peak, cum)
        worst = max(worst, peak - cum)
    return worst


def _streaks(pnls_in_order: List[float]) -> Dict[str, int]:
    best_win = best_loss = win = loss = 0
    for pnl in pnls_in_order:
        if pnl > 0:
            win, loss = win + 1, 0
        elif pnl < 0:
            loss, win = loss + 1, 0
        else:
            win = loss = 0
        best_win, best_loss = max(best_win, win), max(best_loss, loss)
    return {"max_consecutive_wins": best_win, "max_consecutive_losses": best_loss}


def summary(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """The headline numbers for a set of journal rows."""
    rows = list(rows)
    closed = sorted(_closed(rows), key=_close_order)
    decided = [(r, row_pnl(r)) for r in closed]
    decided = [(r, p) for r, p in decided if p is not None]
    pnls = [p for _, p in decided]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    n = len(pnls)
    net = sum(pnls)
    win_total, loss_total = sum(wins), sum(losses)

    gross_values = [g for g in (_f(r.get("gross_pnl")) for r, _ in decided) if g is not None]
    charge_values = [c for c in (_f(r.get("charges")) for r, _ in decided) if c is not None]
    holds = [h for h in (_f(r.get("hold_minutes")) for r in closed) if h is not None]
    r_values = [x for x in (_f(r.get("r_multiple")) for r, _ in decided) if x is not None]
    capitals = [c for c in (_f(r.get("capital_used")) for r in rows) if c is not None and c > 0]
    capital_closed = [c for c in (_f(r.get("capital_used")) for r, _ in decided) if c is not None and c > 0]

    best = max(decided, key=lambda rp: rp[1], default=None)
    worst = min(decided, key=lambda rp: rp[1], default=None)

    def _pick(pair: Optional[tuple]) -> Optional[Dict[str, Any]]:
        if pair is None:
            return None
        r, p = pair
        return {"trade_id": r.get("trade_id"), "symbol": r.get("symbol"),
                "strategy": r.get("strategy"), "pnl": _r2(p)}

    out: Dict[str, Any] = {
        "trades": len(closed),
        "trades_with_pnl": n,
        "unknown_pnl": len(closed) - n,
        "open": sum(1 for r in rows if str(r.get("status") or "").upper() == "OPEN"),
        "pending": sum(1 for r in rows if str(r.get("status") or "").upper() == "PENDING"),
        "rejected": sum(1 for r in rows if str(r.get("status") or "").upper() == "REJECTED"),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": n - len(wins) - len(losses),
        "win_rate_pct": _r2(len(wins) / n * 100.0) if n else None,
        "gross_pnl": _r2(sum(gross_values)) if gross_values else None,
        "charges": _r2(sum(charge_values)) if charge_values else None,
        "net_pnl": _r2(net) if n else 0.0,
        "avg_win": _r2(win_total / len(wins)) if wins else None,
        "avg_loss": _r2(loss_total / len(losses)) if losses else None,
        "profit_factor": _r2(win_total / abs(loss_total)) if losses else None,
        "payoff_ratio": (_r2((win_total / len(wins)) / abs(loss_total / len(losses)))
                         if wins and losses else None),
        "expectancy": _r2(net / n) if n else None,
        "expectancy_r": _r2(sum(r_values) / len(r_values)) if r_values else None,
        "best_trade": _pick(best),
        "worst_trade": _pick(worst),
        "max_drawdown": _r2(max_drawdown(pnls)) if n else 0.0,
        "avg_hold_minutes": _r2(sum(holds) / len(holds)) if holds else None,
        "capital_used_avg": _r2(sum(capitals) / len(capitals)) if capitals else None,
        "capital_used_peak": _r2(max(capitals)) if capitals else None,
        "return_on_capital_pct": (_r2(net / sum(capital_closed) * 100.0)
                                 if capital_closed and n else None),
    }
    out.update(_streaks(pnls))
    return out


def exit_reasons(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Count and P&L per canonical exit reason, most frequent first."""
    groups: Dict[str, List[float]] = defaultdict(list)
    unknown: Dict[str, int] = defaultdict(int)
    for r in _closed(rows):
        reason = str(r.get("exit_reason") or "OTHER")
        p = row_pnl(r)
        if p is None:
            unknown[reason] += 1
            groups.setdefault(reason, [])
        else:
            groups[reason].append(p)
    total = sum(len(v) + unknown.get(k, 0) for k, v in groups.items())
    out = []
    for reason, pnls in groups.items():
        count = len(pnls) + unknown.get(reason, 0)
        out.append({
            "reason": reason,
            "label": EXIT_REASON_LABELS.get(reason, reason.title()),
            "count": count,
            "pct_of_trades": _r2(count / total * 100.0) if total else None,
            "wins": sum(1 for p in pnls if p > 0),
            "losses": sum(1 for p in pnls if p < 0),
            "net_pnl": _r2(sum(pnls)),
            "avg_pnl": _r2(sum(pnls) / len(pnls)) if pnls else None,
        })
    out.sort(key=lambda g: (-g["count"], g["reason"]))
    return out


def _headline(label: str, s: Dict[str, Any], drag: Optional[Dict[str, Any]]) -> str:
    if not s["trades"]:
        return f"{label}: no closed trades yet."
    net = s["net_pnl"] or 0.0
    money = f"{'+' if net >= 0 else '-'}₹{abs(net):,.0f}"
    verdict = "making money" if net > 0 else ("losing money" if net < 0 else "breaking even")
    text = f"{label} is {verdict}: {money} over {s['trades']} trade{'s' if s['trades'] != 1 else ''}"
    if s["win_rate_pct"] is not None:
        text += f", {s['win_rate_pct']:.0f}% winners"
    text += "."
    if drag and drag["net_pnl"] < 0:
        text += (f" Biggest drag: {drag['label'].lower()} "
                 f"({drag['count']} trade{'s' if drag['count'] != 1 else ''}, "
                 f"-₹{abs(drag['net_pnl']):,.0f}).")
    return text


def by_strategy(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One comparison row per strategy: its summary, a plain-English headline, and the exit
    reason that costs it the most. The three known strategies always appear."""
    rows = list(rows)
    buckets: Dict[str, List[Dict[str, Any]]] = {s: [] for s in KNOWN_STRATEGIES}
    for r in rows:
        buckets.setdefault(str(r.get("strategy") or "other"), []).append(r)
    out = []
    for strategy, members in buckets.items():
        s = summary(members)
        reasons = exit_reasons(members)
        costly = [g for g in reasons if g["net_pnl"] < 0]
        drag = min(costly, key=lambda g: g["net_pnl"]) if costly else None
        label = STRATEGY_LABELS.get(strategy, strategy.title())
        out.append({"strategy": strategy, "label": label, **s,
                    "biggest_drag": drag, "headline": _headline(label, s, drag)})
    ranked = sorted(out, key=lambda g: (g["net_pnl"] if g["trades"] else float("-inf")), reverse=True)
    for i, g in enumerate(ranked, 1):
        g["rank"] = i if g["trades"] else None
    return out


def equity(rows: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Cumulative P&L, per trade (in close order) and per day."""
    decided = [(r, row_pnl(r)) for r in sorted(_closed(rows), key=_close_order)]
    decided = [(r, p) for r, p in decided if p is not None]
    by_trade, cum = [], 0.0
    daily: Dict[str, Dict[str, Any]] = {}
    for r, p in decided:
        cum += p
        day = str(r.get("exit_day") or "")
        by_trade.append({"ts_ms": r.get("exit_ts_ms"), "day": day, "trade_id": r.get("trade_id"),
                         "strategy": r.get("strategy"), "pnl": _r2(p), "cum_pnl": _r2(cum)})
        d = daily.setdefault(day, {"day": day, "pnl": 0.0, "trades": 0})
        d["pnl"] += p
        d["trades"] += 1
    by_day, running = [], 0.0
    for day in sorted(daily):
        running += daily[day]["pnl"]
        by_day.append({"day": day, "pnl": _r2(daily[day]["pnl"]), "trades": daily[day]["trades"],
                       "cum_pnl": _r2(running)})
    return {"by_trade": by_trade, "by_day": by_day}


def calendar(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Net P&L per calendar day (by the day the trade closed), oldest first."""
    days: Dict[str, Dict[str, Any]] = {}
    for r in _closed(rows):
        p = row_pnl(r)
        if p is None:
            continue
        day = str(r.get("exit_day") or "")
        d = days.setdefault(day, {"day": day, "net_pnl": 0.0, "gross_pnl": 0.0,
                                   "trades": 0, "wins": 0, "losses": 0})
        d["net_pnl"] += p
        d["gross_pnl"] += _f(r.get("gross_pnl")) if _f(r.get("gross_pnl")) is not None else p
        d["trades"] += 1
        d["wins"] += 1 if p > 0 else 0
        d["losses"] += 1 if p < 0 else 0
    return [{**days[k], "net_pnl": _r2(days[k]["net_pnl"]), "gross_pnl": _r2(days[k]["gross_pnl"])}
            for k in sorted(days)]
