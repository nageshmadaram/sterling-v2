"""Snapback across timeframes: daily swing on long history, intraday modes on real options.

Part A, swing (daily): the strategy's own portfolio replay (``backtest.replay``)
on about seven years of Kite daily bars for liquid F&O names plus NIFTY. The
option premium is MODELLED (no store holds expired-option prices), so the
honest output includes the break-even vol multiple. Four variants: market gate
on/off x futures hedge on/off.

Part B, minute modes: ``intraday.replay_intraday`` on REAL synchronized option
candles from Kite, for contracts still listed (so only recent months). Each
underlying gets its near-ATM CE and PE of the next monthly expiry. A day is used
only when both tapes are complete for it; nothing is filled in.

Usage:
    PYTHONWARNINGS=ignore .venv/bin/python scripts/backtest_snapback_timeframes.py
"""
from __future__ import annotations

import asyncio
import json
import math
import sys
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from collections.abc import Mapping
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.runtime_env import load_sterling_env  # noqa: E402

load_sterling_env()

from app.engines.snapback import Bars, validate  # noqa: E402
from app.engines.snapback.backtest import replay, returns_at_vrp  # noqa: E402
from app.engines.snapback.intraday import replay_intraday  # noqa: E402
from app.engines.snapback.challenger import CHALLENGER_ID  # noqa: E402
from app.engines.snapback.research_reporting import (  # noqa: E402
    choose_option_contract, closed_trade_audit, contract_replay_summary,
    current_instrument_master_provenance, eligible_expiries, mode_totals,
    write_research_report_once,
)
from app.core.research_coverage import paired_session_coverage  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
OUT = Path(__file__).resolve().parents[2] / "docs" / "operations"
STOCKS = ["RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN", "AXISBANK", "LT",
          "BHARTIARTL", "KOTAKBANK", "ITC", "HINDUNILVR", "BAJFINANCE", "MARUTI", "SUNPHARMA", "TATASTEEL"]
INDEX_TOKENS = {"NIFTY": 256265, "BANKNIFTY": 260105}
INTRADAY_UNDERLYINGS = ["NIFTY", "BANKNIFTY", "RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN"]
MODES = {"ultra_scalping": ("minute", 1, 30), "scalp": ("minute", 1, 30),
         "intraday": ("15minute", 15, 90), "overnight": ("60minute", 60, 90)}


def _bars(rows) -> Bars:
    a = np.array([[r[0], r[1], r[2], r[3], r[4], r[5] if len(r) > 5 else 0.0] for r in rows], dtype=float)
    return Bars(a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5])


def _rows(payload) -> list[list[float]]:
    rows = payload.get("candles", (payload.get("data") or {}).get("candles", [])) if isinstance(payload, dict) else payload
    if not isinstance(rows, (list, tuple)):
        raise ValueError("historical candles must be a list of rows")
    out = []
    for r in rows or []:
        if not isinstance(r, (list, tuple, dict)) or (isinstance(r, (list, tuple)) and len(r) < 6):
            raise ValueError("historical candle row has no complete OHLCV")
        ts = r[0] if isinstance(r, (list, tuple)) else r["date"]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        elif type(ts) in (int, float) and math.isfinite(ts):
            ts = datetime.fromtimestamp(ts, IST)
        if not isinstance(ts, datetime):
            raise ValueError("historical candle timestamp is invalid")
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=IST)
        vals = r[1:6] if isinstance(r, (list, tuple)) else [r["open"], r["high"], r["low"], r["close"], r.get("volume", 0)]
        if any(v is None for v in vals):
            raise ValueError("historical OHLCV is missing")
        values = [float(v) for v in vals]
        if not all(math.isfinite(v) for v in values):
            raise ValueError("historical OHLCV must be finite")
        out.append([ts.timestamp(), *values])
    return out


