from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3

import pytest

from app.core.acceptance_evidence import AcceptanceClaim, AcceptanceRegister, gate_status

SHA = "a" * 40


def claim(tmp_path, **changes):
    path = tmp_path / "receipt.log"
    path.write_text("independent synthetic test fixture; not actual acceptance\n")
    values = dict(claim_id="G4", source_sha=SHA, runtime_release_sha=SHA,
                  observed_at=datetime.now(timezone.utc).isoformat(), source_type="LOCAL_TEST",
                  expires_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                  result="VERIFIED", log_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
                  evidence_path=str(path), observer="engineer", independent_reviewer="reviewer")
    values.update(changes)
    return AcceptanceClaim(**values)


def test_missing_register_is_unknown_and_read_does_not_create_it(tmp_path):
    store = AcceptanceRegister(tmp_path / "missing" / "register.db")
    assert store.read() == []
    assert not store.path.parent.exists()
    assert gate_status(None) == "UNKNOWN"


def test_roundtrip_and_changed_artifact_invalidates_evidence(tmp_path):
    store = AcceptanceRegister(tmp_path / "register.db")
    original = claim(tmp_path)
    event_hash = store.append(original)
    assert len(event_hash) == 64
    assert store.read() == [original]
    assert gate_status(store.read()[0]) == "VERIFIED"
    (tmp_path / "receipt.log").write_text("changed")
    assert gate_status(store.read()[0]) == "UNKNOWN"
    # The accepted receipt is sealed in the register. Changing the source log
    # cannot rewrite history, and an isolated restore needs no original path.
    assert store.status(store.read()[0]) == "VERIFIED"
    restored = AcceptanceRegister(tmp_path / "restored.db")
    with sqlite3.connect(store.path) as original, sqlite3.connect(restored.path) as target:
        original.backup(target)
    (tmp_path / "receipt.log").unlink()
    assert restored.status(restored.read()[0]) == "VERIFIED"


