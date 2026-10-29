"""Pure Decimal-based hypothetical performance analytics.

Metrics are read-only descriptive math on externally specified synthetic
assumptions, NOT a trading signal, live P&L, broker fill or safety approval.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timezone
from decimal import Decimal
from math import sqrt

from app.domain.offline_performance import HypotheticalTrade, decimal_text as dtext


def _f(x):
    return float(x)


def _mean(xs):
    return sum(xs, Decimal(0)) / Decimal(len(xs)) if xs else None


def _profit_factor(xs):
    gains = sum((x for x in xs if x > 0), Decimal(0))
    losses = -sum((x for x in xs if x < 0), Decimal(0))
    return gains / losses if losses else None


def _expectancy(xs):
    return _mean(xs)


def _annualized_ratio(daily_returns, *, downside=False):
    # At least two active daily samples; undefined denominators yield null,
    # never infinity and never an assumed performance score.
    if len(daily_returns) < 2:
        return None
    avg = _mean(daily_returns)
    if downside:
        square = sum((min(Decimal(0), x) ** 2 for x in daily_returns), Decimal(0))
        denom = (square / Decimal(len(daily_returns))).sqrt()
    else:
        variance = sum(((x - avg) ** 2 for x in daily_returns), Decimal(0)) / Decimal(len(daily_returns) - 1)
        denom = variance.sqrt()
    if denom == 0:
        return None
    return avg / denom * Decimal(252).sqrt()


def _serial(value):
    if isinstance(value, Decimal):
        return dtext(value)
    if isinstance(value, list):
        return [_serial(x) for x in value]
    if isinstance(value, tuple):
        return [_serial(x) for x in value]
    if isinstance(value, dict):
        return {k: _serial(v) for k, v in value.items()}
    return value


def summarize(trades: list[HypotheticalTrade] | tuple[HypotheticalTrade, ...],
              *, starting_equity_inr: Decimal | None = None,
              rolling_window: int = 5) -> dict:
    if starting_equity_inr is not None and (not starting_equity_inr.is_finite() or starting_equity_inr <= 0):
        raise ValueError("starting equity must be finite and positive")
    if not 2 <= rolling_window <= 200:
        raise ValueError("rolling window must be from 2 to 200")
    trades = sorted(trades, key=lambda t: (t.exit_at_utc, t.id))
    if any(t.execution_authorized is not False or t.evidence_kind != "offline_hypothetical"
           or t.source_provenance != "operator_supplied_synthetic" for t in trades):
        raise PermissionError("only synthetic, non-authoritative evidence supported")
    values = [t.net_pnl_inr for t in trades]
    gains = [x for x in values if x > 0]
    losses = [x for x in values if x < 0]
    gross_profit = sum(gains, Decimal(0))
    gross_loss = -sum(losses, Decimal(0))
    fees = sum((t.fees_inr for t in trades), Decimal(0))
    slippage = sum((t.slippage_inr for t in trades), Decimal(0))
    equity = starting_equity_inr if starting_equity_inr is not None else Decimal(0)
    peak = equity
    maximum_drawdown = Decimal(0)
    maximum_drawdown_pct = Decimal(0) if starting_equity_inr is not None else None
    series = []
    buckets = defaultdict(lambda: Decimal(0))
    months = defaultdict(lambda: Decimal(0))
    contributions_strategy = defaultdict(lambda: Decimal(0))
    contributions_symbol = defaultdict(lambda: Decimal(0))
    risk_multiples = []
    returns_per_trade = []
    assumed_entry_notionals = []
    for i, t in enumerate(trades):
        equity += t.net_pnl_inr
        peak = max(peak, equity)
        drawdown = peak - equity
        maximum_drawdown = max(maximum_drawdown, drawdown)
        # Use current historical high-water mark, never guess capital.
        pct = (drawdown / peak * 100
               if starting_equity_inr is not None and peak > 0 else None)
        if pct is not None:
            maximum_drawdown_pct = max(maximum_drawdown_pct, pct)
        day = t.exit_at_utc.astimezone(timezone.utc).date().isoformat()
        month = day[:7]
        buckets[day] += t.net_pnl_inr
        months[month] += t.net_pnl_inr
        contributions_strategy[t.strategy_id] += t.net_pnl_inr
        contributions_symbol[f"{t.exchange}:{t.symbol}"] += t.net_pnl_inr
        if t.risk_amount_inr is not None:
            risk_multiples.append(t.net_pnl_inr / t.risk_amount_inr)
        entry_notional = t.entry_price * t.units
        assumed_entry_notionals.append(entry_notional)
        returns_per_trade.append(t.net_pnl_inr / entry_notional)
        window = values[max(0, i - rolling_window + 1):i + 1]
        series.append({
            "at_utc": t.exit_at_utc.isoformat(), "trade_id": t.id,
            "net_pnl_inr": t.net_pnl_inr, "equity_inr": equity,
            "drawdown_inr": drawdown, "drawdown_pct": pct,
            "rolling_profit_factor": _profit_factor(window) if len(window) == rolling_window else None,
            "rolling_expectancy_inr": _mean(window) if len(window) == rolling_window else None,
        })
    # Active UTC-day snapshots ONLY, not a calendar-complete daily return series.
    # Never call these broker-proven strategy Sharpe/Sortino estimates.
    daily_returns = ([v / starting_equity_inr for _, v in sorted(buckets.items())]
                     if starting_equity_inr is not None else [])
    mean_r = _mean(risk_multiples)
    duration_s = _mean([Decimal(str((t.exit_at_utc - t.entry_at_utc).total_seconds())) for t in trades])
    report = {
        "provenance": "operator_supplied_synthetic",
        "metric_status": "hypothetical_not_verified_live_profitability",
        "execution_authorized": False,
        "trade_count": len(trades),
        "winning_trades": len(gains), "losing_trades": len(losses),
        "flat_trades": len(trades) - len(gains) - len(losses),
        # Gross profit/loss use WINNING/LOSING NET TRADE outcomes (after all cost);
        # gross price movement before fees is separately surfaced.
        "gross_profit_inr": gross_profit, "gross_loss_inr": gross_loss,
        "net_pnl_inr": sum(values, Decimal(0)),
        "price_pnl_before_cost_inr": sum((t.gross_pnl_inr for t in trades), Decimal(0)),
        "total_fees_inr": fees, "total_slippage_inr": slippage,
        "profit_factor": _profit_factor(values),
        "win_rate_pct": (Decimal(len(gains)) * 100 / Decimal(len(trades))) if trades else None,
        "average_win_inr": _mean(gains), "average_loss_inr": _mean(losses),
        "expectancy_inr": _expectancy(values),
        "max_drawdown_inr": maximum_drawdown if trades else None,
        "max_drawdown_pct": maximum_drawdown_pct if trades else None,
        "mean_return_per_trade_pct": (_mean(returns_per_trade) * 100) if trades else None,
        "average_r_multiple": mean_r,
        "average_trade_duration_seconds": duration_s,
        # These are hypothetical *single-trade entry notionals*, not
        # authenticated portfolio/account exposure.
        "mean_assumed_entry_notional_inr": _mean(assumed_entry_notionals),
        "peak_assumed_entry_notional_inr": max(assumed_entry_notionals) if assumed_entry_notionals else None,
        "observed_live_exposure_inr": None,
        "return_per_risk_unit": mean_r,
        "sharpe_active_utc_days": _annualized_ratio(daily_returns),
        "sortino_active_utc_days": _annualized_ratio(daily_returns, downside=True),
        "starting_equity_inr": starting_equity_inr,
        "daily_pnl_inr": dict(sorted(buckets.items())),
        "session_pnl_inr": dict(sorted(buckets.items())),  # UTC day only: session calendar unknown
        "monthly_pnl_inr": dict(sorted(months.items())),
        "strategy_contribution_inr": dict(sorted(contributions_strategy.items())),
        "symbol_contribution_inr": dict(sorted(contributions_symbol.items())),
        "equity_curve": series,
        "rolling_window_trades": rolling_window,
    }
    return _serial(report)


def compare(trades, *, group_by: str, starting_equity_inr: Decimal | None = None,
            rolling_window: int = 5):
    fields = {
        "strategy": lambda t: t.strategy_id,
        "version": lambda t: t.version_id,
        "symbol": lambda t: f"{t.exchange}:{t.symbol}",
        "regime": lambda t: t.market_regime,
        "approval_mode": lambda t: t.approval_mode,
    }
    if group_by not in fields:
        raise ValueError("unsupported comparison dimension")
    groups = defaultdict(list)
    for trade in trades:
        groups[fields[group_by](trade)].append(trade)
    return {
        "group_by": group_by, "metric_status": "hypothetical_not_verified_live_profitability",
        "execution_authorized": False,
        "groups": {key: summarize(group, starting_equity_inr=starting_equity_inr,
                                  rolling_window=rolling_window)
                   for key, group in sorted(groups.items())},
    }


def threshold_alerts(summary, *, min_win_rate_pct=Decimal(40),
                     min_trade_count=5, max_drawdown_pct=Decimal(20)):
    if not summary or summary.get("execution_authorized") is not False:
        raise PermissionError("non-authoritative metrics only")
    n = summary["trade_count"]
    if n < min_trade_count:
        return [{"code": "INSUFFICIENT_SYNTHETIC_SAMPLE", "severity": "info",
                 "delivery": "not_sent", "execution_authorized": False}]
    alerts = []
    def item(code):
        alerts.append({"code": code, "severity": "informational_only",
                       "delivery": "not_sent", "execution_authorized": False})
    pf = summary["profit_factor"]
    if pf is not None and Decimal(pf) < 1: item("PROFIT_FACTOR_BELOW_ONE")
    expectancy = summary["expectancy_inr"]
    if expectancy is not None and Decimal(expectancy) < 0: item("NEGATIVE_EXPECTANCY")
    wr = summary["win_rate_pct"]
    if wr is not None and Decimal(wr) < min_win_rate_pct: item("LOW_WIN_RATE")
    dd = summary["max_drawdown_pct"]
    if dd is None: item("DRAWDOWN_PERCENT_UNAVAILABLE")
    elif Decimal(dd) > max_drawdown_pct: item("DRAWDOWN_THRESHOLD")
    if summary["net_pnl_inr"] is not None and Decimal(summary["net_pnl_inr"]) < 0:
        item("HYPOTHETICAL_UNDERPERFORMANCE")
    return alerts
