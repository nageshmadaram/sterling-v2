#!/usr/bin/env python3
"""Read-only historical strategy evidence matrix.

Never promotes a strategy or modifies a live release/evidence register. Backtest
artifacts can motivate further research but cannot establish live profits.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = {
    "supertrend": "supertrend_mode_sweep_2026-10-22.json",
    "ema_trend": "ema_modes_backtest_2026-10-22_fee3bps.json",
    "gamma_unwind": "gamma_modes_backtest_2026-10-21.json",
    "snapback": "snapback_timeframes_backtest_2026-10-22.json",
}
# A historical replay never supplies these independent release observations.
REQUIRED_INDEPENDENT_PROOF = (
    "PREREGISTERED_OOS_AND_FORWARD_REPLAY",
    "INDEPENDENT_DATA_AND_COST_RECONCILIATION",
    "CURRENT_SHA_BROKER_EXECUTION_CERTIFICATION",
)


def record(strategy: str, mode: str, *, metrics: dict, blockers: list[str]) -> dict:
    return {
        "strategy_id": strategy, "mode": mode, "historical_metrics": metrics,
        "historical_problems": sorted(set(blockers)),
        "r3_economic_verified": False,
        "new_live_entries_authorized": False,
        "missing_independent_proof": list(REQUIRED_INDEPENDENT_PROOF),
    }


def analyze(strategy: str, obj: dict) -> list[dict]:
    if strategy == "supertrend":
        rows = []
        for mode, data in sorted(obj["modes"].items()):
            oos = data.get("core", {}).get("oos", {})
            net = oos.get("total_pct")
            reasons = ["HISTORICAL_MODE_NOT_ACCEPTED"] if data.get("accepted") is not True else []
            if not isinstance(net, (float, int)) or isinstance(net, bool) or net <= 0:
                reasons.append("OUT_OF_SAMPLE_NONPOSITIVE_OR_MISSING")
            rows.append(record(strategy, mode, metrics={
                "oos_total_pct": net, "oos_trades": oos.get("trades"),
                "historical_accepted": data.get("accepted") is True,
            }, blockers=reasons))
        return rows
    if strategy == "ema_trend":
        rows = []
        for mode, data in sorted(obj["summary"].items()):
            trades, net = data.get("trades", 0), data.get("net_inr", 0)
            reasons = ["NO_HISTORICAL_TRADES"] if not trades else []
            if not isinstance(net, (float, int)) or isinstance(net, bool) or net <= 0:
                reasons.append("HISTORICAL_NET_NONPOSITIVE")
            rows.append(record(strategy, mode, metrics={
                "historical_trades": trades, "historical_net_inr": net,
            }, blockers=reasons))
        return rows
    if strategy == "gamma_unwind":
        complete = sum(bool(s.get("complete")) for s in obj.get("coverage", []))
        requested = len(obj.get("sessions", []))
        rows = []
        for mode, data in sorted(obj.get("summary", {}).items()):
            trades = data.get("trades", 0)
            reasons = []
            if complete != requested or requested == 0:
                reasons.append("INCOMPLETE_HISTORICAL_OPTION_COVERAGE")
            if trades < 2:
                reasons.append("INSUFFICIENT_TRADE_SAMPLE")
            rows.append(record(strategy, mode, metrics={
                "historical_net_inr": data.get("net_inr"),
                "historical_trades": trades,
                "complete_sessions": complete, "requested_sessions": requested,
            }, blockers=reasons))
        return rows
    if strategy == "snapback":
        rows = []
        real = obj["minute_modes_real_options"]
        for mode, data in sorted(real["totals"].items()):
            outcomes = real.get("rows", {}).get(mode, [])
            errors = sum(bool(r.get("error")) for r in outcomes)
            reasons = []
            if errors:
                reasons.append("BACKTEST_CONTRACT_ERRORS")
            if data.get("trades", 0) < 2:
                reasons.append("INSUFFICIENT_TRADE_SAMPLE")
            if data.get("profitable_contracts", 0) < 3:
                reasons.append("PROFIT_CONCENTRATION_RISK")
            rows.append(record(strategy, mode, metrics={
                "historical_trades": data.get("trades"),
                "historical_net_inr": data.get("net_inr"),
                "contract_errors": errors,
                "profitable_contracts": data.get("profitable_contracts"),
                "contracts": data.get("contracts"),
            }, blockers=reasons))
        for name, data in sorted(obj["swing_daily_modelled"]["variants"].items()):
            rows.append(record(strategy, "modelled_" + name, metrics={
                "historical_trades": data.get("trades"),
                "mean_return_pct": data.get("mean_return_per_trade_pct"),
                "median_return_pct": data.get("median_return_pct"),
            }, blockers=["MODELLED_OPTION_PRICES_NOT_LIVE_EXECUTION_EVIDENCE"]))
        return rows
    raise ValueError("unknown strategy")


def build_matrix(data_dir: Path) -> dict[str, Any]:
    results = []
    inputs: dict[str, Any] = {}
    for strategy, filename in ARTIFACTS.items():
        path = data_dir / filename
        if not path.is_file():
            inputs[strategy] = {"file": filename, "status": "MISSING"}
            results.append(record(strategy, "unknown", metrics={},
                                  blockers=["REQUIRED_HISTORICAL_ARTIFACT_MISSING"]))
            continue
        raw = path.read_bytes()
        inputs[strategy] = {
            "file": filename, "status": "READ",
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        try:
            doc = json.loads(raw)
            if not isinstance(doc, dict):
                raise ValueError("expected top-level JSON object")
            lanes = analyze(strategy, doc)
            if not lanes:
                raise ValueError("no mode records")
            results.extend(lanes)
        except (ValueError, TypeError, KeyError, AttributeError, IndexError):
            inputs[strategy]["status"] = "INVALID"
            results.append(record(strategy, "unknown", metrics={},
                                  blockers=["HISTORICAL_ARTIFACT_INVALID"]))
    return {
        "schema_version": 1,
        "evidence_scope": "HISTORICAL_BACKTESTS_ONLY",
        "release_status": "R3_NOT_CERTIFIED",
        "new_live_entries_authorized": False,
        "inputs": inputs,
        "lanes": results,
        "modes_eligible_for_live_promotion": 0,
        "note": "This is a research triage report, never an authenticated G7 acceptance receipt.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path,
                        default=ROOT / "docs/operations")
    args = parser.parse_args()
    print(json.dumps(build_matrix(args.data_dir), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
