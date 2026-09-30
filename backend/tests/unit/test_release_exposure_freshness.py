from __future__ import annotations

from types import SimpleNamespace

from app.services import release_certification as certification


class _EmptyStore:
    def read(self, _sha):
        return {}


def _unknown(key: str):
    return certification.GateResult(key)


def test_certification_uses_supplied_fresh_exposure_snapshot(monkeypatch):
    snapshot = SimpleNamespace(
        total=1,
        unresolved_intents=0,
        open_positions=0,
        external_positions=1,
        held=(),
        external_instruments=("NFO:UNMANAGED",),
        detail="",
    )

    monkeypatch.setattr(certification, "_source_identity", lambda *_a, **_k: _unknown("source_identity"))
    monkeypatch.setattr(certification, "_release_manifest_gate", lambda: _unknown("release_manifest"))
    monkeypatch.setattr(certification, "_backup_restore_gate", lambda _sha: _unknown("backup_restore"))
    monkeypatch.setattr(certification, "_failure_drills_gate", lambda _sha: _unknown("failure_drills"))
    monkeypatch.setattr(certification, "_remote_ci_gate", lambda _sha: _unknown("remote_ci"))

    # If the injected snapshot is ignored, certification_report would import and
    # call exposure_snapshot() itself. The supplied snapshot must be the one and
    # only exposure truth used for this report.
    report = certification.certification_report(
        sha="a" * 40,
        tag="candidate",
        store=_EmptyStore(),
        exposure_snapshot_value=snapshot,
    )

    gate = report.by_key["open_exposure"]
    assert report.unresolved_exposure == 1
    assert gate.status == "FAIL"
    assert "EXTERNAL broker exposure/automation" in gate.detail
    assert "NFO:UNMANAGED" in gate.detail


def test_freeze_command_requires_fresh_broker_snapshot():
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    source = (root / "scripts" / "sterlingctl").read_text(encoding="utf-8")
    freeze = source.split('  freeze)\n', 1)[1].split('  verify)\n', 1)[0]

    assert "exposure_snapshot(include_broker=True)" in freeze
    assert "exposure_snapshot_value=fresh_exposure" in freeze