async def history(client, token: int, interval: str, start: datetime, end: datetime,
                  span_days: int, *, preserve_duplicates: bool = False):
    rows, cur = [], start
    while cur < end:
        stop = min(cur + timedelta(days=span_days), end)
        payload = await client.get_historical(int(token), interval, cur.strftime("%Y-%m-%d %H:%M:%S"),
                                              stop.strftime("%Y-%m-%d %H:%M:%S"), False, False)
        rows.extend(_rows(payload))
        cur = stop
        await asyncio.sleep(0.4)
    ordered = sorted(rows)
    if preserve_duplicates:
        # Duplicate boundary bars are actual quality failures in paired replay;
        # de-duplicating before the coverage audit would erase that evidence.
        return ordered
    seen, uniq = set(), []
    for r in ordered:
        if r[0] not in seen:
            seen.add(r[0]); uniq.append(r)
    return uniq


def summary(trades) -> dict:
    if not trades:
        return {"trades": 0}
    rets = np.array([t.ret for t in trades])
    nets = np.array([t.net for t in trades])
    return {"trades": len(trades), "win_rate": round(float((nets > 0).mean()), 3),
            "mean_return_per_trade_pct": round(float(rets.mean() * 100), 2),
            "median_return_pct": round(float(np.median(rets) * 100), 2),
            "net_inr_one_lot_each": round(float(nets.sum()), 0),
            "worst_trade_pct": round(float(rets.min() * 100), 1),
            "best_trade_pct": round(float(rets.max() * 100), 1),
            "first": min(t.entry_day for t in trades), "last": max(t.entry_day for t in trades)}


