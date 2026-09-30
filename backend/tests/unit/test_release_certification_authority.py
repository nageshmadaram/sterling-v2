from __future__ import annotations

import pytest

from app.services import release_certification as certification


@pytest.mark.parametrize(
    "gate",
    ["source_identity", "release_manifest", "backup_restore"],
)
def test_automated_gate_cannot_be_human_attested(tmp_path, gate):
    store = certification.CertificationStore(tmp_path)

    with pytest.raises(ValueError, match="automated"):
        store.attest(
            "a" * 40,
            gate,
            "PASS",
            attested_by="operator",
            evidence_ref="artifact://proof",
        )


def test_human_pass_requires_evidence_reference(tmp_path):
    store = certification.CertificationStore(tmp_path)

    with pytest.raises(ValueError, match="evidence reference"):
        store.attest(
            "a" * 40,
            "test_suites",
            "PASS",
            attested_by="operator",
        )


class _ForgedStore:
    def read(self, _sha):
        return {
            "backup_restore": certification.GateResult(
                "backup_restore",
                certification.PASS,
                "human said backup was fine",
                attested_by="operator",
                evidence_ref="artifact://forged",
            )
        }


def test_automated_unknown_cannot_be_replaced_by_stored_pass(monkeypatch):
    monkeypatch.setattr(
        certification,
        "_source_identity",
        lambda *_a, **_k: certification.GateResult("source_identity"),
    )
    monkeypatch.setattr(
        certification,
        "_release_manifest_gate",
        lambda: certification.GateResult("release_manifest"),
    )
    monkeypatch.setattr(
        certification,
        "_backup_restore_gate",
        lambda _sha: certification.GateResult(
            "backup_restore",
            certification.UNKNOWN,
            "backup could not be verified",
        ),
    )
    monkeypatch.setattr(
        certification,
        "_failure_drills_gate",
        lambda _sha: certification.GateResult("failure_drills"),
    )
    monkeypatch.setattr(
        certification,
        "_remote_ci_gate",
        lambda _sha: certification.GateResult("remote_ci"),
    )

    snapshot = type(
        "Snapshot",
        (),
        {
            "total": 0,
            "unresolved_intents": 0,
            "open_positions": 0,
            "external_positions": 0,
            "held": (),
            "external_instruments": (),
            "detail": "",
        },
    )()

    report = certification.certification_report(
        sha="a" * 40,
        tag="candidate",
        store=_ForgedStore(),
        exposure_snapshot_value=snapshot,
    )

    gate = report.by_key["backup_restore"]
    assert gate.status == certification.UNKNOWN
    assert gate.detail == "backup could not be verified"
