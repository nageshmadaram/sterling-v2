#!/usr/bin/env python3
"""Final source-only governance policy, structure and provenance checks.

No network, broker, device, deployment or live-system operations. A valid
manifest is NOT an approved release or independent evidence.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/evidence/2026-10-29-next-upgrade"
MANIFEST = EVIDENCE / "increment-6-final-frozen-governance-manifest.json"
REPORT = ROOT / "docs/implementation/2026-10-29-final-policy-closeout-increment-6.md"
IDS = ("H01", "T02", "S03", "I04", "D05", "R06", "B07", "M08", "A09", "L10", "F11", "G12")
DECISION = {
    "OFFLINE VALIDATION": "PASS", "PRODUCTION DEPLOYMENT": "NO-GO",
    "LIVE TRADING": "NOT AUTHORIZED", "BROKER ORDER ROUTING": "NOT AUTHORIZED",
    "production_certification": "NOT ACHIEVED", "release_freeze": "ACTIVE",
    "merge_to_main": "NOT AUTHORIZED",
}
CLOSING = "Production certification remains not achieved. Release freeze remains active."


def check(ok: bool, label: str) -> None:
    if not ok:
        raise ValueError(label)


def load_package() -> dict:
    from scripts.verify_increment5_governance import load_package as old_load
    return {"manifest": json.loads(MANIFEST.read_text(encoding="utf-8")),
            "previous": old_load()}


def verify(data: dict, *, document: str | None = None) -> dict:
    from scripts.verify_increment5_governance import verify as verify_previous
    from scripts.verify_increment5_governance import IDS as PRIOR_IDS
    v = data["manifest"]
    old = data["previous"]
    check(verify_previous(old)["validation"] == "PASS", "prior source-only packet cannot be verified")
    check(tuple(PRIOR_IDS) == IDS, "control ID contract has changed")
    check(v["schema"] == "sterling.v2.final_frozen_governance.v1"
          and v["record_kind"] == "FINAL_SOURCE_ONLY_REVIEW_MANIFEST_NO_CERTIFICATION", "invalid schema or record kind")
    check(v["repository"] == "nageshmadaram/sterling-v2"
          and v["engineering_branch"] == "upgrade/evidence-traceability-20261029", "wrong repository/branch")
    check(v["source_baseline_commit"] == "3291c5fff9d1f7fc2fdb608ac25de135ae43a979"
          and v["main_snapshot_sha"] == "27e39e954ee67dbd5ec1d68ab9d15ff15de7dd7f",
          "baseline source identity changed")
    check(v["decisions"] == DECISION and v["release_freeze"] == "ACTIVE"
          and v["closing_statement"] == CLOSING, "NO-GO boundary changed")
    check(v["human_appointments_verified"] is False and v["operational_authority_granted"] is False,
          "human appointments or operational authority invented")
    check(all(value is False for value in v["operational_events"].values()), "operational event falsely claimed")
    quality = v["content_quality_policy"]
    for name in ("missing_fields_fail_closed", "paths_local_read_only", "redacted_artifact_only",
                 "reviewer_independence_required", "human_appointment_only_from_approved_external_source"):
        check(quality[name] is True, "quality safeguard disabled: " + name)
    for name in ("raw_credentials_allowed", "production_decision_derived_from_test_result"):
        check(quality[name] is False, "source evidence promoted into production approval")
    check(quality["accepted_schema"] == v["schema"]
          and quality["source_file_digest_kind"] == "SHA256_OF_REPOSITORY_SOURCE_BYTES_NOT_PRODUCTION_RECEIPT",
          "SHA-256 provenance confused with production receipt")
    review = v["review_scope"]
    check(review["kind"] == "PREPARED_FOR_HUMAN_REVIEW_ONLY"
          and review["control_accepted_count"] == 0 and review["control_blocked_count"] == 12
          and review["h01_tooling_validation"] == "PASS"
          and review["h01_production_evidence"] == "BLOCKED"
          and review["independent_signoff"] == "NOT PROVIDED"
          and review["certification"] == "NOT ACHIEVED", "final review scope overstates authority")
    check(len(review["missing_external_inputs"]) == 6 and all(isinstance(x,str) and len(x)>20
          for x in review["missing_external_inputs"]), "missing external evidence dependency")
    check([c["id"] for c in v["per_control"]] == list(IDS), "control list incomplete, duplicated or reordered")
    for row, base, role, oldrow in zip(v["per_control"],old["baseline"]["controls"],
                                       old["roles"]["controls"],old["packet"]["controls"]):
        check(row["id"] == base["id"] == role["id"] == oldrow["control_id"], "control id mismatch")
        check(row["status"] == base["status"] == role["status"] == "BLOCKED", "production control accepted")
        check(row["proposed_team"] == role["responsibility_proposal"]
              and row["team_confirmed"] is False
              and row["named_owner_confirmed"] is False
              and row["collector_confirmed"] is False
              and row["reviewer_confirmed"] is False, "unverified assignment promoted")
        check(row["independent_receipt_count"] == 0 and row["approval"] == "NOT PROVIDED",
              "fabricated independent approval or receipt")
        check(row["required_evidence"] == base["evidence_required"]
              and row["missing_conditions"] == oldrow["missing_conditions"]
              and row["exception_refs"] == oldrow["exception_links"],
              "source required evidence, blocker or exception diverged")
    check([x["stage"] for x in v["signoff_stages"]]
          == ["SECURITY","OPERATIONS","RELEASE_GOVERNANCE"], "signoff stages incomplete")
    for row in v["signoff_stages"]:
        check(set(row) == {"stage","status","named_approver","approval_receipt"}
              and row["status"] == "NOT PROVIDED"
              and row["named_approver"] is None and row["approval_receipt"] is None,
              "independent signoff fabricated")
    p = v["provenance"]
    check(p["previous_independent_ci_run"] == 40899422041
          and p["previous_ci_status"] == "success"
          and p["previous_source_only_checks"] == 56
          and p["previous_increment5_focused_checks"] == 22
          and p["backend_disposable_tests"] == 66
          and p["frontend_mocked_tests"] == 21
          and p["headless_chromium_mocked_api_tests"] == 6
          and p["physical_device_sessions"] == 0
          and p["actual_real_environment_receipts"] == 0
          and p["independent_human_signatures"] == 0, "synthetic CI provenance or receipt counts altered")
    check(p["previous_independent_ci_url"] ==
          "https://github.com/nageshmadaram/sterling-v2/actions/runs/40899422041",
          "prior CI URL changed")
    check(len(v["sources"]) >= 20, "incomplete source manifest")
    paths = [s["path"] for s in v["sources"]]
    check(len(paths) == len(set(paths)), "duplicate source evidence paths")
    expected_kinds = {"HISTORICAL_IMPLEMENTATION","SOURCE_ONLY_VALIDATOR_OR_TEST","SOURCE_ONLY_EVIDENCE"}
    hashes = {}
    for row in v["sources"]:
        check(set(row) == {"path","kind","independent_production_receipt"}
              and row["kind"] in expected_kinds
              and row["independent_production_receipt"] is False, "unverified source kind or evidence")
        rel = row["path"]
        check(isinstance(rel,str) and not rel.startswith("/") and not rel.startswith(".."), "unsafe path")
        path = (ROOT / rel).resolve()
        check(path.is_relative_to(ROOT.resolve()) and path.is_file(), "missing/unsafe source: " + rel)
        check(not path.is_symlink(), "symlink source refused")
        check(path.stat().st_size < 1_000_000, "oversized source refused")
        check(row["kind"] == ("HISTORICAL_IMPLEMENTATION" if rel.startswith("docs/implementation/")
                             else "SOURCE_ONLY_VALIDATOR_OR_TEST" if rel.startswith("scripts/")
                             else "SOURCE_ONLY_EVIDENCE"), "source classification mismatch")
        hashes[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    check("docs/evidence/2026-10-29-next-upgrade/increment-5-closeout.json" in hashes,
          "previous validated receipt absent")
    if document is None:
        document = REPORT.read_text(encoding="utf-8")
    for phrase in ("OFFLINE VALIDATION: PASS","PRODUCTION DEPLOYMENT: NO-GO",
                   "LIVE TRADING: NOT AUTHORIZED","BROKER ORDER ROUTING: NOT AUTHORIZED",
                   "0/12","12/12", "human appointments", "reviewer",
                   "not production evidence", CLOSING):
        check(phrase.lower() in document.lower(), "final document missing decision/limitation: " + phrase)
    for pattern in (r"PRODUCTION DEPLOYMENT:\s*GO\b",r"LIVE TRADING:\s*AUTHORIZED\b",
                    r"BROKER ORDER ROUTING:\s*AUTHORIZED\b",r"PRODUCTION CERTIFICATION:\s*ACHIEVED\b"):
        check(re.search(pattern, document, flags=re.I) is None, "final document falsely authorizes release")
    return {
        "validation":"PASS","classification":"SOURCE_ONLY_REVIEW_NOT_PRODUCTION_EVIDENCE",
        "controls_accepted":0,"controls_blocked":12,"human_owners_confirmed":0,
        "independent_signatures":0,"real_environment_receipts":0,
        "source_files_verified":len(hashes),
        "source_sha256":hashes,
        "production_certification":"NOT ACHIEVED",
        "production_deployment":"NO-GO",
        "live_trading":"NOT AUTHORIZED",
        "broker_order_routing":"NOT AUTHORIZED",
        "release_freeze":"ACTIVE",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    opts = parser.parse_args()
    try:
        result = verify(load_package())
    except (ValueError, KeyError, OSError, TypeError) as exc:
        print("GOVERNANCE POLISH VALIDATION FAILED: " + str(exc))
        return 1
    if opts.output:
        opts.output.write_text(json.dumps(result,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    print("SOURCE REVIEW PASS: 0/12 accepted, 12/12 blocked, " +
          str(result["source_files_verified"]) + " local files fingerprinted. NO-GO.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