async def main() -> None:
    from app.services.exchanges.kite import accounts
    from app.services.ema_trend import scanner as ema_scanner

    accounts.bootstrap()
    acct = next((a for a in accounts.all_accounts() if not a.is_paper and a.is_active and a.access_token_enc), None)
    if acct is None:
        raise SystemExit("no Kite account")
    uid = str(acct.user_id)
    client = await accounts.acquire_client(acct)
    now = datetime.now(IST)
    report: dict = {"generated": now.isoformat(), "promotion_eligible": False,
                    "limitations": ["Current-contract selection is retrospective, not a date-effective universe",
                                    "Swing option prices are MODELLED; minute replay is REAL_MARKET_SIM, never broker fills",
                                    "No independent OOS/forward acceptance is established by this script"]}

    tokens = dict(INDEX_TOKENS)
    for s in STOCKS:
        try:
            meta = await ema_scanner.equity_metadata(uid, f"NSE:{s}", client=client)
            tokens[s] = int(meta.token)
        except Exception as exc:  # noqa: BLE001
            print("no token", s, exc, flush=True)

    # ── Part A: daily swing ──────────────────────────────────────────────
    tapes = {}
    for name in ["NIFTY", *STOCKS]:
        if name not in tokens:
            continue
        rows = await history(client, tokens[name], "day", now - timedelta(days=2600), now, 1900)
        rows = [r for r in rows if datetime.fromtimestamp(r[0], IST).date() < now.date()]
        if len(rows) > 300:
            tapes[name] = _bars(rows)
        print("daily", name, len(rows), flush=True)
    base = validate({"trading_mode": "swing"})
    one_lot = dict(sizing_mode="LOTS", lots=1)
    variants = {
        "gate_on_no_hedge": replace(base, market_filter="bearish", hedge_mode="none", **one_lot),
        "gate_off_no_hedge": replace(base, market_filter="off", hedge_mode="none", **one_lot),
        "gate_on_futures_hedge": replace(base, market_filter="bearish", hedge_mode="index_futures", **one_lot),
        "gate_off_futures_hedge": replace(base, market_filter="off", hedge_mode="index_futures", **one_lot),
    }
    swing = {}
    for name, cfg in variants.items():
        try:
            res = replay(tapes, cfg)
            s = summary(res.trades)
            s["dropped_signals"] = dict(res.unsized) if getattr(res, "unsized", None) else {}
            try:
                from app.engines.snapback.pricing import break_even_vrp
                s["break_even_vol_multiple"] = round(float(break_even_vrp(returns_at_vrp(tapes, cfg))), 3)
            except Exception as exc:  # noqa: BLE001
                s["vol_curve_error"] = str(exc)[:120]
            swing[name] = s
        except Exception as exc:  # noqa: BLE001
            swing[name] = {"error": str(exc)[:300]}
        print("swing", name, swing[name], flush=True)
    report["swing_daily_modelled"] = {"universe": sorted(tapes), "variants": swing}

    # ── Part B: minute modes on real option candles ──────────────────────
    nfo = await client._load_nfo_instruments()
    candidate_universe = [
        contract for contract in nfo if isinstance(contract, Mapping)
        and str(contract.get("name") or "") in INTRADAY_UNDERLYINGS
        and contract.get("instrument_type") in ("CE", "PE")
    ]
    report["minute_instrument_master_provenance"] = current_instrument_master_provenance(
        candidate_universe, now.isoformat())
    # This is a CURRENT, non-date-effective broker dump. A historical
    # strategy profitability certificate needs frozen masters for EACH
    # selection date plus the actual hedge tape, not only these quotes.
    intraday = {m: [] for m in MODES}
    minute_cache: dict[tuple[int, str, int], list[list[float]]] = {}

    async def minute_history(token: int, interval: str, days: int, tf: int):
        # Ultra/scalp reuse one real tape rather than hammering Kite twice.
        key = (int(token), interval, days)
        if key not in minute_cache:
            minute_cache[key] = await history(
                client, token, interval, now - timedelta(days=days), now,
                55 if tf == 1 else 180, preserve_duplicates=True)
        return minute_cache[key]

    for und in INTRADAY_UNDERLYINGS:
        if und not in tokens:
            for mode in MODES:
                for kind in ("CE", "PE"):
                    intraday[mode].append({
                        "underlying": und, "option_type": kind,
                        "skipped": "underlying token unavailable",
                        "replay_complete": False,
                    })
            continue
        try:
            spot_rows = await history(client, tokens[und], "day", now - timedelta(days=10), now, 30)
        except Exception as exc:  # noqa: BLE001 - report gaps, do not select winners
            for mode in MODES:
                for kind in ("CE", "PE"):
                    intraday[mode].append({
                        "underlying": und, "option_type": kind,
                        "skipped": f"underlying history unavailable: {type(exc).__name__}",
                        "replay_complete": False,
                    })
            continue
        spot = spot_rows[-1][4] if spot_rows else None
        opts = [r for r in nfo if isinstance(r, Mapping)
                and str(r.get("name")) == und and r.get("instrument_type") in ("CE", "PE")]
        expiries = eligible_expiries(opts, now.date())
        if not spot or not expiries:
            reason = "underlying price unavailable" if not spot else "eligible expiry unavailable"
            for mode in MODES:
                for kind in ("CE", "PE"):
                    intraday[mode].append({
                        "underlying": und, "option_type": kind, "skipped": reason,
                        "replay_complete": False,
                    })
            continue
        # Next monthly expiry: the last expiry in the next calendar month that has one.
        months = {}
        for e in expiries:
            months.setdefault(e[:7], []).append(e)
        monthly = [max(v) for k, v in sorted(months.items())]
        expiry = monthly[1] if len(monthly) > 1 and monthly[0] <= (now.date() + timedelta(days=10)).isoformat() else monthly[0]
        for kind in ("CE", "PE"):
            cands = [r for r in opts if str(r.get("expiry"))[:10] == expiry and r.get("instrument_type") == kind]
            pick = choose_option_contract(cands, spot)
            if pick is None:
                for mode in MODES:
                    intraday[mode].append({
                        "underlying": und, "option_type": kind,
                        "skipped": "eligible contract metadata unavailable",
                        "replay_complete": False,
                    })
                continue
            for mode, (interval, tf, days) in MODES.items():
                try:
                    start = now - timedelta(days=days)
                    u = await minute_history(tokens[und], interval, days, tf)
                    o = await minute_history(int(pick["instrument_token"]), interval, days, tf)
                    # These maps are used ONLY after coverage validates there
                    # were no duplicate or off-grid bars for accepted sessions.
                    by_u = {r[0]: r for r in u}; by_o = {r[0]: r for r in o}
                    coverage = paired_session_coverage(u, o, start=start.date(), end=now.date(),
                                                       interval_minutes=tf, as_of=now)
                    keep = coverage.pop("accepted_timestamps")
                    if len(keep) < 50:
                        intraday[mode].append({"underlying": und, "option": pick["tradingsymbol"],
                                               "coverage": coverage, "skipped": "too few complete days",
                                               "evidence_type": "REAL_MARKET_SIM", "replay_complete": False})
                        continue
                    ub = _bars([by_u[t] for t in keep]); ob = _bars([by_o[t] for t in keep])
                    cfg = validate({"trading_mode": mode})
                    res = replay_intraday(ub, ob, cfg, und, option_type=kind,
                                          lot_size=int(pick.get("lot_size") or 1), option_symbol=pick["tradingsymbol"])
                    candidate = replay_intraday(
                        ub, ob, cfg, und, option_type=kind,
                        lot_size=int(pick.get("lot_size") or 1),
                        option_symbol=pick["tradingsymbol"],
                        candidate_policy=CHALLENGER_ID,
                    )
                    candidate_report = {
                        "underlying": und, "option": pick["tradingsymbol"],
                        "bars": len(keep), "coverage": dict(coverage),
                        "days": len({datetime.fromtimestamp(t, IST).date() for t in keep}),
                        **contract_replay_summary(candidate),
                        "closed_trade_audit": closed_trade_audit(candidate),
                        "candidate_policy": CHALLENGER_ID,
                        "challenger_rejections": candidate["challenger_rejections"],
                    }
                    intraday[mode].append({"underlying": und, "option": pick["tradingsymbol"],
                                           "bars": len(keep), "coverage": coverage,
                                           "days": len({datetime.fromtimestamp(t, IST).date() for t in keep}),
                                           **contract_replay_summary(res),
                                           "closed_trade_audit": closed_trade_audit(res),
                                           "cost_feasible_challenger": candidate_report})
                except Exception as exc:  # noqa: BLE001
                    intraday[mode].append({"underlying": und, "option": pick.get("tradingsymbol"),
                                           "error": f"{type(exc).__name__}: {exc}"[:240],
                                           "replay_complete": False})
                print(mode, und, kind, intraday[mode][-1], flush=True)
    totals = {mode: mode_totals(rows) for mode, rows in intraday.items()}
    # Historical report outputs are diagnostic, and missing/failed contracts
    # remain visible in counts instead of disappearing from profitable totals.
    report["minute_modes_real_options"] = {"totals": totals, "rows": intraday}
    challenger_rows = {}
    for mode, rows in intraday.items():
        challenger_rows[mode] = [
            row["cost_feasible_challenger"] if "cost_feasible_challenger" in row
            else {key: row[key] for key in (
                "underlying", "option", "option_type", "skipped", "error",
                "coverage", "replay_complete")
                if key in row}
            for row in rows
        ]
    report["minute_challenger_research"] = {
        "policy": CHALLENGER_ID,
        "research_only": True, "historical_selection_bias": True,
        "complete_after_cost_hedge_results": False,
        "independent_untouched_oos_evidence": False,
        "baseline_is_unchanged": True,
        "totals": {mode: mode_totals(rows) for mode, rows in challenger_rows.items()},
        "rows": challenger_rows,
    }
    path = OUT / f"snapback_timeframes_backtest_{now.date().isoformat()}.json"
    # Atomic no-overwrite publish: failed/disk-full research runs never
    # leave a truncated file that could be mistaken for verified evidence.
    write_research_report_once(path, report)
    print(json.dumps({"swing": swing, "minute_totals": totals}, indent=2, default=str))
    print("written", path)


if __name__ == "__main__":
    asyncio.run(main())
