"""Read the required CI contexts for one commit from GitHub, and record them.

Transcribing required check names and run ids by hand is how a release ends up
certified against the wrong commit. This asks GitHub for the check runs attached
to one exact SHA and records what it finds.

Two deliberate limits. It records only the contexts the specification requires,
so an unrelated check cannot pad the list. And it records what it was told —
a failure is stored as a failure — because a sync that quietly skipped bad news
would be worse than typing it in by hand.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.core.ci_certification import (
    REQUIRED_CONTEXTS,
    CIContextRecord,
    CIRecordStore,
    ci_report,
)

log = logging.getLogger(__name__)

__all__ = ["SyncResult", "sync_ci_contexts", "check_runs_for"]


@dataclass(frozen=True)
class SyncResult:
    runtime_sha: str
    recorded: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"runtime_sha": self.runtime_sha, "recorded": list(self.recorded),
                "missing": list(self.missing), "error": self.error}


def check_runs_for(sha: str, *, repo: str | None = None) -> list[Mapping[str, Any]]:
    """Every check run GitHub has attached to this commit.

    Uses the `gh` CLI so the operator's existing credentials are the only ones
    involved: this tool never handles a token itself.
    """
    binary = shutil.which("gh")
    if binary is None:
        raise FileNotFoundError("the gh CLI is not installed")

    endpoint = (f"repos/{repo}/commits/{sha}/check-runs" if repo
                else f"repos/{{owner}}/{{repo}}/commits/{sha}/check-runs")
    completed = subprocess.run(
        [binary, "api", endpoint, "--paginate", "--jq", ".check_runs[]"],
        capture_output=True, text=True, timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "gh failed").strip()[:300])

    runs: list[Mapping[str, Any]] = []
    for line in completed.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            runs.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError("GitHub returned an unreadable check-run record") from exc
        if not isinstance(runs[-1], dict):
            raise ValueError("GitHub returned an invalid check-run record")
    return runs


def sync_ci_contexts(
    sha: str,
    *,
    repo: str | None = None,
    store: CIRecordStore | None = None,
    runs: Sequence[Mapping[str, Any]] | None = None,
) -> SyncResult:
    """Record each required context's conclusion for ``sha``.

    Replace the snapshot on every sync. Missing, pending or unreadable results
    must invalidate prior successes rather than leaving stale release proof.
    """
    record_store = store or CIRecordStore()
    try:
        found = list(runs) if runs is not None else check_runs_for(sha, repo=repo)
    except Exception as exc:  # noqa: BLE001
        record_store.replace(sha, [])
        return SyncResult(sha, missing=REQUIRED_CONTEXTS, error=str(exc))

    latest: dict[str, Mapping[str, Any]] = {}

    def _attempt_order(run: Mapping[str, Any]) -> tuple[str, int]:
        """Order attempts by when they started, then by GitHub check-run id.

        A newer queued/running re-run must supersede an older completed PASS.
        Ordering by completion time does the opposite while the re-run is still
        in progress because its completed_at is empty.
        """
        attempted_at = str(
            run.get("started_at") or run.get("completed_at") or ""
        ).strip()
        try:
            run_id = int(run.get("id") or 0)
        except (TypeError, ValueError):
            run_id = 0
        return attempted_at, run_id

    for run in found:
        name = str(run.get("name") or "")
        if name not in REQUIRED_CONTEXTS or run.get("head_sha") != sha:
            continue
        # A pending rerun supersedes a completed success. Check IDs increase
        # with creation; completion time would instead prefer the old success.
        try:
            run_id = int(run.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if run_id <= 0:
            continue

        # Defensive identity check. The GitHub endpoint is SHA-scoped, but the
        # optional injected `runs` path is used by tests and future callers;
        # never let a check-run that explicitly names another head SHA certify
        # this commit.
        head_sha = str(run.get("head_sha") or "").strip()
        if head_sha and head_sha != sha:
            continue

        previous = latest.get(name)
        if previous is None or _attempt_order(run) >= _attempt_order(previous):
            latest[name] = run

    recorded: list[str] = []
    records: list[CIContextRecord] = []
    for name, run in latest.items():
        conclusion = str(run.get("conclusion") or "").strip().lower()
        completed_at = str(run.get("completed_at") or "").strip()
        started_at = str(run.get("started_at") or "").strip()
        status = str(run.get("status") or "").strip().lower()

        # A newer queued/running attempt invalidates a previously recorded PASS
        # immediately. Persist UNKNOWN for that context instead of leaving the
        # stale success in the SHA-bound store.
        incomplete = (
            not conclusion
            or (status and status != "completed")
            or (conclusion == "success" and not completed_at)
        )
        try:
            record_store.record(
                runtime_sha=sha,
                context=name,
                conclusion="" if incomplete else conclusion,
                run_id=str(run.get("id") or ""),
                observed_at=(completed_at or started_at or None),
                url=str(run.get("html_url") or ""),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("ci sync: could not record %s: %s", name, exc)
            continue
        if not incomplete:
            recorded.append(name)

    record_store.replace(sha, records)
    report = ci_report(sha, store=record_store)
    return SyncResult(
        runtime_sha=sha,
        recorded=tuple(sorted(recorded)),
        missing=tuple(r.context for r in report.unknowns),
    )
