"""SuperTrend per-mode sweep: SuperTrend lines and exit rule chosen per trading mode, on real lake bars.

Each of the five modes runs on its own signal timeframe (``lanes.MODE_SIGNAL_TIMEFRAMES``) and
holding rules. For every mode this sweeps the three SuperTrend lines (period and multiplier sets)
and the red-count exit rule, scoring the live engine's own entries and exits (``compute_regime``,
``entry_transitions``, the exit-mode study's ``_exit_bar``) on the underlying (delta-1 lens, with
the exit-mode study's cost). The first 70% of each index's bars picks the winner; the last 30% must
confirm it. A mode only moves off the frozen core when its winner is positive out of sample on at
least 3 of 4 indices and beats the core out of sample; otherwise the core stays.

Bars come from the offline lake (``KITELAKE_ROOT`` or the backup drive), read-only.

Usage:
    PYTHONWARNINGS=ignore .venv/bin/python -m study.kite_st_mode_sweep
"""
from __future__ import annotations

import itertools
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engines.common.exit_counter import exit_needs_counter_signal, get_exit_threshold  # noqa: E402
from app.engines.sterling_kite_engine.config import SterlingKiteEngineConfig  # noqa: E402
from app.engines.sterling_kite_engine.regime import compute_regime, entry_transitions  # noqa: E402
from study.kite_st_exit_mode_sweep import DELTA1_COST_BPS, OOS_FRAC, _exit_bar  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
LAKE = Path(os.environ.get("KITELAKE_ROOT", "/run/media/nageshmadaram/3331-6535/SterlingLakeBackup/SterlingLake"))
OUT = Path(__file__).resolve().parents[2] / "docs" / "operations"
INDICES = [("NSE", "256265__NIFTY_50"), ("NSE", "260105__NIFTY_BANK"),
           ("NSE", "257801__NIFTY_FIN_SERVICE"), ("BSE", "265__SENSEX")]

# Holding rules per mode: lake interval, max bars held (None = no cap), whether the position must be
# flat by 15:15, the latest entry time, and the session cap (None = same day only via flat_by).
MODES = {
    "ultra_scalping": dict(interval="minute", max_bars=20, flat_by=(15, 15), last_entry=(15, 0), sessions=None),
    "scalping": dict(interval="5minute", max_bars=12, flat_by=(15, 15), last_entry=(14, 45), sessions=None),
    "intraday": dict(interval="15minute", max_bars=None, flat_by=(15, 15), last_entry=(14, 30), sessions=None),
    "overnight": dict(interval="60minute", max_bars=None, flat_by=None, last_entry=(15, 0), sessions=3),
    "swing": dict(interval="60minute", max_bars=None, flat_by=None, last_entry=(15, 0), sessions=10),
}
PERIODS = [(21, 14, 7), (14, 10, 5), (30, 21, 10), (10, 7, 3)]
MULTIPLIERS = [(1.0, 2.0, 3.0), (0.75, 1.5, 2.25), (1.5, 2.5, 3.5), (1.25, 2.5, 3.75)]
EXITS = ["one_red", "two_red"]
CORE = ((21, 14, 7), (1.0, 2.0, 3.0), "one_red")
MIN_TRADES_IS, MIN_TRADES_OOS = 30, 10


def load(exchange: str, stem: str, interval: str) -> dict | None:
    path = LAKE / "bars" / f"interval={interval}" / f"exchange={exchange}" / "segment=INDICES" / f"{stem}.parquet"
    if not path.exists():
        return None
    t = pq.read_table(path, columns=["ts", "open", "high", "low", "close"]).to_pandas().sort_values("ts")
    scale = 100.0 if t["close"].median() > 1e6 else 1.0  # prices are stored scaled in some files
    ts = t["ts"].dt.tz_convert(IST)
    return {"ts": ts.to_numpy(), "o": t["open"].to_numpy(float) / scale, "h": t["high"].to_numpy(float) / scale,
            "l": t["low"].to_numpy(float) / scale, "c": t["close"].to_numpy(float) / scale}


def forced_exit(i: int, ts, rules: dict) -> int:
    """First bar the mode's holding rules force an exit at (or the last bar)."""
    n, start = len(ts), ts[i]
    day0 = start.date() if hasattr(start, "date") else datetime.fromtimestamp(start.astype("int64") / 1e9, IST).date()
    sessions_seen, last_day = 1, day0
    for j in range(i + 1, n):
        t = ts[j]
        d = t.date()
        if d != last_day:
            # Overnight and swing never carry over a weekend or holiday gap of more than a day.
            if rules["sessions"] is not None and (d - last_day).days > 1 and rules["sessions"] <= 3:
                return j - 1
            sessions_seen += 1;last_day = d
            if rules["sessions"] is None or sessions_seen > rules["sessions"]:
                return j - 1
        if rules["max_bars"] is not None and j - i >= rules["max_bars"]:
            return j
        if rules["flat_by"] is not None and (t.hour, t.minute) >= rules["flat_by"]:
            return j
    return n - 1


