#!/usr/bin/env python3
"""Read-only Increment 9 final evidence and branch freeze.

Git file integrity and offline test success do not certify production.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from scripts.verify_increment8_closeout import load_package as load_previous, verify as verify_previous

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "docs/evidence/2026-10-29-next-upgrade/increment-9-final-review-freeze.json"
DOCUMENT = ROOT / "docs/implementation/2026-10-29-final-review-closure-increment-9.md"
BASE = "60cef51b93f12bd3cb8ae849aba83f1003bb1a5f"
TREE = "9bc3618eea5f913a81e6f74cc0a6a6fd3d47629a"
MAIN = "27e39e954ee67dbd5ec1d68ab9d15ff15de7dd7f"
BRANCH = "upgrade/evidence-traceability-20261029"
IDS = ("H01", "T02", "S03", "I04", "D05", "R06", "B07", "M08", "A09", "L10", "F11", "G12")
FILES = (
    ("docs/evidence/2026-10-29-next-upgrade/increment-8-review-snapshot.json", "eb00af65c5ba8ffdfdef30589e22100a94eef363"),
    ("docs/implementation/2026-10-29-final-frozen-evidence-closeout-increment-8.md", "c5abb6a73c912f2b12d03c484ddde75eb3771f4e"),
    ("scripts/verify_increment8_closeout.py", "e52cbef1d636ef1558bfbb19326253070c42545d"),
    ("scripts/tests/test_next_upgrade_increment8_closeout.py", "d6411566d6ba7bfd22ce768e28988d65e597cf29"),
)
ALLOW = (
    ".github/workflows/next-upgrade-evidence.yml",
    "docs/evidence/2026-10-29-next-upgrade/increment-9-final-review-freeze.json",
    "docs/implementation/2026-10-29-final-review-closure-increment-9.md",
    "scripts/verify_increment9_review_freeze.py",
    "scripts/tests/test_next_upgrade_increment9_review_freeze.py",
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
CLOSE = "Production certification remains not achieved. Release freeze remains active."


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def load_package() -> dict:
    return {"snapshot": json.loads(PATH.read_text(encoding="utf-8")),
            "previous": load_previous()}


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, stderr=subprocess.DEVNULL).decode().strip()


def checkout_state() -> dict:
    """Local read-only Git checks, with no network, fetch, merge or push."""
    branch = os.getenv("GITHUB_REF_NAME") or git("branch", "--show-current")
    head = git("rev-parse", "HEAD")
    require(branch == BRANCH, "not on declared engineering branch")
    require(git("merge-base", BASE, head) == BASE, "historical baseline not an ancestor")
    if os.getenv("GITHUB_SHA"):
        require(os.environ["GITHUB_SHA"] == head, "CI checked out wrong event SHA")
    raw = subprocess.check_output(
        ["git", "diff", "--no-renames", "--name-only", "-z", BASE, head],
        cwd=ROOT, stderr=subprocess.DEVNULL)
    paths = [os.fsdecode(x) for x in raw.split(b"\0") if x]
    clean = subprocess.check_output(["git", "status", "--porcelain", "-z"],
                                    cwd=ROOT, stderr=subprocess.DEVNULL) == b""
    modes = {}
    for p in ALLOW:
        record = git("ls-files", "--stage", "--", p)
        modes[p] = record.split(" ", 1)[0] if record else None
    return {
        "branch": branch, "head": head, "baseline_tree": git("rev-parse", BASE + "^{tree}"),
        "main": git("rev-parse", "refs/remotes/origin/main"), "changed_paths": paths,
        "working_tree_clean": clean, "file_modes": modes,
    }


def verify(data: dict, *, state: dict | None = None, document: str | None = None,
           source_reader=None) -> dict:
    m, previous = data["snapshot"], data["previous"]
    previous_result = verify_previous(previous)
    require(previous_result["validation"] == "PASS" and previous_result["accepted_controls"] == 0
            and previous_result["locked_historical_sources"] == 26,
            "Increment 8 source-only evidence no longer consistent")
    require(m["schema"] == "sterling.v2.increment9.final_readonly_closeout.v1"
            and m["record_kind"] == "ENGINEERING_BRANCH_REVIEW_FREEZE_NOT_AUTHORITY",
            "invalid manifest schema or false record kind")
    require(m["repo"] == "nageshmadaram/sterling-v2" and m["branch"] == BRANCH
            and m["increment8_commit"] == BASE and m["increment8_tree_sha"] == TREE
            and m["main_observed_sha"] == MAIN,
            "unexpected branch, baseline tree or main")
    require(m["previous_ci_historical_replay"] == "PIN_INCREMENT8_CHECKOUT_TO_INCREMENT8_SHA",
            "historical validation not isolated")
    require(m["increment8_ci"] == {
        "run_id": 40914383173, "conclusion": "success", "head_sha": BASE,
        "completed_jobs": 9,
    }, "historical CI provenance altered")
    require(m["change_allowlist"] == list(ALLOW), "branch diff allowlist broadened")
    require(m["evidence_boundary"] ==
            "Repository Git blob identifiers and CI source checks prove only file and branch structure, never independent real environment or production acceptance.",
            "source hashes incorrectly equated to production evidence")
    require(m["scope"] == "SOURCE_ONLY_EVIDENCE_AND_GOVERNANCE_PREP_NO_RUNTIME_OPERATION",
            "operational source scope claimed")
    require(m["decision"] == DECISIONS and m["final_sentence"] == CLOSE,
            "freeze or NO-GO boundary changed")
    require(m["actions"] == {"merge_main":False,"deploy":False,"connect_broker":False,
            "route_orders":False,"enable_live_trading":False,"issue_signoff":False},
            "claimed execution or sign-off authority")
    require(m["governance"] == {
        "accepted":0,"blocked":12,"confirmed_owners":0,"independent_signoffs":0,
        "real_operational_receipts":0,"h01_tooling":"PASS",
        "h01_production_evidence":"BLOCKED","release_freeze":"ACTIVE",
        "signed_release_decision":"NOT PROVIDED",
    }, "false acceptance or missing blocker")
    require([c["id"] for c in m["controls"]] == list(IDS), "control IDs missing, duplicate or reordered")
    for c in m["controls"]:
        require(c == {"id":c["id"],"status":"BLOCKED","accepted":False,
                "independent_receipts":0,"owner_appointed":False,"reviewer_appointed":False},
                "control accepted or person appointed without evidence")
    require(len(m["next_human_gates"]) == 4 and all(isinstance(v,str) and len(v)>30
            for v in m["next_human_gates"]), "missing independent review gates")
    require(len(m["historical_files"]) == len(FILES), "historical file set incomplete")
    hashes = {}
    for actual, (expected_path, expected_sha) in zip(m["historical_files"], FILES):
        require(actual == {"path":expected_path,"git_blob_sha1":expected_sha,
                "scope":"SOURCE_ONLY_HISTORICAL_NOT_PRODUCTION_EVIDENCE"},
                "historical source identity altered")
        path = ROOT / expected_path
        require(not path.is_symlink() and path.is_file()
                and path.resolve().is_relative_to(ROOT.resolve()) and path.stat().st_size < 1_000_000,
                "unsafe or unavailable source file")
        data_bytes = source_reader(expected_path) if source_reader else path.read_bytes()
        require(isinstance(data_bytes, bytes), "source reader returned non-byte content")
        sha = hashlib.sha1(b"blob " + str(len(data_bytes)).encode() + b"\0" + data_bytes).hexdigest()
        require(sha == expected_sha, "historical evidence content changed: " + expected_path)
        hashes[expected_path] = hashlib.sha256(data_bytes).hexdigest()
    if state is not None:
        require(state["branch"] == BRANCH
                and isinstance(state["head"],str)
                and re.fullmatch(r"[a-f0-9]{40}",state["head"]) is not None,
                "wrong branch or invalid HEAD")
        require(state["baseline_tree"] == TREE and state["main"] == MAIN,
                "unexpected baseline tree or main change")
        require(state["working_tree_clean"] is True, "checkout contains uncommitted changes")
        changed = state["changed_paths"]
        require(len(changed) == len(set(changed)) and set(changed) == set(ALLOW),
                "branch contains missing or unauthorized changed files")
        require(state["file_modes"] == dict.fromkeys(ALLOW, "100644"),
                "changed file is missing, symlink or has unexpected mode")
    if document is None:
        document = DOCUMENT.read_text(encoding="utf-8")
    for required in (
        "OFFLINE VALIDATION: PASS", "PRODUCTION DEPLOYMENT: NO-GO",
        "LIVE TRADING: NOT AUTHORIZED", "BROKER ORDER ROUTING: NOT AUTHORIZED",
        "0/12", "12/12", "human", "independent", "source-only", "not production evidence", CLOSE
    ):
        require(required.lower() in document.lower(), "closeout missing " + required)
    for forbidden in (
        r"PRODUCTION DEPLOYMENT:\s*GO\b", r"LIVE TRADING:\s*AUTHORIZED\b",
        r"BROKER ORDER ROUTING:\s*AUTHORIZED\b", r"PRODUCTION CERTIFICATION:\s*ACHIEVED\b"
    ):
        require(re.search(forbidden, document, re.IGNORECASE) is None,
                "document contains unsupported production authority")
    return {
        "validation":"PASS","record_kind":"READ_ONLY_ENGINEERING_REVIEW_NO_GO",
        "baseline_historical_sources_verified":26,
        "increment8_pinned_files_verified":len(hashes),
        "source_sha256":hashes,
        "controls_accepted":0,"controls_blocked":12,
        "human_owners_confirmed":0,"independent_receipts":0,"independent_signatures":0,
        "branch_diff":"PASS" if state is not None else "NOT_CHECKED",
        "main_freeze":"PASS" if state is not None else "NOT_CHECKED",
        "production_certification":"NOT ACHIEVED","release_freeze":"ACTIVE",
        "offline_validation":"PASS","production_deployment":"NO-GO",
        "live_trading":"NOT AUTHORIZED","broker_order_routing":"NOT AUTHORIZED",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path)
    options = parser.parse_args()
    try:
        result = verify(load_package(),state=checkout_state())
    except (ValueError, KeyError, TypeError, OSError, subprocess.CalledProcessError) as e:
        print("INCREMENT 9 REVIEW FREEZE BLOCKED: "+str(e))
        return 1
    if options.output:
        options.output.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print("INCREMENT 9 REVIEW PASS: source consistency only, 0/12 accepted, NO-GO.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
