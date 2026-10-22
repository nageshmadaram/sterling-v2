"""Statistics checked against figures worked out by hand (see the table in ``TRADES``)."""
from __future__ import annotations

import pytest

from app.services import trade_journal_stats as stats
from tj_support import ms

# (id, strategy, net P&L, closed at, hold min, R, exit reason)
#  T1  supertrend  +1000  12 Oct 10:00   60   2.0  TARGET
#  T2  supertrend   -400  12 Oct 11:00   30  -1.0  SL
#  T3  gamma        +600  12 Oct 12:00   90   1.5  TARGET
#  T4  gamma        -900  13 Oct 10:00   45  -1.0  SL
#  T5  snapback     +300  13 Oct 11:00  120   0.5  TSL
#  T6  supertrend   -200  14 Oct 10:00   15  -0.5  SL
#  T7  supertrend  (still open)
_ROWS = [
    ("T1", "supertrend", 1000.0, ms(10, 0, 12), 60, 2.0, "TARGET"),
    ("T2", "supertrend", -400.0, ms(11, 0, 12), 30, -1.0, "SL"),
    ("T3", "gamma", 600.0, ms(12, 0, 12), 90, 1.5, "TARGET"),
    ("T4", "gamma", -900.0, ms(10, 0, 13), 45, -1.0, "SL"),
    ("T5", "snapback", 300.0, ms(11, 0, 13), 120, 0.5, "TSL"),
    ("T6", "supertrend", -200.0, ms(10, 0, 14), 15, -0.5, "SL"),
]


def _row(tid, strategy, net, exit_ms, hold, r, reason):
    return dict(trade_id=tid, strategy=strategy, symbol=f"SYM{tid}", status="CLOSED", net_pnl=net,
                gross_pnl=net + 50.0, charges=50.0, exit_ts_ms=exit_ms, exit_day=f"2026-10-{12 + (exit_ms > ms(23, 59, 12)) + (exit_ms > ms(23, 59, 13))}",
                hold_minutes=hold, r_multiple=r, capital_used=10000.0, exit_reason=reason)


@pytest.fixture
def trades():
    rows = [_row(*r) for r in _ROWS]
    rows.append(dict(trade_id="T7", strategy="supertrend", symbol="SYMT7", status="OPEN", capital_used=20000.0))
    return rows


def test_summary_matches_the_hand_computed_figures(trades):
    s = stats.summary(trades)
    assert s["trades"] == 6 and s["open"] == 1 and s["unknown_pnl"] == 0
    assert (s["wins"], s["losses"], s["breakeven"]) == (3, 3, 0)
    assert s["win_rate_pct"] == 50.0
    assert s["net_pnl"] == 400.0            # 1000 - 400 + 600 - 900 + 300 - 200
    assert s["gross_pnl"] == 700.0          # net + 6 x 50 charges
    assert s["charges"] == 300.0
    assert s["avg_win"] == 633.33           # 1900 / 3
    assert s["avg_loss"] == -500.0          # -1500 / 3
    assert s["profit_factor"] == 1.27       # 1900 / 1500
    assert s["payoff_ratio"] == 1.27        # 633.33 / 500
    assert s["expectancy"] == 66.67         # 400 / 6
    assert s["expectancy_r"] == 0.25        # (2 - 1 + 1.5 - 1 + 0.5 - 0.5) / 6
    assert s["best_trade"]["trade_id"] == "T1" and s["best_trade"]["pnl"] == 1000.0
    assert s["worst_trade"]["trade_id"] == "T4" and s["worst_trade"]["pnl"] == -900.0
    assert s["avg_hold_minutes"] == 60.0    # 360 / 6
    assert s["max_consecutive_wins"] == 1 and s["max_consecutive_losses"] == 1
    assert s["capital_used_peak"] == 20000.0
    assert s["capital_used_avg"] == 11428.57    # (6 x 10000 + 20000) / 7
    assert s["return_on_capital_pct"] == 0.67   # 400 / 60000


def test_max_drawdown_is_peak_to_trough_in_close_order(trades):
    # cumulative: 1000, 600, 1200, 300, 600, 400 -> deepest fall is 1200 -> 300
    assert stats.summary(trades)["max_drawdown"] == 900.0
    # a losing start counts from the zero peak
    assert stats.max_drawdown([-100.0, -50.0, 30.0]) == 150.0
    assert stats.max_drawdown([100.0, 50.0]) == 0.0
    assert stats.max_drawdown([]) == 0.0


def test_close_order_not_input_order_decides_drawdown():
    rows = [_row("A", "gamma", -500.0, ms(14, 0), 10, None, "SL"),
            _row("B", "gamma", 800.0, ms(10, 0), 10, None, "TARGET")]
    # B closed first: +800 then -500 -> drawdown 500 (input order would say 0 from a -500 start)
    assert stats.summary(rows)["max_drawdown"] == 500.0