@pytest.mark.parametrize("changes", [
    {"source_type": "CODE_IMPLEMENTED"}, {"source_type": "RESEARCH"},
    {"source_sha": "a" * 39}, {"runtime_release_sha": "../other"},
    {"independent_reviewer": "engineer"}, {"independent_reviewer": ""},
    {"log_hash": "b" * 64}, {"result": "PASS"},
    {"observed_at": "2026-01-01T10:00:00"},
    {"observed_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()},
    {"expires_at": ""},
])
def test_invalid_or_self_certified_evidence_cannot_be_verified(tmp_path, changes):
    with pytest.raises(ValueError):
        AcceptanceRegister(tmp_path / "register.db").append(claim(tmp_path, **changes))


def test_failure_can_be_recorded_without_manufacturing_receipts(tmp_path):
    failure = claim(tmp_path, result="FAILED", evidence_path="", log_hash="", independent_reviewer="")
    store = AcceptanceRegister(tmp_path / "register.db")
    store.append(failure)
    assert gate_status(store.read()[0]) == "FAILED"


def test_expired_evidence_is_unknown(tmp_path):
    now = datetime.now(timezone.utc)
    expired = claim(tmp_path, observed_at=(now - timedelta(days=2)).isoformat(),
                    expires_at=(now - timedelta(days=1)).isoformat())
    assert gate_status(expired) == "UNKNOWN"


def test_update_delete_and_hash_tampering_are_detected(tmp_path):
    store = AcceptanceRegister(tmp_path / "register.db")
    store.append(claim(tmp_path))
    with sqlite3.connect(store.path) as conn:
        for sql in ("DELETE FROM claims", "UPDATE claims SET payload='{}'"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                conn.execute(sql)
        conn.execute("DROP TRIGGER claims_no_update")
        conn.execute("UPDATE claims SET payload='{}'")
    with pytest.raises(ValueError, match="hash chain"):
        store.read()
    with pytest.raises(ValueError, match="hash chain"):
        store.append(claim(tmp_path))


def test_concurrent_append_preserves_all_claims(tmp_path):
    store = AcceptanceRegister(tmp_path / "register.db")
    original = claim(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        hashes = list(pool.map(lambda i: store.append(replace(original, detail=str(i))), range(20)))
    assert len(set(hashes)) == 20
    assert len(store.read()) == 20


def economic_report(**changes):
    report = dict(schema_version=1, dataset_id="frozen-test-dataset", strategy_id="sterling-kite",
                  mode="swing", strategy_version=SHA, config_hash="c" * 64, runtime_release_sha=SHA,
                  hypothesis_registered_at="2026-01-01T00:00:00Z",
                  forward_start="2026-06-01T00:00:00Z", forward_end="2026-08-01T00:00:00Z",
                  oos_windows=[{"start": "2026-02-01T00:00:00Z", "end": "2026-03-01T00:00:00Z", "net_expectancy": 1},
                               {"start": "2026-04-01T00:00:00Z", "end": "2026-05-01T00:00:00Z", "net_expectancy": 1}],
                  confidence_method="session_cluster_bootstrap", confidence_level=.95,
                  net_expectancy=1, expectancy_lower_bound=.01, required_sessions=60, observed_sessions=90,
                  required_trades=300, observed_trades=500, forward_evidence_type="REAL_MARKET_SIM",
                  stress_expectancy={k: .01 for k in ("fees", "latency", "stop_slippage", "liquidity", "adverse_regime", "best_trade_removed", "best_symbol_removed")})
    report.update({key: True for key in ("data_integrity", "sessions_complete", "no_lookahead", "purge_and_embargo",
                   "multiple_testing_recorded", "holdout_unused", "sample_adequacy_reviewed", "forward_quote_reconciled",
                   "exceptions_and_missed_fills_counted")})
    for metric in ("drawdown", "expected_shortfall", "turnover", "capital_use"):
        report[metric], report[f"max_{metric}"] = 1, 10
    report.update(changes)
    return report


def test_economic_report_contract_accepts_only_full_independently_reviewed_evidence(tmp_path):
    report = economic_report()
    base = claim(tmp_path, claim_id="G7", source_type="RESEARCH", dataset_id=report["dataset_id"],
                 strategy_id=report["strategy_id"], mode=report["mode"], strategy_version=SHA,
                 config_hash=report["config_hash"])
    path = tmp_path / "economic.json"
    path.write_text(json.dumps(report))
    economic = replace(base, evidence_path=str(path), log_hash=hashlib.sha256(path.read_bytes()).hexdigest())
    economic.validate()
    assert gate_status(economic) == "VERIFIED"


@pytest.mark.parametrize("corrupt", [
    {"oos_windows": [None, {"start": "2026-04-01T00:00:00Z", "end": "2026-05-01T00:00:00Z", "net_expectancy": 1}]},
    {"stress_expectancy": []},
    {"stress_expectancy": "all scenarios passed"},
])
def test_malformed_economic_receipts_fail_closed(corrupt):
    from app.core.economic_acceptance import validate_economic_report
    with pytest.raises(ValueError):
        validate_economic_report(economic_report(**corrupt), economic_report())


def test_non_object_economic_report_is_rejected():
    from app.core.economic_acceptance import validate_economic_report
    with pytest.raises(ValueError):
        validate_economic_report([], economic_report())


@pytest.mark.parametrize("changes", [
    {"sessions_complete": False}, {"no_lookahead": False}, {"forward_evidence_type": "MODELLED"},
    {"expectancy_lower_bound": 0}, {"net_expectancy": -1}, {"net_expectancy": float("nan")},
    {"observed_trades": 0}, {"required_trades": 0}, {"max_drawdown": 0}, {"drawdown": 11},
    {"config_hash": "other"}, {"holdout_unused": False}, {"stress_expectancy": {}},
    {"hypothesis_registered_at": "2026-04-02T00:00:00Z"},
    {"forward_end": "2099-01-01T00:00:00Z"},
])
def test_economic_contract_rejects_incomplete_negative_and_modelled_evidence(changes):
    from app.core.economic_acceptance import validate_economic_report
    identity = economic_report()
    with pytest.raises(ValueError):
        validate_economic_report(economic_report(**changes), identity)
