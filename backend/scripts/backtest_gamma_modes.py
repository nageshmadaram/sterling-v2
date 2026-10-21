"""Backtest Gamma-Unwind trading modes on recent real data.

For each recent trading day the historical replay rebuilds the day's 4/4
ENTRY signals exactly as the live engine would. Every ENTRY is then held under
each of the five modes using that contract's own 1-minute option bars from
Kite, and the underlying's daily closes for the D68 swing stop.

Limits, stated in the report:
- Kite sells no history for expired options, so only contracts still listed
  can be replayed. That caps the window at the current expiry cycle.
- Fills are at the signal's entry price and at each exit level (no queue
  model). A bar that touches both the stop and a target counts as the stop.
- Costs: a flat slippage share of premium on each leg plus fixed charges.

Usage:
    PYTHONWARNINGS=ignore .venv/bin/python scripts/backtest_gamma_modes.py --days 15
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import replace
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.runtime_env import load_sterling_env  # noqa: E402

load_sterling_env()

from app.engines.gamma_move import tick_action  # noqa: E402
from app.engines.gamma_move.config import GammaMoveConfig  # noqa: E402
from app.engines.gamma_move.models import GammaSignal, PositionState, TIER_ENTRY  # noqa: E402
from app.engines.gamma_move.modes import TRADE_MODES, resolve_mode  # noqa: E402
from app.engines.gamma_move.exit import d68_action  # noqa: E402
from app.engines.gamma_move.selection import is_trading_day, trading_days_between  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
SLIPPAGE = 0.01          # 1% of premium per leg
FIXED_PER_TRADE = 60.0   # brokerage + statutory, rupees per round trip
OUT = Path(__file__).resolve().parents[2] / "docs" / "operations"


def _candles(raw) -> list[dict]:
    rows = raw.get("candles") if isinstance(raw, dict) else raw
    out = []
    for r in rows or []:
        ts = r[0] if isinstance(r, (list, tuple)) else r.get("date")
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=IST)
        o, h, l, c = (r[1], r[2], r[3], r[4]) if isinstance(r, (list, tuple)) else (r["open"], r["high"], r["low"], r["close"])
        out.append({"t": ts.astimezone(IST), "o": float(o), "h": float(h), "l": float(l), "c": float(c)})
    return out


def simulate(sig: GammaSignal, mode: str, bars: list[dict], daily: dict[date, float],
             cfg: GammaMoveConfig) -> dict | None:
    """Hold one ENTRY under one mode. Returns the trade or None if no bars."""
    entry = float(sig.entry)
    if entry <= 0 or not bars:
        return None
    day0 = datetime.fromtimestamp(sig.at_ms / 1000, IST).date()
    pos = PositionState.from_signal(sig, fill=entry, quantity=1, lots=1, at_ms=sig.at_ms,
                                    day=day0.isoformat(), trade_mode=mode)
    pos.status = "open"
    rules = resolve_mode(mode, sig.contract.dte)
    expiry = date.fromisoformat(pos.instrument.expiry[:10]) if pos.instrument.expiry else None
    cap = pos.max_sessions or cfg.swing_days
    last_day = day0
    prev_close = entry
    exit_px, reason, exit_t = None, None, None
    for b in bars:
        t, d = b["t"], b["t"].date()
        if t.timestamp() * 1000 <= sig.at_ms:
            continue
        if d != last_day:
            # Day boundary: close of the previous session decides swing rules.
            if pos.hold == "intraday":
                exit_px, reason = prev_close, "EOD"
                break
            k = trading_days_between(day0, last_day)
            close = daily.get(last_day)
            if close is not None and pos.trade_state == "OPEN":
                act = d68_action(pos.instrument.option_type, pos.zone_edge, close, pos.swing_armed)
                if act == "arm":
                    pos.swing_armed = True
                elif act == "stop":
                    exit_px, reason = prev_close, "D68"
                    break
            if k >= cap:
                exit_px, reason = prev_close, "DAY5" if cap == cfg.swing_days else "SESSION_LIMIT"
                break
            if expiry and last_day >= expiry:
                exit_px, reason = prev_close, "EXPIRY"
                break
            if pos.no_gap_carry and not is_trading_day(last_day + timedelta(days=1)):
                exit_px, reason = prev_close, "GAP"
                break
            last_day = d
        # Within the bar: adverse extreme first, then favourable.
        for px in (b["l"], b["h"]):
            event, level = tick_action(pos, px)
            if event in ("SL", "TRAIL"):
                exit_px, reason = level, event
                break
            if event == "T2":
                exit_px, reason = pos.t2, "T2"
                break
        if reason:
            exit_t = t
            break
        if pos.time_limit_min and (t.timestamp() * 1000 - sig.at_ms) >= pos.time_limit_min * 60_000:
            exit_px, reason, exit_t = b["c"], "TIME", t
            break
        if pos.hold == "intraday" and t.time() >= dtime(15, 20):
            exit_px, reason, exit_t = b["c"], "EOD", t
            break
        prev_close = b["c"]
    else:
        exit_px, reason = bars[-1]["c"], "OPEN_AT_END"
        exit_t = bars[-1]["t"]
    lot = int(pos.instrument.lot_size or 1)
    gross = (exit_px - entry) * lot
    cost = (entry + exit_px) * SLIPPAGE * lot + FIXED_PER_TRADE
    return {"mode": mode, "hold": rules.hold, "symbol": pos.instrument.tradingsymbol,
            "day": day0.isoformat(), "entry": entry, "exit": round(exit_px, 2), "reason": reason,
            "lot": lot, "gross": round(gross, 2), "net": round(gross - cost, 2),
            "minutes": round(((exit_t or bars[-1]["t"]).timestamp() * 1000 - sig.at_ms) / 60000)}


async def main(days: int) -> None:
    from app.services import gamma_move_historical as hist
    from app.services.exchanges.kite import accounts
    from app.services.gamma_move import get_config

    accounts.bootstrap()
    acct = next((a for a in accounts.all_accounts() if not a.is_paper and a.is_active and a.access_token_enc), None) \
        or next(iter(accounts.all_accounts()), None)
    if acct is None:
        raise SystemExit("no Kite account")
    uid = str(acct.user_id)
    cfg = get_config(uid)
    client = await accounts.acquire_client(acct)

    today = datetime.now(IST).date()
    day, sessions = today, []
    while len(sessions) < days:
        day -= timedelta(days=1)
        if is_trading_day(day):
            sessions.append(day)
    sessions.reverse()

    signals: list[GammaSignal] = []
    coverage = []
    for d in sessions:
        res = await hist.replay_day(uid, d, replace(cfg, trade_mode="auto"))
        entries = [s for s in res.get("signals", []) if str(s.get("tier")) == TIER_ENTRY]
        tiers = {}
        for s in res.get("signals", []):
            tiers[str(s.get("tier"))] = tiers.get(str(s.get("tier")), 0) + 1
        notes = res.get("coverage") or []
        coverage.append({"day": d.isoformat(), "entries": len(entries), "complete": res.get("complete"),
                         "issue": res.get("issue"), "watchlist": len(res.get("watchlist") or []),
                         "tiers": tiers, "gaps": len(notes), "gap_samples": [str(n)[:160] for n in notes[:5]]})
        print("   watch", len(res.get("watchlist") or []), "tiers", tiers, "gaps", len(notes), flush=True)
        print(f"{d} entries={len(entries)} complete={res.get('complete')} {res.get('issue') or ''}", flush=True)
        for s in entries:
            try:
                signals.append(GammaSignal.from_dict(s))
            except Exception as exc:  # noqa: BLE001
                coverage[-1].setdefault("bad", []).append(str(exc))

    trades, skipped = [], 0
    for sig in signals:
        inst = sig.contract.instrument
        start = datetime.fromtimestamp(sig.at_ms / 1000, IST)
        end = min(datetime.now(IST), start + timedelta(days=9))
        try:
            raw = await client.get_historical(int(inst.instrument_id), "minute",
                                              start.strftime("%Y-%m-%d %H:%M:%S"),
                                              end.strftime("%Y-%m-%d %H:%M:%S"), False, False)
            bars = _candles(raw)
            raw_d = await client.get_historical(int(sig.contract.underlying_token), "day",
                                                start.date().isoformat(), end.date().isoformat(), False, False)
            daily = {b["t"].date(): b["c"] for b in _candles(raw_d)}
        except Exception as exc:  # noqa: BLE001
            print("skip", inst.tradingsymbol, exc, flush=True)
            skipped += 1
            continue
        for mode in ("auto", *TRADE_MODES):
            t = simulate(sig, mode, bars, daily, cfg)
            if t:
                trades.append(t)
        await asyncio.sleep(0.4)

    summary = {}
    for mode in ("auto", *TRADE_MODES):
        rows = [t for t in trades if t["mode"] == mode]
        if not rows:
            continue
        wins = [t for t in rows if t["net"] > 0]
        summary[mode] = {
            "trades": len(rows), "win_rate": round(len(wins) / len(rows), 3),
            "net_inr": round(sum(t["net"] for t in rows), 2),
            "avg_net_inr": round(sum(t["net"] for t in rows) / len(rows), 2),
            "worst_inr": round(min(t["net"] for t in rows), 2),
            "best_inr": round(max(t["net"] for t in rows), 2),
            "avg_minutes": round(sum(t["minutes"] for t in rows) / len(rows)),
            "exits": {r: sum(1 for t in rows if t["reason"] == r) for r in sorted({t["reason"] for t in rows})},
        }
    out = {"generated": datetime.now(IST).isoformat(), "sessions": [d.isoformat() for d in sessions],
           "signals": len(signals), "skipped_no_data": skipped, "costs": {"slippage_per_leg": SLIPPAGE,
           "fixed_per_trade_inr": FIXED_PER_TRADE}, "coverage": coverage, "summary": summary, "trades": trades}
    path = OUT / f"gamma_modes_backtest_{today.isoformat()}.json"
    path.write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(summary, indent=2))
    print("written", path)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=15)
    asyncio.run(main(ap.parse_args().days))
