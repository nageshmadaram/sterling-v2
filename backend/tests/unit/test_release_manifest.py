"""The release manifest must name the build, and must refuse to invent identity.

Two failure modes are worth more than the rest: a lane with no frozen rules
acquiring a plausible-looking identity hash, and a manifest that still reads as
matching after a rule hash moved.
"""
from __future__ import annotations

import json

import pytest

from app.core.lane_registry import LANES
from app.core.release_manifest import (
    build_release_manifest,
    lane_manifest_file_drift,
    read_manifest,
    release_tag,
    release_tag_points_at,
    render_manifest,
    verify_manifest,
    write_manifest,
)


@pytest.fixture()
def manifest():
    return build_release_manifest(
        sha="a" * 40, tag="test-release-1.0", generated_at="2026-09-18T00:00:00+00:00"
    )


def test_every_declared_lane_appears(manifest):
    keys = {lane["lane_key"] for lane in manifest["lanes"]}
    assert keys == set(LANES)
    assert manifest["lane_count"] == 10


def test_a_lane_without_frozen_rules_gets_no_identity(manifest):
    by_key = {lane["lane_key"]: lane for lane in manifest["lanes"]}

    for lane_key, lane in LANES.items():
        row = by_key[lane_key]
        if lane.rules_defined:
            continue
        assert row["identity"] is None, f"{lane_key} fabricated an identity"
        assert row["identity_unavailable"] == "rules_not_frozen"


def test_lanes_with_rules_carry_a_distinct_identity_hash(manifest):
    hashes = [
        lane["identity"]["identity_hash"]
        for lane in manifest["lanes"]
        if lane["identity"]
    ]
    assert hashes, "no lane produced an identity"
    assert len(set(hashes)) == len(hashes), "two lanes share one identity hash"


def test_the_build_is_recorded_on_every_identity(manifest):
    for lane in manifest["lanes"]:
        if lane["identity"]:
            assert lane["identity"]["runtime_sha"] == "a" * 40
            assert lane["identity"]["release_tag"] == "test-release-1.0"


def test_an_unknown_build_sha_refuses_identity_rather_than_faking_one():
    # A row that cannot name its build is not authoritative evidence, so the
    # manifest must record the refusal instead of stamping "UNKNOWN" on it.
    manifest = build_release_manifest(sha="UNKNOWN", tag="t")
    frozen = [
        lane
        for lane in manifest["lanes"]
        if LANES[lane["lane_key"]].rules_defined
    ]
    assert frozen
    for lane in frozen:
        assert lane["identity"] is None
        assert "runtime_sha is UNKNOWN" in lane["identity_unavailable"]


def test_verify_matches_itself(manifest):
    verdict = verify_manifest(manifest, manifest)
    assert verdict.matches
    assert verdict.drift == ()


def test_a_moved_rule_hash_is_identity_drift(manifest):
    stored = json.loads(json.dumps(manifest))
    target = next(lane for lane in stored["lanes"] if lane["identity"])
    target["identity"]["rule_hash"] = "0" * 16

    verdict = verify_manifest(stored, manifest)

    assert not verdict.matches
    assert any(d.field == "rule_hash" for d in verdict.drift)
    assert verdict.identity_drift, "a moved rule hash must count as identity drift"


def test_a_moved_build_is_drift_but_not_identity_drift(manifest):
    stored = json.loads(json.dumps(manifest))
    stored["build"]["runtime_sha"] = "b" * 40

    verdict = verify_manifest(stored, manifest)

    assert not verdict.matches
    assert any(d.field == "runtime_sha" for d in verdict.drift)
    assert not verdict.identity_drift


@pytest.mark.parametrize(
    ("field", "mutated"),
    [
        ("account_binding_id", "other-binding"),
        ("live_execution_enabled", True),
        ("manifest_schema_version", "999"),
        ("lane_count", 999),
    ],
)
def test_top_level_release_contract_drift_is_detected(manifest, field, mutated):
    stored = json.loads(json.dumps(manifest))
    stored[field] = mutated

    verdict = verify_manifest(stored, manifest)

    assert not verdict.matches
    assert any(d.scope == "manifest" and d.field == field for d in verdict.drift)


def test_dirty_worktree_state_is_release_drift(manifest):
    stored = json.loads(json.dumps(manifest))
    stored["build"]["working_tree_clean"] = not bool(
        manifest["build"].get("working_tree_clean")
    )

    verdict = verify_manifest(stored, manifest)

    assert not verdict.matches
    assert any(
        d.scope == "build" and d.field == "working_tree_clean"
        for d in verdict.drift
    )


def test_frozen_certification_facts_are_verified(manifest):
    stored = json.loads(json.dumps(manifest))
    stored["certification"] = {"ci": {"all_required_contexts": "PASS"}}

    verdict = verify_manifest(stored, manifest)

    assert not verdict.matches
    assert any(
        d.scope == "manifest" and d.field == "certification"
        for d in verdict.drift
    )


