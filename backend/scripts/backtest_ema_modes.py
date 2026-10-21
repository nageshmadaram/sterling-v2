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
CAPITAL = Decimal("1000000")
#: History per signal timeframe. The live scanner only fetches a recent window
#: (enough to trade, not to test); the backtest asks for as much as the
#: verified session calendar allows (from 2024-11-01).
DAYS = {1: 120, 3: 200, 5: 200, 15: 400, 60: 720, 1440: 800}


async def fetch_long(client, meta, minutes: int, now):
    from datetime import time as dtime
    from app.engines.ema_trend.calendar import HISTORY_START
    from app.services.ema_trend.scanner import normalize_history
    interval = {1: "minute", 3: "3minute", 5: "5minute", 15: "15minute", 60: "60minute", 1440: "day"}[minutes]
    span = {1: 55, 3: 90, 5: 90, 15: 180, 60: 360, 1440: 1500}[minutes]
    earliest = datetime.combine(HISTORY_START, dtime(0, 0), IST)
    cursor, raw = max(now - timedelta(days=DAYS[minutes]), earliest), []
    while cursor < now:
        end = min(cursor + timedelta(days=span), now)
        payload = await client.get_historical(meta.token, interval, cursor.strftime("%Y-%m-%d %H:%M:%S"),
                                              end.strftime("%Y-%m-%d %H:%M:%S"))
        raw.extend(payload.get("candles", (payload.get("data") or {}).get("candles", [])))
        cursor = end
        await asyncio.sleep(0.35)
    return normalize_history(raw, minutes, asof_ms=int(now.timestamp() * 1000), exchange=meta.exchange)
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
                bars = await fetch_long(client, meta, profile.signal_minutes, now)
                if profile.context_minutes == 10080:
                    context = aggregate_completed_bars(bars, 10080, int(now.timestamp() * 1000), meta.exchange)
                else:
                    context = await fetch_long(client, meta, profile.context_minutes, now)
                import os
                extra = {}
                if os.environ.get("EMA_FEE_BPS"):
                    extra["fee_bps"] = Decimal(os.environ["EMA_FEE_BPS"])
                if os.environ.get("EMA_SLIPPAGE_BPS"):
                    extra["slippage_bps"] = Decimal(os.environ["EMA_SLIPPAGE_BPS"])
                cfg = EmaTrendConfig(symbols=[symbol], modes=[mode], allocation_inr=CAPITAL,
                                     execution_mode="PAPER", **extra)
                rep = run_replay(symbol, mode, bars, context, cfg, uid=uid)
                from app.engines.ema_trend.features import build_features
                from app.engines.ema_trend.setups import evaluate_setups
                warm = sum(1 for x in evaluate_setups(symbol, mode, build_features(bars, context))
                           if getattr(x, "reason", "") == "WARMING_UP")
                m = rep["metrics"]
                first = datetime.fromtimestamp(bars[0].close_ms / 1000, IST).date()
                import collections
                refusals = dict(collections.Counter(d.get("reason") for d in rep["decisions"]).most_common(4))
                rows.append({"refusals": refusals, "mode": mode, "symbol": symbol, "from": first.isoformat(), "bars": len(bars), "tested_bars": len(bars) - warm,
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
    import os
    tag = f"_fee{os.environ['EMA_FEE_BPS']}bps" if os.environ.get("EMA_FEE_BPS") else ""
    path = OUT / f"ema_modes_backtest_{now.date().isoformat()}{tag}.json"
    path.write_text(json.dumps(out, indent=2))
    print(json.dumps(summary, indent=2))
    print("written", path)


if __name__ == "__main__":
    asyncio.run(main())
