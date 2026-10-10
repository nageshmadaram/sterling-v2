"""A retry, missing check or failed fetch invalidates old release evidence."""
import json
from types import SimpleNamespace

import pytest

from app.core.ci_certification import CIRecordStore, REQUIRED_CONTEXTS, ci_report
from app.services import ci_context_sync as sync

SHA = "a" * 40


def run(name, run_id, **changes):
    row = {"name": name, "id": run_id, "head_sha": SHA, "status": "completed",
           "conclusion": "success", "completed_at": "2026-09-29T12:00:00Z"}
    return row | changes


@pytest.fixture
def store(tmp_path):
    store = CIRecordStore(tmp_path)
    sync.sync_ci_contexts(SHA, store=store,
                         runs=[run(name, i + 1) for i, name in enumerate(REQUIRED_CONTEXTS)])
    assert ci_report(SHA, store=store).all_passed
    return store


def test_empty_snapshot_cannot_preserve_previous_success(store):
    result = sync.sync_ci_contexts(SHA, store=store, runs=[])
    assert set(result.missing) == set(REQUIRED_CONTEXTS)
    assert not ci_report(SHA, store=store).all_passed


@pytest.mark.parametrize("reverse", [False, True])
def test_pending_retry_supersedes_old_success_in_either_api_order(store, reverse):
    name = REQUIRED_CONTEXTS[0]
    rows = [run(name, 1), run(name, 2, status="in_progress", conclusion=None, completed_at=None)]
    sync.sync_ci_contexts(SHA, store=store, runs=list(reversed(rows)) if reverse else rows)
    assert ci_report(SHA, store=store).by_context[name].status == "UNKNOWN"


@pytest.mark.parametrize("changes", [
    {"head_sha": "b" * 40}, {"head_sha": None}, {"status": "queued"},
    {"completed_at": None}, {"id": "invalid"}, {"id": 0},
])
def test_incomplete_or_foreign_record_does_not_certify(store, changes):
    sync.sync_ci_contexts(SHA, store=store, runs=[run(REQUIRED_CONTEXTS[0], 100, **changes)])
    assert ci_report(SHA, store=store).by_context[REQUIRED_CONTEXTS[0]].status == "UNKNOWN"


def test_api_failure_clears_previous_success(store, monkeypatch):
    def failed(*args, **kwargs):
        raise RuntimeError("billing or authentication blocked")
    monkeypatch.setattr(sync, "check_runs_for", failed)
    result = sync.sync_ci_contexts(SHA, store=store)
    assert result.error
    assert len(result.missing) == len(REQUIRED_CONTEXTS)
    assert not ci_report(SHA, store=store).all_passed


@pytest.mark.parametrize("output", ["{unreadable}", "null", "[]"])
def test_malformed_api_output_is_not_silently_ignored(monkeypatch, output):
    monkeypatch.setattr(sync.shutil, "which", lambda _: "/fake/gh")
    monkeypatch.setattr(sync.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=output, stderr=""))
    with pytest.raises(ValueError):
        sync.check_runs_for(SHA)


def test_replacement_is_exact_snapshot(store):
    name = REQUIRED_CONTEXTS[0]
    sync.sync_ci_contexts(SHA, store=store, runs=[run(name, 99, conclusion="failure")])
    payload = json.loads((store.directory / f"{SHA}.json").read_text())
    assert payload["runtime_sha"] == SHA
    assert len(payload["contexts"]) == 1
    assert ci_report(SHA, store=store).by_context[name].status == "FAIL"
