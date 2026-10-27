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
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.core.ci_certification import (
    REQUIRED_CONTEXTS,
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
    # jq may pretty-print objects across multiple lines. Decode JSON values,
    # rather than silently dropping them one physical line at a time.
    decoder = json.JSONDecoder()
    stream = completed.stdout.lstrip()
    while stream:
        run, end = decoder.raw_decode(stream)
        runs.append(run)
        stream = stream[end:].lstrip()
    for index, run in enumerate(runs):
        match = re.fullmatch(r"https://github\.com/([^/]+/[^/]+)/actions/runs/(\d+)/job/(\d+)", str(run.get("details_url") or run.get("html_url") or ""))
        if not match:
            continue
        job_repo, workflow_id, job_id = match.groups()
        if repo and job_repo != repo:
            continue
        job_response = subprocess.run(
            [binary, "api", f"repos/{job_repo}/actions/jobs/{job_id}"],
            capture_output=True, text=True, timeout=30,
        )
        if job_response.returncode != 0:
            continue
        try:
            job = json.loads(job_response.stdout)
        except (json.JSONDecodeError, TypeError):
            continue
        if str(job.get("id")) != job_id or str(job.get("run_id")) != workflow_id or job.get("head_sha") != sha:
            continue
        runs[index] = {**run, "execution_job": job}
    return runs


def sync_ci_contexts(
    sha: str,
    *,
    repo: str | None = None,
    store: CIRecordStore | None = None,
    runs: Sequence[Mapping[str, Any]] | None = None,
) -> SyncResult:
    """Record each required context's conclusion for ``sha``.

    A context GitHub does not report is left with no record rather than being
    written as anything: "we did not find it" and "it failed" are different
    facts, and only one of them means someone should re-run a workflow.
    """
    record_store = store or CIRecordStore()
    try:
        found = list(runs) if runs is not None else check_runs_for(sha, repo=repo)
    except Exception as exc:  # noqa: BLE001
        return SyncResult(sha, error=str(exc))

    latest: dict[str, Mapping[str, Any]] = {}

    def _attempt_order(run: Mapping[str, Any]) -> tuple[int, str]:
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
        # Queued attempts have neither timestamp. A newer check-run id must
        # still invalidate an older PASS while it waits for a runner.
        return run_id, attempted_at

    for run in found:
        name = str(run.get("name") or "")
        if name not in REQUIRED_CONTEXTS:
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
    for name, run in latest.items():
        conclusion = str(run.get("conclusion") or "").strip().lower()
        completed_at = str(run.get("completed_at") or "").strip()
        started_at = str(run.get("started_at") or "").strip()
        status = str(run.get("status") or "").strip().lower()
        job = run.get("execution_job") or {}
        job_valid = isinstance(job, Mapping) and job.get("head_sha") == sha and job.get("name") == name
        job_id = job.get("id", 0) if job_valid else 0
        runner_id = job.get("runner_id", 0) if job_valid else 0
        executed_steps = sum(
            1 for step in job.get("steps", [])
            if isinstance(step, Mapping) and step.get("status") == "completed"
            and step.get("conclusion") not in {None, "skipped"}
            and step.get("started_at") and step.get("completed_at")
        ) if job_valid else 0
        execution_observed = (type(job_id) is int and job_id > 0 and type(runner_id) is int
                              and runner_id > 0 and executed_steps > 0)
        failed_steps = any(isinstance(step, Mapping) and step.get("conclusion")
                           not in {"success", "skipped"} for step in job.get("steps", [])) if job_valid else True

        # A newer queued/running attempt invalidates a previously recorded PASS
        # immediately. Persist UNKNOWN for that context instead of leaving the
        # stale success in the SHA-bound store.
        incomplete = (
            not conclusion
            or (status and status != "completed")
            or (conclusion == "success" and not completed_at)
            or (conclusion == "success" and (not execution_observed or failed_steps or job.get("conclusion") != "success" or job.get("status") != "completed"))
        )
        try:
            record_store.record(
                runtime_sha=sha,
                context=name,
                conclusion="" if incomplete else conclusion,
                run_id=str(run.get("id") or ""),
                observed_at=(completed_at or started_at or None),
                url=str(run.get("html_url") or ""),
                job_id=job_id if type(job_id) is int else 0,
                runner_id=runner_id if type(runner_id) is int else 0,
                executed_steps=executed_steps,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("ci sync: could not record %s: %s", name, exc)
            continue
        if not incomplete:
            recorded.append(name)

    report = ci_report(sha, store=record_store)
    return SyncResult(
        runtime_sha=sha,
        recorded=tuple(sorted(recorded)),
        missing=tuple(r.context for r in report.unknowns),
    )