def replay(data: dict, cfg: SterlingKiteEngineConfig, rules: dict) -> list[float]:
    o, h, l, c, ts = data["o"], data["h"], data["l"], data["c"], [x for x in data["ts"]]
    n = len(c)
    if n <= cfg.warmup + 2:
        return []
    r = compute_regime(o, h, l, c, cfg)
    longs, shorts = entry_transitions(r)
    threshold, needs_signal = get_exit_threshold(cfg.exit_mode), exit_needs_counter_signal(cfg.exit_mode)
    out, i = [], 0
    while i < n:
        if not longs[i]:  # long only, as the live engine (allow_short=False)
            i += 1;continue
        t = ts[i]
        if (t.hour, t.minute) > rules["last_entry"]:
            i += 1;continue
        sig_exit, _ = _exit_bar(i=i, want=1, t_fast=r.t_fast, t_mid=r.t_mid, t_slow=r.t_slow, longs=longs,
                                shorts=shorts, threshold=threshold, needs_signal=needs_signal, n=n)
        exit_i = min(sig_exit, forced_exit(i, ts, rules))
        move = (float(c[exit_i]) - float(c[i])) / float(c[i])
        out.append(move - DELTA1_COST_BPS / 1e4)
        i = exit_i + 1
    return out


def stats(rets: list[float]) -> dict:
    if not rets:
        return {"trades": 0, "mean_bps": 0.0, "total_pct": 0.0, "win": 0.0}
    a = np.array(rets)
    return {"trades": len(a), "mean_bps": round(float(a.mean() * 1e4), 2), "total_pct": round(float(a.sum() * 100), 2),
            "win": round(float((a > 0).mean()), 3)}


def main() -> None:
    report: dict = {"generated": datetime.now(IST).isoformat(), "cost_bps": DELTA1_COST_BPS, "oos_frac": OOS_FRAC,
                    "trials_per_mode": len(PERIODS) * len(MULTIPLIERS) * len(EXITS), "modes": {}}
    for mode, rules in MODES.items():
        series = {stem: load(ex, stem, rules["interval"]) for ex, stem in INDICES}
        series = {k: v for k, v in series.items() if v is not None and len(v["c"]) > 200}
        rows = []
        for (periods, mults, exit_mode) in itertools.product(PERIODS, MULTIPLIERS, EXITS):
            cfg = SterlingKiteEngineConfig(horizon_mode="swing", fast=(periods[0], mults[0]), mid=(periods[1], mults[1]),
                                           slow=(periods[2], mults[2]), exit_mode=exit_mode)
            per_index, is_all, oos_all = {}, [], []
            for stem, data in series.items():
                n = len(data["c"]);cut = int(n * (1 - OOS_FRAC))
                part = lambda lo, hi: {k: v[lo:hi] for k, v in data.items()}
                is_r, oos_r = replay(part(0, cut), cfg, rules), replay(part(cut, n), cfg, rules)
                is_all += is_r;oos_all += oos_r
                per_index[stem] = {"is": stats(is_r), "oos": stats(oos_r)}
            rows.append({"periods": periods, "multipliers": mults, "exit_mode": exit_mode,
                         "is": stats(is_all), "oos": stats(oos_all),
                         "oos_positive_indices": sum(1 for v in per_index.values() if v["oos"]["trades"] and v["oos"]["mean_bps"] > 0),
                         "per_index": per_index})
        core = next(r for r in rows if (r["periods"], r["multipliers"], r["exit_mode"]) == CORE)
        eligible = [r for r in rows if r["is"]["trades"] >= MIN_TRADES_IS and r["oos"]["trades"] >= MIN_TRADES_OOS]
        best = max(eligible, key=lambda r: r["is"]["mean_bps"]) if eligible else None
        accepted = bool(best and best["oos"]["mean_bps"] > 0 and best["oos_positive_indices"] >= 3
                        and best["oos"]["mean_bps"] > core["oos"]["mean_bps"])
        chosen = best if accepted else core
        report["modes"][mode] = {"interval": rules["interval"], "indices": sorted(series), "core": core, "best_in_sample": best,
                                 "accepted": accepted, "chosen": {k: chosen[k] for k in ("periods", "multipliers", "exit_mode", "is", "oos", "oos_positive_indices")},
                                 "all": sorted(rows, key=lambda r: -r["is"]["mean_bps"])[:10]}
        print(f"{mode:15s} {rules['interval']:9s} core OOS {core['oos']['mean_bps']:+7.2f}bps ({core['oos']['trades']}t) | "
              f"best IS {best and best['periods']} {best and best['multipliers']} {best and best['exit_mode']} "
              f"OOS {best['oos']['mean_bps'] if best else 0:+7.2f}bps {best['oos_positive_indices'] if best else 0}/4 -> "
              f"{'ACCEPT' if accepted else 'keep core'}", flush=True)
    path = OUT / f"supertrend_mode_sweep_{date.today().isoformat()}.json"
    path.write_text(json.dumps(report, indent=1, default=str))
    print("written", path)


if __name__ == "__main__":
    main()
