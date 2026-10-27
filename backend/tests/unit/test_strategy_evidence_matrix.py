"""Historical model reports are never a live-profitability certificate."""
from pathlib import Path
import json
import runpy

ROOT = Path(__file__).resolve().parents[3]
module = runpy.run_path(str(ROOT / "scripts/strategy_evidence_matrix.py"))
build_matrix = module["build_matrix"]


def test_recorded_research_does_not_authorize_capital():
    report = build_matrix(ROOT / "docs/operations")
    assert report["release_status"] == "R3_NOT_CERTIFIED"
    assert report["modes_eligible_for_live_promotion"] == 0
    assert not report["new_live_entries_authorized"]
    assert report["lanes"]
    assert all(not row["r3_economic_verified"]
               and not row["new_live_entries_authorized"] for row in report["lanes"])


def test_supertrend_all_recorded_modes_fail_historical_acceptance():
    matrix = build_matrix(ROOT / "docs/operations")
    rows = [r for r in matrix["lanes"] if r["strategy_id"] == "supertrend"]
    assert len(rows) == 5
    assert all(r["historical_metrics"]["oos_total_pct"] < 0 for r in rows)
    assert all("HISTORICAL_MODE_NOT_ACCEPTED" in r["historical_problems"] for r in rows)


def test_positive_snapback_total_does_not_hide_contract_errors():
    matrix = build_matrix(ROOT / "docs/operations")
    scalp = next(r for r in matrix["lanes"]
                 if r["strategy_id"] == "snapback" and r["mode"] == "ultra_scalping")
    assert scalp["historical_metrics"]["historical_net_inr"] > 0
    assert scalp["historical_metrics"]["contract_errors"] == 3
    assert "BACKTEST_CONTRACT_ERRORS" in scalp["historical_problems"]
    assert not scalp["new_live_entries_authorized"]


def test_gamma_incomplete_tapes_and_modelled_swing_remain_blocked():
    matrix = build_matrix(ROOT / "docs/operations")
    assert any("INCOMPLETE_HISTORICAL_OPTION_COVERAGE" in r["historical_problems"]
               for r in matrix["lanes"] if r["strategy_id"] == "gamma_unwind")
    assert all("MODELLED_OPTION_PRICES_NOT_LIVE_EXECUTION_EVIDENCE" in r["historical_problems"]
               for r in matrix["lanes"] if r["strategy_id"] == "snapback"
               and r["mode"].startswith("modelled_"))


def test_missing_and_corrupt_sources_are_explicitly_refused(tmp_path):
    missing = build_matrix(tmp_path)
    assert all(v["status"] == "MISSING" for v in missing["inputs"].values())
    assert len(missing["lanes"]) == 4
    filename = module["ARTIFACTS"]["supertrend"]
    (tmp_path / filename).write_text('{"modes": "invalid"}')
    result = build_matrix(tmp_path)
    assert result["inputs"]["supertrend"]["status"] == "INVALID"
    assert not result["new_live_entries_authorized"]
