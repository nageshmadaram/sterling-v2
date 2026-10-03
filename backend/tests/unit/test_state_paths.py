from __future__ import annotations

from app.core.state_paths import source_root, state_root, state_path


def test_explicit_state_root_is_independent_of_source_root(tmp_path, monkeypatch):
    source = tmp_path / "source"
    state = tmp_path / "persistent"
    monkeypatch.setenv("STERLING_ROOT", str(source))
    monkeypatch.setenv("STERLING_STATE_ROOT", str(state))

    assert source_root() == source
    assert state_root() == state
    assert state_path("data/example.json") == state / "data/example.json"


def test_state_root_falls_back_to_source_root_for_existing_checkouts(tmp_path, monkeypatch):
    monkeypatch.setenv("STERLING_ROOT", str(tmp_path))
    monkeypatch.delenv("STERLING_STATE_ROOT", raising=False)

    assert state_root() == tmp_path


def test_release_and_operator_stores_share_explicit_state_root(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setenv("STERLING_STATE_ROOT", str(state))
    monkeypatch.setenv("STERLING_ROOT", str(tmp_path / "source"))
    for name in ("STERLING_SAFE_MODE_FILE", "STERLING_SHADOW_DIR"):
        monkeypatch.delenv(name, raising=False)

    from app.core.ci_certification import CIRecordStore
    from app.core.deployment_identity import DeploymentIdentityStore
    from app.core.failure_drills import DrillRegister
    from app.services.account_binding_service import AccountBindingStore
    from app.services.continuity_doctor import _checklist_path
    from app.services.release_certification import CertificationStore
    from app.services.safety_supervisor import safe_mode_path
    from app.services.shadow_execution import ShadowStore

    paths = (
        AccountBindingStore().directory,
        CIRecordStore().directory,
        DrillRegister().directory,
        CertificationStore().directory,
        DeploymentIdentityStore().path,
        _checklist_path(),
        safe_mode_path(),
        ShadowStore().directory,
    )
    assert all(path == state or state in path.parents for path in paths)


def test_release_manifest_defaults_to_state_root(tmp_path, monkeypatch):
    from app.core import release_manifest as rm

    state = tmp_path / "state"
    monkeypatch.setenv("STERLING_STATE_ROOT", str(state))
    payload = rm.build_release_manifest(
        sha="a" * 40,
        tag="test-state-root",
        generated_at="2026-10-02T00:00:00+00:00",
    )

    path = rm.write_manifest(manifest=payload)

    assert path == state / rm.RELEASE_MANIFEST_PATH
    assert rm.read_manifest() == payload
    assert rm.lane_manifest_file_drift(payload) == ()


def test_backup_coverage_tracks_execution_intent_journal(tmp_path, monkeypatch):
    from app.core.backup_coverage import resolve_artifacts

    state = tmp_path / "state"
    intent = state / "data" / "intents.db"
    monkeypatch.setenv("STERLING_STATE_ROOT", str(state))
    monkeypatch.setenv("STERLING_INTENT_DB", str(intent))

    by_name = {artifact.name: path for artifact, path in resolve_artifacts(state)}
    assert by_name["execution_intent_journal"] == intent
