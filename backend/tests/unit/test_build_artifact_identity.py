from __future__ import annotations

from pathlib import Path

from app.core import build_artifact_identity as bai


SHA = "a" * 40


def test_build_metadata_binds_sha_to_exact_source_bytes(tmp_path: Path):
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")

    path = bai.write_build_metadata(sha=SHA, tag="release-1", root=tmp_path)
    identity = bai.artifact_identity(tmp_path)

    assert path == tmp_path / bai.METADATA_FILENAME
    assert identity.present is True
    assert identity.valid is True
    assert identity.build_sha == SHA
    assert identity.release_tag == "release-1"
    assert identity.expected_source_sha256 == identity.actual_source_sha256
    assert identity.source_file_count == 1


def test_source_tamper_invalidates_artifact_identity(tmp_path: Path):
    source = tmp_path / "app.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    bai.write_build_metadata(sha=SHA, tag="release-1", root=tmp_path)

    source.write_text("VALUE = 2\n", encoding="utf-8")
    identity = bai.artifact_identity(tmp_path)

    assert identity.present is True
    assert identity.valid is False
    assert identity.reason == "source_digest_mismatch"
    assert identity.source_clean is False


def test_runtime_expected_sha_cannot_define_actual_artifact_sha(monkeypatch):
    from app.services import snapback_identity as runtime

    runtime.build_sha.cache_clear()
    monkeypatch.setattr(runtime, "_git_build_sha", lambda: "")
    monkeypatch.setattr(runtime, "_artifact_build_sha", lambda: SHA)
    monkeypatch.setenv("STERLING_EXPECTED_BUILD_SHA", "b" * 40)

    assert runtime.build_sha() == SHA
    ok, reasons = runtime.verify_build_identity(require_expectation=True)

    assert ok is False
    assert any("build_sha_mismatch" in reason for reason in reasons)
    runtime.build_sha.cache_clear()


def test_release_checks_fall_back_to_verified_artifact_when_git_is_absent(
    tmp_path: Path, monkeypatch
):
    from app.core import release_manifest as rm

    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    bai.write_build_metadata(sha=SHA, tag="release-1", root=tmp_path)

    monkeypatch.setattr(rm, "_git", lambda *args: "")
    monkeypatch.setattr(bai, "backend_root", lambda: tmp_path)

    assert rm.release_tag_points_at("release-1", SHA) is True
    assert rm.working_tree_clean() is True

    (tmp_path / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert rm.release_tag_points_at("release-1", SHA) is False
    assert rm.working_tree_clean() is False


def test_missing_artifact_metadata_is_unknown_not_clean(tmp_path: Path, monkeypatch):
    from app.core import release_manifest as rm

    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.setattr(rm, "_git", lambda *args: "")
    monkeypatch.setattr(bai, "backend_root", lambda: tmp_path)

    assert rm.release_tag_points_at("release-1", SHA) is None
    assert rm.working_tree_clean() is None
