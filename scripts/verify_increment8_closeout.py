#!/usr/bin/env python3
"""Increment 8 branch-only source integrity / NO-GO closeout.

Reads local Git checkout and source files. Does not connect to operational systems.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from scripts.verify_increment7_branch_freeze import (
    LOCK as HISTORICAL_LOCK,
    load_package as load_prior,
    verify as verify_prior,
)

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "docs/evidence/2026-10-29-next-upgrade/increment-8-review-snapshot.json"
DOCUMENT = ROOT / "docs/implementation/2026-10-29-final-frozen-evidence-closeout-increment-8.md"
BASE = "8eb416e525b1e5b4b9378d3607fb9e99ce441bcc"
MAIN = "27e39e954ee67dbd5ec1d68ab9d15ff15de7dd7f"
HISTORICAL_LOCK_BLOB = "509946c6b7d2e27ffbf0baceaecd73323c1392d0"
BRANCH = "upgrade/evidence-traceability-20261029"
IDS = ("H01", "T02", "S03", "I04", "D05", "R06", "B07", "M08", "A09", "L10", "F11", "G12")
PATHS = (
    ".github/workflows/next-upgrade-evidence.yml",
    "docs/evidence/2026-10-29-next-upgrade/increment-8-review-snapshot.json",
    "docs/implementation/2026-10-29-final-frozen-evidence-closeout-increment-8.md",
    "scripts/verify_increment8_closeout.py",
    "scripts/tests/test_next_upgrade_increment8_closeout.py",
)
DECISIONS = {
    "OFFLINE VALIDATION": "PASS",
    "PRODUCTION DEPLOYMENT": "NO-GO",
    "LIVE TRADING": "NOT AUTHORIZED",
    "BROKER ORDER ROUTING": "NOT AUTHORIZED",
    "production_certification": "NOT ACHIEVED",
    "release_freeze": "ACTIVE",
    "independent_release_signoff": "NOT PROVIDED",
    "merge_to_main": "NOT AUTHORIZED",
}
FINAL = "Production certification remains not achieved. Release freeze remains active."


def guard(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def git(*parts: str) -> str:
    return subprocess.check_output(["git", *parts], cwd=ROOT, stderr=subprocess.DEVNULL).decode().strip()


def read_git_state() -> dict:
    """No fetch, push, merge, shell, deployment, or broker calls."""
    branch = os.environ.get("GITHUB_REF_NAME") or git("branch", "--show-current")
    guard(branch == BRANCH, "unexpected source branch")
    sha = git("rev-parse", "HEAD")
    guard(git("merge-base", BASE, sha) == BASE, "Increment 7 not ancestor of checkout")
    ci_sha = os.environ.get("GITHUB_SHA")
    if ci_sha:
        guard(sha == ci_sha, "checkout is not the CI event SHA")
    diff = subprocess.check_output(
        ["git", "diff", "--no-renames", "--name-only", "-z", BASE, sha],
        cwd=ROOT, stderr=subprocess.DEVNULL,
    )
    changes = [os.fsdecode(part) for part in diff.split(b"\0") if part]
    clean = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=normal", "-z"],
        cwd=ROOT, stderr=subprocess.DEVNULL,
    ) == b""
    modes = {}
    for path in PATHS:
        indexed = git("ls-files", "--stage", "--", path)
        modes[path] = indexed.split(" ", 1)[0] if indexed else None
    return {
        "branch": branch, "head":sha, "main":git("rev-parse", "refs/remotes/origin/main"),
        "changed_paths":changes, "working_tree_clean":clean,
        "historical_lock_blob":git("hash-object", "--", str(HISTORICAL_LOCK)),
        "file_modes":modes,
    }


def load_package() -> dict:
    return {"snapshot": json.loads(PATH.read_text(encoding="utf-8")), "prior": load_prior()}


def verify(data: dict, *, state: dict | None = None, doc: str | None = None) -> dict:
    snapshot, prior = data["snapshot"], data["prior"]
    check_prior = verify_prior(prior)
    guard(check_prior["validation"] == "PASS"
          and check_prior["locked_sources"] == 26
          and check_prior["accepted_controls"] == 0, "Increment 7 historical source lock not valid")
    guard(snapshot["schema"] == "sterling.v2.increment8.final_branch_review.v1"
          and snapshot["record_kind"] == "UNMERGED_SOURCE_ONLY_EVIDENCE_CLOSEOUT_NOT_RELEASE_DECISION",
          "snapshot schema/record kind mismatch")
    guard(snapshot["repository"] == "nageshmadaram/sterling-v2"
          and snapshot["engineering_branch"] == BRANCH
          and snapshot["baseline_increment7_commit"] == BASE
          and snapshot["main_snapshot_sha"] == MAIN, "unexpected repository or frozen ref")
    guard(snapshot["baseline_increment7_lock_blob_sha"] == HISTORICAL_LOCK_BLOB,
          "historical source lock identity changed")
    guard(snapshot["historical_increment7_validation"] == "PINNED_TO_PREVIOUSLY_TESTED_COMMIT",
          "historical validation not pinned")
    guard(snapshot["increment8_changed_paths_exactly"] == list(PATHS),
          "Increment 8 allowlist changed or broadened")
    guard(snapshot["decisions"] == DECISIONS and snapshot["final_sentence"] == FINAL,
          "decision boundary or closeout changed")
    guard(snapshot["permissions"] == {
        "production_deployment":False, "main_merge":False, "broker_connection":False,
        "broker_order_routing":False,"live_trading":False,"independent_human_approval":False,
    }, "unauthorized production or broker authority claimed")
    guard(snapshot["counts"] == {
        "accepted":0,"blocked":12,"confirmed_human_owners":0,
        "independent_review_signatures":0,"real_environment_receipts":0,
    }, "governance counts promoted")
    controls = snapshot["controls"]
    guard([c["id"] for c in controls] == list(IDS), "missing/duplicated/unsorted control IDs")
    for c in controls:
        guard(c == {
            "id":c["id"],"status":"BLOCKED","accepted":False,
            "real_production_evidence_receipts":0,"human_accountable_owner_confirmed":False,
            "independent_reviewer_confirmed":False,"approval_signature":"NOT PROVIDED",
        }, "control acceptance or appointment fabricated")
    gates = snapshot["review_gates"]
    guard(gates == {
        "source_branch_integrity":"EVIDENCE_ONLY_UNTIL_RECHECKED",
        "h01_tooling_validation":"PASS", "h01_production_acceptance":"BLOCKED",
        "three_independent_signoffs":"NOT PROVIDED",
        "real_device_acceptance":"NOT ACHIEVED",
        "mfa_stepup_acceptance":"BLOCKED","release_freeze":"ACTIVE",
    }, "governance, device or MFA gate changed")
    prov = snapshot["provenance"]
    guard(prov["increment7_source_lock"] ==
          "docs/evidence/2026-10-29-next-upgrade/increment-7-source-integrity-lock.json"
          and prov["increment7_lock_sources"] == 26
          and prov["prior_evidence_packet"] ==
          "docs/evidence/2026-10-29-next-upgrade/increment-5-final-review-packet.json"
          and prov["human_appointment_source"] == "NONE_PROVIDED"
          and prov["independent_production_receipts"] == 0,
          "provenance source or human intake unsupported")
    ci = snapshot["previously_tested_ci"]
    guard(ci == {
        "run_id":40909176920,"conclusion":"success","head_sha":BASE,
        "source_evidence_tests":101,"increment7_tests":23,"backend_disposable_tests":66,
        "frontend_mock_tests":21,"chromium_mocked_tests":6,"physical_device_sessions":0,
    }, "prior synthetic CI validation provenance changed")
    guard(len(snapshot["external_review_sequence"]) == 5,
          "remaining independent review sequence incomplete")
    if state is not None:
        guard(state["branch"] == BRANCH and re.fullmatch(r"[0-9a-f]{40}",state["head"]),
              "wrong or unverified checkout")
        guard(state["main"] == MAIN, "main changed from observed frozen snapshot")
        paths = state["changed_paths"]
        guard(len(paths) == len(set(paths)) and set(paths) == set(PATHS),
              "changed files outside final evidence-only allowlist")
        guard(state["working_tree_clean"] is True,
              "working tree contains uncommitted changes or untracked files")
        guard(state["historical_lock_blob"] == HISTORICAL_LOCK_BLOB,
              "Increment 7 lock file content no longer matches frozen snapshot")
        guard(state["file_modes"] == dict.fromkeys(PATHS,"100644"),
              "unexpected file mode or missing changed file")
    if doc is None:
        doc = DOCUMENT.read_text(encoding="utf-8")
    for phrase in (
        "OFFLINE VALIDATION: PASS","PRODUCTION DEPLOYMENT: NO-GO",
        "LIVE TRADING: NOT AUTHORIZED","BROKER ORDER ROUTING: NOT AUTHORIZED",
        "0/12", "12/12", "human appointments", "source-only",
        "production evidence", FINAL,
    ):
        guard(phrase.lower() in doc.lower(), "closeout lacks mandatory caveat: " + phrase)
    for bad in (r"PRODUCTION DEPLOYMENT:\s*GO\b",r"LIVE TRADING:\s*AUTHORIZED\b",
                r"BROKER ORDER ROUTING:\s*AUTHORIZED\b",r"PRODUCTION CERTIFICATION:\s*ACHIEVED\b"):
        guard(re.search(bad,doc,re.I) is None, "closeout improperly implies release authority")
    return {
        "validation":"PASS", "kind":"READ_ONLY_ENGINEERING_CLOSEOUT_NOT_CERTIFICATION",
        "locked_historical_sources":26,"checked_controls":12,
        "accepted_controls":0,"blocked_controls":12,"human_owners_confirmed":0,
        "real_production_receipts":0,"independent_signoffs":0,
        "branch_hygiene":"PASS" if state is not None else "NOT_CHECKED",
        "main_snapshot":"UNCHANGED" if state is not None else "NOT_CHECKED",
        "offline_validation":"PASS",
        "production_deployment":"NO-GO",
        "live_trading":"NOT AUTHORIZED", "broker_order_routing":"NOT AUTHORIZED",
        "production_certification":"NOT ACHIEVED","release_freeze":"ACTIVE",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    try:
        result = verify(load_package(),state=read_git_state())
    except (ValueError, KeyError, TypeError, OSError, subprocess.CalledProcessError) as exc:
        print("FINAL BRANCH CLOSEOUT BLOCKED: "+str(exc))
        return 1
    if args.output:
        args.output.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print("Increment 8 source-only closeout: PASS, 0/12 accepted; NO-GO.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
