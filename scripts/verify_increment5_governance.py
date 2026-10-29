#!/usr/bin/env python3
"""Source-only final Sterling V2 governance packet gate.

The gate verifies source consistency and rejects fabricated approval. It cannot
appoint humans, observe hardware/production systems, or authorize deployment.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "docs/evidence/2026-10-29-next-upgrade"
IDS = ("H01", "T02", "S03", "I04", "D05", "R06", "B07", "M08", "A09", "L10", "F11", "G12")
DECISIONS = {
    "OFFLINE VALIDATION": "PASS",
    "PRODUCTION DEPLOYMENT": "NO-GO",
    "LIVE TRADING": "NOT AUTHORIZED",
    "BROKER ORDER ROUTING": "NOT AUTHORIZED",
    "production_certification": "NOT ACHIEVED",
    "release_freeze": "ACTIVE",
    "main_merge": "NOT AUTHORIZED",
    "independent_release_signoff": "NOT PROVIDED",
    "h01_real_production_evidence": "BLOCKED",
}
CLOSEOUT = "Production certification remains not achieved. Release freeze remains active."


def fail(message: str) -> None:
    raise ValueError(message)


def check(predicate: bool, message: str) -> None:
    if not predicate:
        fail(message)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_package() -> dict:
    return {
        "packet": _read(FOLDER / "increment-5-final-review-packet.json"),
        "roles": _read(FOLDER / "increment-5-owner-reviewer-separation.json"),
        "baseline": _read(FOLDER / "control-evidence-map.json"),
        "exceptions": _read(FOLDER / "exceptions.json"),
        "prior": _read(FOLDER / "increment-4-governance-review.json"),
        "session": _read(FOLDER / "increment-4-session-contract.json"),
        "device": _read(FOLDER / "increment-4-physical-device-matrix.json"),
    }


def verify(data: dict) -> dict:
    packet, roles, baseline, exceptions = (
        data["packet"], data["roles"], data["baseline"], data["exceptions"])
    check(packet["record_kind"] == "NONOPERATIONAL_GOVERNANCE_REVIEW_PACKET_NO_ACCEPTANCE", "packet may not claim acceptance")
    check(roles["record_kind"] == "PROPOSED_ASSIGNMENT_STRUCTURE_NOT_HUMAN_CONFIRMATION", "roles may not imply authority")
    check(packet["engineering_branch"] == roles["branch"] == "upgrade/evidence-traceability-20261029", "wrong branch")
    check(packet["entry_commit"] == roles["baseline_commit"], "pinned baseline mismatch")
    check(packet["decision"] == DECISIONS, "decision boundary changed")
    check(packet["closeout_sentence"] == CLOSEOUT, "freeze closeout missing")
    check(packet["main_observed_sha"] == "27e39e954ee67dbd5ec1d68ab9d15ff15de7dd7f", "main baseline altered")
    check(all(item["draft"] is True and item["merged"] is False for item in packet["pr_state_at_intake"])
          and [x["pr"] for x in packet["pr_state_at_intake"]] == [13,14], "draft PR record altered")
    check(packet["current_review"] == {
        "status": "PREPARED_FOR_INDEPENDENT_HUMAN_INTAKE_NOT_ACCEPTANCE",
        "human_owners_confirmed": 0, "teams_confirmed": 0, "receipts_received": 0,
        "independent_reviewers_confirmed": 0, "independent_release_signatures": 0,
        "production_controls_accepted": 0, "production_controls_blocked": 12,
    }, "review counts or approvals changed")
    for key in ("confirmed_teams", "confirmed_named_owners", "confirmed_named_collectors",
                "confirmed_independent_reviewers"):
        check(roles[key] == 0, "human appointment not confirmed: " + key)
    check(roles["human_appointment_source"] == "NONE_PROVIDED"
          and roles["git_maintainer_implies_owner"] is False
          and roles["ai_can_appoint_people"] is False, "false human appointment authority")
    check(roles["status"] == {"accepted":0,"blocked":12,"independent_signoff":"NOT PROVIDED"},
          "roles status changed")
    check([x["id"] for x in baseline["controls"]] == list(IDS), "baseline controls do not match")
    check([x["id"] for x in roles["controls"]] == list(IDS), "ownership controls missing/duplicate")
    check([x["control_id"] for x in packet["controls"]] == list(IDS), "packet controls missing/duplicate")
    check(packet["package_classification"]["production_action"] is False
          and packet["package_classification"]["broker_connection"] is False
          and packet["package_classification"]["order_routing"] is False, "operational packet status")
    check(packet["package_classification"]["operations_now"] == "NONE_SOURCE_ONLY", "operational evidence invented")
    check(all(v is False for v in packet["prohibited_actions"].values()), "unauthorized action")
    check(len(packet["signoff_checklist"]) == 3, "missing independent signoff stages")
    check([x["stage"] for x in packet["signoff_checklist"]] == ["SECURITY","OPERATIONS","RELEASE_GOVERNANCE"],
          "signoff stages changed")
    for row in packet["signoff_checklist"]:
        check(row["named_approver"] is None and row["independent_signed_receipt"] is None
              and row["status"] == "NOT PROVIDED", "fabricated approval/signature")
    for base, role, item in zip(baseline["controls"], roles["controls"], packet["controls"]):
        check(base["id"] == role["id"] == item["control_id"], "control id changed")
        check(base["status"] == role["status"] == item["status"] == "BLOCKED", "control promoted")
        check(item["required_independent_evidence"] == base["evidence_required"]
              and item["verification_method"] == base["verification_method"], "baseline evidence changed")
        check(item["responsibility"]["proposed_team"] == role["responsibility_proposal"], "team role no longer traces")
        check(item["responsibility"]["independent_reviewer_role"] == role["independent_reviewer_role"],
              "reviewer role no longer traces")
        check(role["team_authority"] == "PROPOSED_UNCONFIRMED"
              and role["team_appointment_accepted"] is False
              and role["reviewer_independence_checked"] is False
              and role["approval_stage"] == "UNASSIGNED_NOT_ACCEPTED", "role promoted to assigned")
        for key in ("confirmed_team","team_acceptance_receipt","owner_person",
                    "owner_appointment_receipt","collector_person","collector_acceptance_receipt",
                    "independent_reviewer_person","reviewer_appointment_receipt",
                    "human_governance_approver","approval_signature_receipt"):
            check(role[key] is None, "role appointment evidence invented: " + key)
        check(role["separation_policy"]["independent_reviewer_must_differ_from_owner_and_collector"] is True,
              "independent reviewer separation weakened")
        check(role["separation_policy"]["governance_signatory_must_be_separately_authorized"] is True,
              "signatory authority weakened")
        for key in ("confirmed_team","named_owner","named_evidence_collector","independent_reviewer_person",
                    "approval_authority"):
            check(item["responsibility"][key] is None, "named appointment not provided: " + key)
        check(item["responsibility"]["owner_confirmed"] is False
              and item["responsibility"]["reviewer_independence_confirmed"] is False,
              "control owner/reviewer confirmed without evidence")
        check(item["currently_received_real_receipts"] == []
              and item["current_human_approval"] == "NOT PROVIDED", "unverified receipt or approval")
        required = item["missing_conditions"]
        check(len(required) >= 6 and len(set(required)) == len(required), "missing or duplicate blocker conditions")
        for expected in base["evidence_required"]:
            check("Independent redacted receipt: " + expected in required, "independent evidence requirement missing")
        check(any("independent technical review" in x for x in required), "independent review gate omitted")
        check("G12 independent governance sign-off after prior controls are accepted" in required, "governance gate omitted")
        check(item["exception_links"] == [
            x["id"] for x in exceptions["exceptions"] if base["id"] in x["controls"]], "exception crossrefs changed")
    check(len(packet["exceptions"]) == len(exceptions["exceptions"]) == 5, "exception list incomplete")
    for current, original in zip(packet["exceptions"], exceptions["exceptions"]):
        check(current["id"] == original["id"] and current["controls"] == original["controls"]
              and current["status"] == original["status"] == "OPEN"
              and current["severity"] == original["severity"] == "BLOCKER", "historical exception altered")
        check(current["source_register_is_historical"] is True, "historical register reinterpreted as current acceptance")
    check("historical slice" in packet["exceptions"][4]["current_interpretation"], "historical E-05 scope lost")
    check(data["session"]["mfa_production_acceptance"] == "BLOCKED"
          and packet["additional_unmet_gates"]["mfa_bound_stepup"]["status"] == "BLOCKED",
          "MFA accepted without evidence")
    check(data["device"]["physical_acceptance"] == "NOT_ACHIEVED"
          and packet["additional_unmet_gates"]["physical_mobile"]["physical_sessions"] == 0,
          "real-device evidence invented")
    check(packet["additional_unmet_gates"]["control_G12"] == "INDEPENDENT_RELEASE_GOVERNANCE_NOT_PROVIDED",
          "governance signoff false")
    check(all(value is None for key,value in packet["external_receipt_template"].items()
              if key not in ("acceptance_decision","note")), "nonblank receipt form implies supplied evidence")
    check(packet["external_receipt_template"]["acceptance_decision"] == "NOT PROVIDED",
          "receipt form has an approval")
    check(len(packet["source_documents"]) == 11, "authoritative documentation list incomplete")
    for src in packet["source_documents"] + list(packet["evidence_sources"].values()):
        if not isinstance(src, str) or not src.startswith(("docs/","https://","Offline ","H01 ")):
            fail("invalid provenance reference")
        if src.startswith("docs/"):
            path = (ROOT / src).resolve()
            check(path.is_relative_to(ROOT.resolve()) and path.is_file(), "missing/untrusted source path")
    check(packet["review_sequence"] and len(packet["review_sequence"]) >= 6, "independent review sequence missing")
    return {
        "validation":"PASS", "kind":"NONOPERATIONAL_SOURCE_ONLY_GOVERNANCE",
        "controls_accepted":0, "controls_blocked":12, "human_owners_confirmed":0,
        "independent_signatures":0, "independent_receipts_received":0,
        "external_host_or_provider_access_performed":False,
        "production_certification":"NOT ACHIEVED", "production_deployment":"NO-GO",
        "live_trading":"NOT AUTHORIZED", "broker_order_routing":"NOT AUTHORIZED",
        "release_freeze":"ACTIVE",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        outcome = verify(load_package())
    except (ValueError, KeyError, TypeError, OSError) as e:
        print("FINAL PACKET BLOCKED: " + str(e))
        return 1
    if args.output:
        args.output.write_text(json.dumps(outcome,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(outcome,sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