def test_a_removed_lane_is_reported(manifest):
    stored = json.loads(json.dumps(manifest))
    dropped = stored["lanes"].pop()["lane_key"]

    verdict = verify_manifest(stored, manifest)

    assert not verdict.matches
    assert verdict.unexpected_lanes == (dropped,)


def test_write_and_read_round_trip(tmp_path, manifest):
    path = write_manifest(tmp_path, manifest)

    assert path == tmp_path / "data/manifests/release.json"
    assert read_manifest(tmp_path) == manifest

    lane_files = sorted((tmp_path / "data/manifests/strategies").glob("*.json"))
    assert len(lane_files) == 10
    one = json.loads(lane_files[0].read_text())
    assert one["build"] == manifest["build"]


def test_lane_manifest_files_match_frozen_release(tmp_path, manifest):
    write_manifest(tmp_path, manifest)

    assert lane_manifest_file_drift(manifest, tmp_path) == ()


def test_lane_manifest_file_mutation_is_detected(tmp_path, manifest):
    write_manifest(tmp_path, manifest)
    lane_file = next((tmp_path / "data/manifests/strategies").glob("*.json"))
    payload = json.loads(lane_file.read_text(encoding="utf-8"))
    payload["state"] = "LIVE_SCALED"
    lane_file.write_text(json.dumps(payload), encoding="utf-8")

    problems = lane_manifest_file_drift(manifest, tmp_path)

    assert problems
    assert any("content does not match frozen release" in item for item in problems)


def test_unexpected_lane_manifest_file_is_detected(tmp_path, manifest):
    write_manifest(tmp_path, manifest)
    extra = tmp_path / "data/manifests/strategies/obsolete__lane.json"
    extra.write_text("{}", encoding="utf-8")

    problems = lane_manifest_file_drift(manifest, tmp_path)

    assert any("unexpected lane manifest" in item for item in problems)


def test_read_manifest_is_none_when_never_frozen(tmp_path):
    assert read_manifest(tmp_path) is None



def test_refreezing_same_release_is_idempotent_and_byte_stable(tmp_path, manifest):
    path = write_manifest(tmp_path, manifest)
    original = path.read_bytes()

    changed_timestamp = json.loads(json.dumps(manifest))
    changed_timestamp["generated_at"] = "2099-01-01T00:00:00+00:00"
    same = write_manifest(tmp_path, changed_timestamp)

    assert same == path
    assert path.read_bytes() == original
    assert read_manifest(tmp_path)["generated_at"] == manifest["generated_at"]


def test_same_sha_tag_with_changed_contract_is_not_treated_as_idempotent(
    tmp_path, manifest
):
    path = write_manifest(tmp_path, manifest)
    original = path.read_bytes()

    changed = json.loads(json.dumps(manifest))
    changed["account_binding_id"] = "different-binding"

    with pytest.raises(RuntimeError, match="same release SHA/tag"):
        write_manifest(tmp_path, changed)

    assert path.read_bytes() == original


def test_a_different_release_cannot_overwrite_the_frozen_manifest(tmp_path, manifest):
    path = write_manifest(tmp_path, manifest)
    original = path.read_bytes()

    other = json.loads(json.dumps(manifest))
    other["build"]["runtime_sha"] = "b" * 40
    other["build"]["release_tag"] = "test-release-2.0"

    with pytest.raises(FileExistsError, match="different release is already frozen"):
        write_manifest(tmp_path, other)

    assert path.read_bytes() == original


def test_render_lists_every_lane(manifest):
    out = render_manifest(manifest)
    for lane_key in LANES:
        assert lane_key in out
    assert "test-release-1.0" in out



def test_release_tag_never_falls_back_to_a_short_sha(monkeypatch):
    from app.core import release_manifest as rm

    monkeypatch.delenv("STERLING_RELEASE_TAG", raising=False)
    monkeypatch.setattr(rm, "runtime_sha", lambda: "a" * 40)
    monkeypatch.setattr(rm, "_git", lambda *args: "")
    assert release_tag() == rm.UNKNOWN


def test_declared_release_tag_must_resolve_to_exact_sha(monkeypatch):
    from app.core import release_manifest as rm

    expected = "a" * 40

    def fake_git(*args):
        if args == ("rev-parse", "refs/tags/v1^{}"):
            return expected
        return ""

    monkeypatch.setattr(rm, "_git", fake_git)
    assert release_tag_points_at("v1", expected) is True
    assert release_tag_points_at("v1", "b" * 40) is False


def test_source_identity_refuses_a_tag_name_not_bound_to_sha(monkeypatch):
    from app.services import release_certification as cert

    monkeypatch.setattr(cert, "working_tree_clean", lambda: True)
    monkeypatch.setattr(cert, "release_tag_points_at", lambda tag, sha: False)
    result = cert._source_identity("a" * 40, "pretend-release")
    assert result.status == cert.FAIL
    assert "does not point" in result.detail


def test_source_identity_is_a_pre_freeze_gate_but_manifest_is_not():
    from app.services.release_certification import PRE_FREEZE_GATES

    assert "source_identity" in PRE_FREEZE_GATES
    assert "release_manifest" not in PRE_FREEZE_GATES