def test_profit_factor_is_none_without_losses_and_break_even_is_neither_win_nor_loss():
    wins_only = [_row("A", "gamma", 100.0, ms(10, 0), 5, None, "TARGET")]
    s = stats.summary(wins_only)
    assert s["profit_factor"] is None and s["avg_loss"] is None and s["win_rate_pct"] == 100.0
    flat = [_row("A", "gamma", 0.0, ms(10, 0), 5, None, "EOD"), _row("B", "gamma", 100.0, ms(11, 0), 5, None, "TARGET")]
    s = stats.summary(flat)
    assert (s["wins"], s["losses"], s["breakeven"]) == (1, 0, 1) and s["win_rate_pct"] == 50.0


def test_a_closed_trade_without_a_pnl_is_counted_but_not_scored():
    rows = [_row("A", "gamma", 100.0, ms(10, 0), 5, None, "TARGET"),
            dict(trade_id="B", strategy="gamma", status="CLOSED", net_pnl=None, gross_pnl=None,
                 exit_ts_ms=ms(11, 0), exit_day="2026-10-12", exit_reason="RECONCILED")]
    s = stats.summary(rows)
    assert s["trades"] == 2 and s["trades_with_pnl"] == 1 and s["unknown_pnl"] == 1
    assert s["win_rate_pct"] == 100.0 and s["net_pnl"] == 100.0


def test_gross_stands_in_for_net_when_charges_are_unknown():
    rows = [dict(trade_id="A", strategy="gamma", status="CLOSED", net_pnl=None, gross_pnl=250.0,
                 exit_ts_ms=ms(10, 0), exit_day="2026-10-12", exit_reason="TARGET")]
    assert stats.summary(rows)["net_pnl"] == 250.0


def test_empty_journal_is_all_zero_not_an_error():
    s = stats.summary([])
    assert s["trades"] == 0 and s["net_pnl"] == 0.0 and s["win_rate_pct"] is None and s["max_drawdown"] == 0.0


def test_by_strategy_ranks_and_explains_the_biggest_drag(trades):
    table = {g["strategy"]: g for g in stats.by_strategy(trades)}
    assert list(table) == ["supertrend", "snapback", "gamma", "ema_trend", "price_rules"]
    assert table["supertrend"]["net_pnl"] == 400.0 and table["supertrend"]["trades"] == 3
    assert table["gamma"]["net_pnl"] == -300.0 and table["snapback"]["net_pnl"] == 300.0
    assert (table["supertrend"]["rank"], table["snapback"]["rank"], table["gamma"]["rank"]) == (1, 2, 3)
    assert table["gamma"]["biggest_drag"]["reason"] == "SL" and table["gamma"]["biggest_drag"]["net_pnl"] == -900.0
    assert table["supertrend"]["biggest_drag"]["count"] == 2     # two stop-outs cost -600
    assert table["snapback"]["biggest_drag"] is None
    assert table["gamma"]["headline"] == (
        "Gamma Unwind is losing money: -₹300 over 2 trades, 50% winners. "
        "Biggest drag: stop-loss hit (1 trade, -₹900).")
    assert table["snapback"]["headline"] == "Snapback is making money: +₹300 over 1 trade, 100% winners."


def test_by_strategy_lists_all_strategies_even_with_no_trades():
    table = stats.by_strategy([])
    assert [g["strategy"] for g in table] == ["supertrend", "snapback", "gamma", "ema_trend", "price_rules"]
    assert all(g["rank"] is None and g["trades"] == 0 for g in table)
    assert table[0]["headline"] == "Supertrend: no closed trades yet."


def test_exit_reasons_count_and_pnl(trades):
    reasons = {g["reason"]: g for g in stats.exit_reasons(trades)}
    assert [g["reason"] for g in stats.exit_reasons(trades)] == ["SL", "TARGET", "TSL"]
    assert reasons["SL"]["count"] == 3 and reasons["SL"]["net_pnl"] == -1500.0 and reasons["SL"]["losses"] == 3
    assert reasons["SL"]["pct_of_trades"] == 50.0 and reasons["SL"]["avg_pnl"] == -500.0
    assert reasons["TARGET"]["count"] == 2 and reasons["TARGET"]["net_pnl"] == 1600.0
    assert reasons["TSL"]["pct_of_trades"] == 16.67 and reasons["TSL"]["label"] == "Trailing stop hit"


def test_equity_curve_per_trade_and_per_day(trades):
    curve = stats.equity(trades)
    assert [p["cum_pnl"] for p in curve["by_trade"]] == [1000.0, 600.0, 1200.0, 300.0, 600.0, 400.0]
    assert [(d["day"], d["pnl"], d["cum_pnl"], d["trades"]) for d in curve["by_day"]] == [
        ("2026-10-12", 1200.0, 1200.0, 3), ("2026-10-13", -600.0, 600.0, 2), ("2026-10-14", -200.0, 400.0, 1)]


def test_calendar_is_net_pnl_per_closing_day(trades):
    cal = stats.calendar(trades)
    assert [(d["day"], d["net_pnl"], d["trades"], d["wins"], d["losses"]) for d in cal] == [
        ("2026-10-12", 1200.0, 3, 2, 1), ("2026-10-13", -600.0, 2, 1, 1), ("2026-10-14", -200.0, 1, 0, 1)]
    assert cal[0]["gross_pnl"] == 1350.0        # 1200 net + 3 x 50 charges
