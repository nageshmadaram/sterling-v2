"""Backtest EMA Trend in all five modes on real Kite bars for liquid NSE stocks.

Uses the strategy's own replay (run_replay): the live reducer, next-bar-open
fills, stop-first on a bar that touches both, and estimated fees. Each stock
and mode is replayed with the same capital; results are summed per mode.

Usage:
    PYTHONWARNINGS=ignore .venv/bin/python scripts/backtest_ema_modes.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.runtime_env import load_sterling_env  # noqa: E402

load_sterling_env()

from app.engines.ema_trend.backtest import run_replay  # noqa: E402
from app.engines.ema_trend.config import MODES, PROFILES, EmaTrendConfig  # noqa: E402
from app.engines.ema_trend.features import IST, aggregate_completed_bars  # noqa: E402

SYMBOLS = ["NSE:RELIANCE", "NSE:HDFCBANK", "NSE:ICICIBANK", "NSE:INFY", "NSE:TCS",
           "NSE:SBIN", "NSE:AXISBANK", "NSE:LT", "NSE:BHARTIARTL", "NSE:KOTAKBANK"]
CAPITAL = Decimal("100000")
OUT = Path(__file__).resolve().parents[2] / "docs" / "operations"


async def main() -> None:
    from app.services.ema_trend import scanner
    from app.services.exchanges.kite import accounts

    accounts.bootstrap()
    acct = next((a for a in accounts.all_accounts() if not a.is_paper and a.is_active and a.access_token_enc), None)
    if acct is None:
        raise SystemExit("no Kite account")
    uid = str(acct.user_id)
    client = await scanner.market_client(uid)
    now = datetime.now(IST)
    rows, errors = [], []
    for mode in MODES:
        profile = PROFILES[mode]
        for symbol in SYMBOLS:
            try:
                meta = await scanner.equity_metadata(uid, symbol, client=client)
                bars = await scanner.fetch_bars(client, meta, profile.signal_minutes, now)
                if profile.context_minutes == 10080:
                    context = aggregate_completed_bars(bars, 10080, int(now.timestamp() * 1000), meta.exchange)
                else:
                    context = await scanner.fetch_bars(client, meta, profile.context_minutes, now)
                cfg = EmaTrendConfig(symbols=[symbol], modes=[mode], allocation_inr=CAPITAL,
                                     execution_mode="PAPER")
                rep = run_replay(symbol, mode, bars, context, cfg, uid=uid,
                                 activation_ms=int(bars[min(250, len(bars) - 1)].close_ms))
                m = rep["metrics"]
                first = datetime.fromtimestamp(bars[0].close_ms / 1000, IST).date()
                rows.append({"mode": mode, "symbol": symbol, "from": first.isoformat(), "bars": len(bars),
                             "trades": m["completed_campaigns"], "open": m["open_campaigns"],
                             "net": float(m["net_realized"]), "fees": float(m["estimated_fees"]),
                             "win_rate": m["win_rate"], "max_dd": float(m["max_drawdown"])})
                print(f"{mode:15} {symbol:15} trades={m['completed_campaigns']:3} net={float(m['net_realized']):10.2f}", flush=True)
            except Exception as exc:  # noqa: BLE001
                errors.append({"mode": mode, "symbol": symbol, "error": str(exc)[:200]})
                print(f"{mode:15} {symbol:15} ERROR {str(exc)[:120]}", flush=True)
            await asyncio.sleep(0.4)

    summary = {}
    for mode in MODES:
        r = [x for x in rows if x["mode"] == mode]
        trades = sum(x["trades"] for x in r)
        if not r:
            continue
        wins = sum(round((x["win_rate"] or 0) * x["trades"]) for x in r)
        summary[mode] = {"symbols": len(r), "trades": trades,
                         "win_rate": round(wins / trades, 3) if trades else None,
                         "net_inr": round(sum(x["net"] for x in r), 2),
                         "fees_inr": round(sum(x["fees"] for x in r), 2),
                         "worst_symbol_dd": round(max((x["max_dd"] for x in r), default=0), 4),
                         "profitable_symbols": sum(1 for x in r if x["net"] > 0),
                         "history_from": min(x["from"] for x in r)}
    out = {"generated": now.isoformat(), "capital_per_symbol": str(CAPITAL), "symbols": SYMBOLS,
           "summary": summary, "rows": rows, "errors": errors}
    path = OUT / f"ema_modes_backtest_{now.date().isoformat()}.json"
    path.write_text(json.dumps(out, indent=2))
    print(json.dumps(summary, indent=2))
    print("written", path)


if __name__ == "__main__":
    asyncio.run(main())
