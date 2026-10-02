from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.services import release_certification as cert


SHA = "a" * 40


def _payload(*, sha: str = SHA, overall: str = "PASS", reconnect: str = "PASS"):
    now = datetime.now(timezone.utc)
    return {
        "runtime_sha": sha,
        "started_at": (now - timedelta(minutes=2)).isoformat(),
        "finished_at": (now - timedelta(minutes=1)).isoformat(),
        "underlying": "NIFTY",
        "evidence_class": "MODELLED",
        "tag": "TEST_ACCEPTANCE",
        "market_data_live": "PASS",
        "forced_disconnect": reconnect,
        "disconnect_observed": reconnect,
        "reconnect_attempted": reconnect,
        "reconnect_connected": reconnect,
        "post_reconnect_fresh_ticks": reconnect,
        "post_reconnect_full_mode": reconnect,
        "persistence": "PASS",
        "reload_reproduces_values": "PASS",
        "writer_healthy": "PASS",
        "conclusions": {
            "engineering_gate": "PASS",
            "vendor_assumptions": "PROVEN",
            "strategy_reality": {"status": "EXECUTABLE", "findings": []},
        },
        "overall": overall,
    }


def _write(root, name: str, payload: dict):
    directory = root / "data" / "evidence_acceptance"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_exact_sha_acceptance_artifact_derives_all_three_gates(tmp_path, monkeypatch):
    monkeypatch.setenv("STERLING_STATE_ROOT", str(tmp_path))
    path = _write(tmp_path, "acceptance-20261003-093200.json", _payload())

    gates = cert._acceptance_gates(SHA)

    assert gates["kite_live_acceptance"].status == "PASS"
    assert gates["reconnect"].status == "PASS"
    assert gates["persistence"].status == "PASS"
    assert gates["kite_live_acceptance"].evidence_ref == str(path)
    assert gates["kite_live_acceptance"].evidence_sha256


def test_acceptance_from_another_sha_satisfies_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("STERLING_STATE_ROOT", str(tmp_path))
    _write(tmp_path, "acceptance-20261003-093200.json", _payload(sha="b" * 40))

    gates = cert._acceptance_gates(SHA)

    assert {row.status for row in gates.values()} == {"UNKNOWN"}


def test_arbitrary_exact_sha_json_cannot_be_called_acceptance(tmp_path, monkeypatch):
    monkeypatch.setenv("STERLING_STATE_ROOT", str(tmp_path))
    payload = _payload()
    payload["tag"] = "NOT_THE_ACCEPTANCE_HARNESS"
    _write(tmp_path, "acceptance-20261003-093200.json", payload)

    gates = cert._acceptance_gates(SHA)

    assert {row.status for row in gates.values()} == {"FAIL"}


def test_later_failed_rerun_supersedes_older_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("STERLING_STATE_ROOT", str(tmp_path))
    now = datetime.now(timezone.utc)
    old = _payload()
    old["started_at"] = (now - timedelta(minutes=12)).isoformat()
    old["finished_at"] = (now - timedelta(minutes=10)).isoformat()
    new = _payload(reconnect="FAIL")
    new["started_at"] = (now - timedelta(minutes=4)).isoformat()
    new["finished_at"] = (now - timedelta(minutes=2)).isoformat()
    _write(tmp_path, "acceptance-20261003-093200.json", old)
    _write(tmp_path, "acceptance-20261003-100200.json", new)

    gates = cert._acceptance_gates(SHA)

    assert gates["reconnect"].status == "FAIL"
    # The same newest report remains authoritative for every acceptance gate.
    assert gates["kite_live_acceptance"].status == "PASS"


def test_newer_future_dated_rerun_blocks_older_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("STERLING_STATE_ROOT", str(tmp_path))
    now = datetime.now(timezone.utc)

    old = _payload()
    old["started_at"] = (now - timedelta(minutes=10)).isoformat()
    old["finished_at"] = (now - timedelta(minutes=8)).isoformat()

    future = _payload()
    future["started_at"] = (now + timedelta(minutes=4)).isoformat()
    future["finished_at"] = (now + timedelta(minutes=5)).isoformat()

    _write(tmp_path, "acceptance-20261003-093200.json", old)
    _write(tmp_path, "acceptance-20261003-100200.json", future)

    gates = cert._acceptance_gates(SHA)

    assert {row.status for row in gates.values()} == {"UNKNOWN"}
    assert "newer exact-SHA acceptance artifact" in gates["kite_live_acceptance"].detail
    assert "future" in gates["kite_live_acceptance"].detail


def test_acceptance_finish_before_start_is_not_valid_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("STERLING_STATE_ROOT", str(tmp_path))
    now = datetime.now(timezone.utc)
    payload = _payload()
    payload["started_at"] = now.isoformat()
    payload["finished_at"] = (now - timedelta(minutes=1)).isoformat()
    _write(tmp_path, "acceptance-20261003-093200.json", payload)

    gates = cert._acceptance_gates(SHA)

    assert {row.status for row in gates.values()} == {"UNKNOWN"}
    assert "clock-invalid" in gates["kite_live_acceptance"].detail


@pytest.mark.parametrize(
    "key", ["kite_live_acceptance", "reconnect", "persistence"]
)
def test_machine_derived_acceptance_gates_reject_manual_pass(tmp_path, key):
    store = cert.CertificationStore(tmp_path)
    with pytest.raises(ValueError, match="derived from its own register or machine evidence"):
        store.attest(
            SHA,
            key,
            "PASS",
            attested_by="operator",
            evidence_ref="https://example.test/not-proof",
        )


def test_acceptance_runtime_sha_uses_release_identity(monkeypatch):
    import app.core.release_manifest as release_manifest
    import study.snapback_live_evidence_acceptance as harness

    monkeypatch.setattr(release_manifest, "runtime_sha", lambda: SHA)

    assert harness._runtime_sha() == SHA
