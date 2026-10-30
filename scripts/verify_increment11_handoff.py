#!/usr/bin/env python3
"""Final read-only engineering handoff verification. No operational authority."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from scripts.verify_increment10_review_index import load_package as previous_load
from scripts.verify_increment10_review_index import verify as previous_verify

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs/evidence/2026-10-29-next-upgrade/increment-11-review-handoff.json"
DOCUMENT = ROOT / "docs/implementation/2026-10-30-final-governance-handoff-increment-11.md"
BRANCH = "upgrade/evidence-traceability-20261029"
BASE = "e99dd71b99916c61fdf5eaed23c76bfefe5d7c3e"
TREE = "81e178491d8d21b14bd34a40227ff74aa5a34e48"
MAIN = "27e39e954ee67dbd5ec1d68ab9d15ff15de7dd7f"
IDS = ("H01", "T02", "S03", "I04", "D05", "R06", "B07", "M08", "A09", "L10", "F11", "G12")
FILES = (
    ("docs/evidence/2026-10-29-next-upgrade/increment-10-final-readonly-review-index.json", "eb573faa7bce260e5f7909fbe8c9cf7ccca83f1e"),
    ("docs/implementation/2026-10-29-final-review-index-increment-10.md", "a4b894196883156239297ee60083b8837be5b588"),
    ("scripts/verify_increment10_review_index.py", "427ec7a4bc3bb8ac325ea166c3196cbd4b3d3a63"),
    ("scripts/tests/test_next_upgrade_increment10_review_index.py", "44152da9b6a352f16f8c1edaf13c6b4b443983e6"),
)
ALLOW = (
    ".github/workflows/next-upgrade-evidence.yml",
    "docs/evidence/2026-10-29-next-upgrade/increment-11-review-handoff.json",
    "docs/implementation/2026-10-30-final-governance-handoff-increment-11.md",
    "scripts/verify_increment11_handoff.py",
    "scripts/tests/test_next_upgrade_increment11_handoff.py",
)
DECISIONS = {
    "OFFLINE VALIDATION": "PASS",
    "PRODUCTION DEPLOYMENT": "NO-GO",
    "LIVE TRADING": "NOT AUTHORIZED",
    "BROKER ORDER ROUTING": "NOT AUTHORIZED",
    "production_certification": "NOT ACHIEVED",
    "release_freeze": "ACTIVE",
    "independent_release_signoff": "NOT PROVIDED",
    "main_merge": "NOT AUTHORIZED",
}
FINAL = "Production certification remains not achieved. Release freeze remains active."


def check(ok: bool, why: str) -> None:
    if not ok:
        raise ValueError(why)


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, stderr=subprocess.DEVNULL).decode().strip()


def checkout_state() -> dict:
    """Local Git read only. Does not fetch, push or access services."""
    branch = os.environ.get("GITHUB_REF_NAME") or git("branch", "--show-current")
    head = git("rev-parse", "HEAD")
    check(branch == BRANCH and git("merge-base", BASE, head) == BASE, "wrong branch/ancestry")
    if os.environ.get("GITHUB_SHA"):
        check(os.environ["GITHUB_SHA"] == head, "CI event/checkout identity mismatch")
    raw = subprocess.check_output(["git", "diff", "--no-renames", "--name-only", "-z", BASE, head],
                                  cwd=ROOT, stderr=subprocess.DEVNULL)
    return {
        "branch": branch, "head": head, "base_tree": git("rev-parse", BASE + "^{tree}"),
        "main_sha": git("rev-parse", "refs/remotes/origin/main"),
        "changed_files": [os.fsdecode(part) for part in raw.split(b"\0") if part],
        "clean": subprocess.check_output(["git", "status", "--porcelain", "-z"], cwd=ROOT) == b"",
        "modes": {p: git("ls-files", "--stage", "--", p).split(" ", 1)[0] for p in ALLOW},
    }


def load_package() -> dict:
    return {"handoff": json.loads(MANIFEST.read_text(encoding="utf-8")),
            "previous": previous_load()}


def verify(package: dict, *, state: dict | None = None,
           document: str | None = None, read_source=None) -> dict:
    m = package["handoff"]
    historical = previous_verify(package["previous"])
    check(historical["validation"] == "PASS" and historical["accepted"] == 0
          and historical["blocked"] == 12 and historical["increment9_pinned_files"] == 4,
          "previous evidence chain cannot be validated")
    check(m["schema"] == "sterling.v2.final_governance_handoff.v1"
          and m["kind"] == "READ_ONLY_UNMERGED_EVIDENCE_HANDOFF", "invalid review record type")
    check(m["repository"] == "nageshmadaram/sterling-v2" and m["branch"] == BRANCH
          and m["increment10_validated_commit"] == BASE and m["increment10_validated_tree"] == TREE
          and m["main_reference_snapshot"] == MAIN, "unexpected commit/tree/repo")
    check(m["ci_reference"] == {
        "run_id":40921149866,"commit":BASE,"result":"SUCCESS","jobs":11,
        "source_tests":164,"increment10_focused":20,"backend_disposable":66,
        "frontend_mocked":21,"chromium_mocked":6,"physical_device_sessions":0,
    }, "historical CI provenance altered")
    check(m["historical_replay"] == "PIN_INCREMENT10_CHECKOUT_TO_VALIDATED_COMMIT",
          "historical validation moved")
    check(m["allowed_increment11_paths"] == list(ALLOW), "source-only diff allowlist broadened")
    check(m["provenance_level"] == "GIT_BLOB_IDENTITIES_SHA256_AND_SYNTHETIC_CI_ONLY",
          "operational evidence claim unsupported")
    check(m["previous_control_manifest"] ==
          "docs/evidence/2026-10-29-next-upgrade/increment-6-final-frozen-governance-manifest.json"
          and m["previous_review_index"] ==
          "docs/evidence/2026-10-29-next-upgrade/increment-10-final-readonly-review-index.json",
          "source lineage changed")
    check(m["decisions"] == DECISIONS and m["final_statement"] == FINAL,
          "freeze or NO-GO status changed")
    check(m["counts"] == {
        "accepted":0,"blocked":12,"independent_real_environment_receipts":0,
        "confirmed_human_appointments":0,"independent_signatures":0,
    }, "invented acceptance or signoff")
    check(m["human_review"] == {
        "approved_owner_list":"NOT PROVIDED","reviewer_separation":"NOT CONFIRMED",
        "security_signoff":"NOT PROVIDED","operations_signoff":"NOT PROVIDED",
        "release_signoff":"NOT PROVIDED","h01_source_tooling":"PASS",
        "h01_production_evidence":"BLOCKED",
    }, "human reviewer or H01 acceptance invented")
    check(m["operations"] == {
        "main_merge":False,"production_deploy":False,"broker_connection":False,
        "broker_routing":False,"live_trading":False,"release_approval":False,
    }, "unsupported operational authority")
    check(m["limitation"] == "File integrity and synthetic CI evidence cannot prove operational safety, real-host acceptance, human sign-off or production authorization.",
          "source evidence promoted to authority")
    check([x["id"] for x in m["production_controls"]] == list(IDS),
          "production controls missing/reordered")
    for item in m["production_controls"]:
        check(item == {"id":item["id"],"status":"BLOCKED",
                       "owner_confirmed":False,"reviewer_confirmed":False,"receipts_accepted":0},
              "control accepted or appointment fabricated")
    check(len(m["future_work"]) == 5 and all(isinstance(x, str) and len(x) > 25 for x in m["future_work"]),
          "future independent requirements missing")
    check(len(m["pinned_sources"]) == 4, "pinned source set incomplete")
    hashes = {}
    for row, (path, expected_sha) in zip(m["pinned_sources"], FILES):
        check(row == {"path":path,"git_blob_sha1":expected_sha,
                      "kind":"FROZEN_REPOSITORY_SOURCE_NOT_PRODUCTION_RECEIPT"},
              "source path or pinned hash modified")
        src = ROOT / path
        check(src.is_file() and not src.is_symlink()
              and src.resolve().is_relative_to(ROOT.resolve())
              and src.stat().st_size < 1_000_000, "source unsafe or unavailable")
        buf = read_source(path) if read_source is not None else src.read_bytes()
        check(isinstance(buf, bytes) and len(buf) < 1_000_000, "bad file bytes")
        git_hash = hashlib.sha1(b"blob " + str(len(buf)).encode() + b"\0" + buf).hexdigest()
        check(git_hash == expected_sha, "pinned historical source changed: " + path)
        hashes[path] = hashlib.sha256(buf).hexdigest()
    if state is not None:
        check(state["branch"] == BRANCH and re.fullmatch(r"[a-f0-9]{40}", state["head"]) is not None,
              "wrong branch/HEAD")
        check(state["base_tree"] == TREE and state["main_sha"] == MAIN,
              "baseline tree/main changed")
        check(state["clean"] is True, "dirty working tree")
        paths = state["changed_files"]
        check(len(paths) == len(set(paths)) and set(paths) == set(ALLOW),
              "modified runtime file or incomplete review diff")
        check(state["modes"] == dict.fromkeys(ALLOW, "100644"),
              "unexpected path mode/type")
    if document is None:
        document = DOCUMENT.read_text(encoding="utf-8")
    for token in ("OFFLINE VALIDATION: PASS", "PRODUCTION DEPLOYMENT: NO-GO",
                  "LIVE TRADING: NOT AUTHORIZED", "BROKER ORDER ROUTING: NOT AUTHORIZED",
                  "0/12", "12/12", "source-only", "not production evidence",
                  "human", "BLOCKED", FINAL):
        check(token.lower() in document.lower(), "missing final review limitation: " + token)
    for pattern in (r"PRODUCTION DEPLOYMENT:\s*GO\b", r"LIVE TRADING:\s*AUTHORIZED\b",
                    r"BROKER ORDER ROUTING:\s*AUTHORIZED\b", r"PRODUCTION CERTIFICATION:\s*ACHIEVED\b"):
        check(re.search(pattern, document, re.I) is None,
              "false production approval in closeout document")
    return {
        "validation":"PASS","classification":"SOURCE_ONLY_HUMAN_HANDOFF_NOT_CERTIFICATION",
        "historic_increment10_sources_verified":4,"source_sha256":hashes,
        "accepted":0,"blocked":12,"confirmed_human_owners":0,"signed_receipts":0,
        "independent_signoffs":0,
        "branch_hygiene":"PASS" if state is not None else "NOT_CHECKED",
        "main_snapshot":"UNCHANGED" if state is not None else "NOT_CHECKED",
        "offline_validation":"PASS","production_deployment":"NO-GO",
        "live_trading":"NOT AUTHORIZED","broker_order_routing":"NOT AUTHORIZED",
        "production_certification":"NOT ACHIEVED","release_freeze":"ACTIVE",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    try:
        report = verify(load_package(), state=checkout_state())
        if options.output:
            options.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as e:
        print("INCREMENT11 REVIEW HANDOFF BLOCKED: " + str(e))
        return 1
    print("INCREMENT11 FINAL HANDOFF PASS: source integrity only, 12/12 BLOCKED, NO-GO.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
