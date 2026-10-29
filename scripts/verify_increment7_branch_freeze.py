#!/usr/bin/env python3
"""Offline Git-source lock and branch-diff gate. No production or broker access."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from scripts.verify_increment6_closeout import load_package as load_previous, verify as verify_previous

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/evidence/2026-10-29-next-upgrade"
LOCK = EVIDENCE / "increment-7-source-integrity-lock.json"
DOC = ROOT / "docs/implementation/2026-10-29-final-frozen-branch-review-increment-7.md"
BRANCH = "upgrade/evidence-traceability-20261029"
BASE = "5c21c9d4be5cb4bcef269e53043fb57a3aac07ab"
MAIN = "27e39e954ee67dbd5ec1d68ab9d15ff15de7dd7f"
IDS = ("H01","T02","S03","I04","D05","R06","B07","M08","A09","L10","F11","G12")
EXTRA = (
    "docs/evidence/2026-10-29-next-upgrade/increment-6-final-frozen-governance-manifest.json",
    "docs/implementation/2026-10-29-final-policy-closeout-increment-6.md",
    "scripts/verify_increment6_closeout.py",
    "scripts/tests/test_next_upgrade_increment6_closeout.py",
)
ALLOWED = (
    ".github/workflows/next-upgrade-evidence.yml",
    "docs/evidence/2026-10-29-next-upgrade/increment-7-source-integrity-lock.json",
    "docs/implementation/2026-10-29-final-frozen-branch-review-increment-7.md",
    "scripts/verify_increment7_branch_freeze.py",
    "scripts/tests/test_next_upgrade_increment7_branch_freeze.py",
)
DECISIONS = {
    "OFFLINE VALIDATION": "PASS",
    "PRODUCTION DEPLOYMENT": "NO-GO",
    "LIVE TRADING": "NOT AUTHORIZED",
    "BROKER ORDER ROUTING": "NOT AUTHORIZED",
    "production_certification": "NOT ACHIEVED",
    "release_freeze": "ACTIVE",
    "main_merge": "NOT AUTHORIZED",
}
CLOSING = "Production certification remains not achieved. Release freeze remains active."


def require(condition: bool, msg: str) -> None:
    if not condition:
        raise ValueError(msg)


def load_package() -> dict:
    return {
        "lock": json.loads(LOCK.read_text(encoding="utf-8")),
        "previous": load_previous(),
    }


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, stderr=subprocess.DEVNULL
    ).decode("utf-8").strip()


def checkout_state() -> tuple[list[str], str, str]:
    """Only read local checkout, no fetch, push or external GitHub calls."""
    branch = os.environ.get("GITHUB_REF_NAME") or git("branch", "--show-current")
    require(branch == BRANCH, "wrong checkout branch")
    require(git("merge-base", BASE, "HEAD") == BASE, "baseline is not an ancestor")
    main_sha = git("rev-parse", "refs/remotes/origin/main")
    changed = subprocess.check_output(
        ["git", "diff", "--no-renames", "--name-only", "-z", BASE, "HEAD"],
        cwd=ROOT, stderr=subprocess.DEVNULL
    )
    paths = [os.fsdecode(b) for b in changed.split(b"\0") if b]
    return paths, main_sha, branch


def safe_source(path_value: str) -> Path:
    require(isinstance(path_value, str) and not path_value.startswith("/")
            and "\\" not in path_value and "\0" not in path_value
            and ".." not in Path(path_value).parts, "unsafe source path")
    p = ROOT / path_value
    require(p.resolve().is_relative_to(ROOT.resolve()) and p.is_file()
            and not p.is_symlink() and p.stat().st_size <= 1_000_000,
            "missing, changed type, or oversize baseline source: " + path_value)
    return p


def verify(data: dict, *, changed_paths: list[str] | None = None,
           observed_main: str | None = None, doc: str | None = None,
           source_reader=None) -> dict:
    lock, previous = data["lock"], data["previous"]
    require(verify_previous(previous)["validation"] == "PASS", "prior evidence check failed")
    require(lock["schema"] == "sterling.v2.increment7.frozen_source_lock.v1"
            and lock["record_kind"] == "REPOSITORY_SOURCE_INTEGRITY_REFERENCE_NOT_PRODUCTION_RECEIPT",
            "source lock schema/claim changed")
    require(lock["repository"] == "nageshmadaram/sterling-v2"
            and lock["branch"] == BRANCH and lock["baseline_commit"] == BASE
            and lock["main_snapshot_sha"] == MAIN, "repo baseline or main snapshot changed")
    require(lock["source_identity"] == "GIT_BLOB_SHA1_COMPUTED_ON_FILE_BYTES"
            and lock["runtime_hash_kind"] == "SHA256_COMPUTED_FROM_CURRENT_FILE_BYTES_IN_READ_ONLY_CI"
            and lock["review_scope"] == "IMMUTABLE_BASELINE_SOURCE_COMPARISON_AND_NONOPERATIONAL_REVIEW_ONLY",
            "source hashes misrepresented as operational proof")
    require(lock["boundary"] ==
            "Git source identity and SHA-256 only measure repository file integrity; they do not certify real devices, real host, operational evidence or human acceptance.",
            "source integrity must not imply operational or human acceptance")
    require("do not certify" not in lock["final_sentence"].lower()
            and lock["final_sentence"] == CLOSING, "closing boundary changed")
    require(lock["decisions"] == DECISIONS, "decision boundary altered")
    gov = lock["governance"]
    require(gov == {
        "controls_accepted":0, "controls_blocked":12, "confirmed_human_owners":0,
        "independent_signoffs":0, "real_environment_receipts":0, "release_freeze":"ACTIVE",
        "h01_offline_tooling":"PASS", "h01_production_evidence":"BLOCKED"
    }, "human/production control authority inferred")
    prior_manifest = previous["manifest"]
    expected = [v["path"] for v in prior_manifest["sources"]] + list(EXTRA)
    rows = lock["source_entries"]
    require(len(rows) == len(expected) == 26
            and [row["path"] for row in rows] == expected, "pinned source set incomplete or reordered")
    require(tuple(lock["allowlisted_changes_since_baseline"]) == ALLOWED, "change allowlist broadened")
    digests: dict[str,str] = {}
    for row in rows:
        require(set(row) == {"path","git_blob_sha1","classification"}, "unexpected evidence row fields")
        rel = row["path"]
        source_path = safe_source(rel)
        require(row["classification"] == (
            "IMPLEMENTATION_SOURCE" if rel.startswith("docs/implementation/")
            else "SOURCE_ONLY_EVIDENCE" if rel.startswith("docs/evidence/")
            else "SOURCE_VALIDATION_TEST" if "/tests/" in rel
            else "SOURCE_VALIDATOR"), "source classification changed")
        expected_blob = row["git_blob_sha1"]
        require(isinstance(expected_blob,str) and re.fullmatch(r"[0-9a-f]{40}",expected_blob) is not None,
                "missing pinned Git blob identity")
        raw = source_reader(rel) if source_reader is not None else source_path.read_bytes()
        require(isinstance(raw,bytes) and len(raw) <= 1_000_000, "invalid source bytes")
        git_hash = hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()
        require(git_hash == expected_blob, "historical source changed after baseline: " + rel)
        digests[rel] = hashlib.sha256(raw).hexdigest()
    if changed_paths is not None:
        require(len(changed_paths) == len(set(changed_paths)), "duplicate changed file")
        require(set(changed_paths) == set(ALLOWED), "branch diff is missing or includes unauthorized files")
    if observed_main is not None:
        require(observed_main == MAIN, "main was changed since approved frozen snapshot")
    if doc is None:
        doc = DOC.read_text(encoding="utf-8")
    for phrase in (
        "OFFLINE VALIDATION: PASS", "PRODUCTION DEPLOYMENT: NO-GO",
        "LIVE TRADING: NOT AUTHORIZED", "BROKER ORDER ROUTING: NOT AUTHORIZED",
        "0/12", "12/12", "no independent production evidence",
        "named human", "synthetic", CLOSING
    ):
        require(phrase.lower() in doc.lower(), "final review lacks a mandatory qualifier: " + phrase)
    for pattern in (
        r"PRODUCTION DEPLOYMENT:\s*GO\b", r"LIVE TRADING:\s*AUTHORIZED\b",
        r"BROKER ORDER ROUTING:\s*AUTHORIZED\b", r"PRODUCTION CERTIFICATION:\s*ACHIEVED\b"
    ):
        require(re.search(pattern, doc, flags=re.IGNORECASE) is None,
                "unqualified operational approval language")
    return {
        "validation":"PASS",
        "classification":"SOURCE_REVIEW_ONLY_NO_PRODUCTION_AUTHORITY",
        "locked_sources":len(digests), "source_sha256":digests,
        "branch_hygiene":"PASS" if changed_paths is not None else "NOT_CHECKED_IN_UNIT_TEST",
        "main_unchanged":"PASS" if observed_main is not None else "NOT_CHECKED_IN_UNIT_TEST",
        "accepted_controls":0, "blocked_controls":12, "independent_signoffs":0,
        "human_appointments":0, "real_operational_receipts":0,
        "offline_validation":"PASS","production_deployment":"NO-GO",
        "live_trading":"NOT AUTHORIZED","broker_order_routing":"NOT AUTHORIZED",
        "production_certification":"NOT ACHIEVED","release_freeze":"ACTIVE",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        changed, main_sha, _branch = checkout_state()
        report = verify(load_package(),changed_paths=changed,observed_main=main_sha)
        if args.output:
            args.output.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    except (ValueError, OSError, TypeError, KeyError, subprocess.CalledProcessError) as exc:
        print("INCREMENT7 FREEZE CHECK BLOCKED: " + str(exc))
        return 1
    print("FINAL SOURCE LOCK PASS: 26 historical blobs, diff allowlist and frozen main; 0/12 accepted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
