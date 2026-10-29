#!/usr/bin/env python3
"""Fail-closed source-only Increment 4 review-gate checker.

It cannot contact GitHub, operator sessions, production hosts, devices, brokers,
providers, vaults or deploy. No evidence accepted or sign-off granted here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "docs/evidence/2026-10-29-next-upgrade"
IDS = ("H01", "T02", "S03", "I04", "D05", "R06", "B07", "M08", "A09", "L10", "F11", "G12")
DECISION = {
    "offline_validation": "PASS",
    "production_deployment": "NO-GO",
    "live_trading": "NOT AUTHORIZED",
    "broker_order_routing": "NOT AUTHORIZED",
    "production_certification": "NOT ACHIEVED",
    "release_freeze": "ACTIVE",
    "independent_signoff": "NOT PROVIDED",
    "merge_to_main": "NOT AUTHORIZED",
}


def read_json(filename: str) -> dict:
    return json.loads((FOLDER / filename).read_text(encoding="utf-8"))


def load_package() -> dict:
    return {
        "prior": read_json("control-evidence-map.json"),
        "owner": read_json("increment-4-owner-confirmation.json"),
        "session": read_json("increment-4-session-contract.json"),
        "device": read_json("increment-4-physical-device-matrix.json"),
        "governance": read_json("increment-4-governance-review.json"),
    }


def guard(condition: bool, explanation: str) -> None:
    if not condition:
        raise ValueError(explanation)


def verify(data: dict) -> dict:
    baseline = data["prior"]["controls"]
    owner = data["owner"]
    session = data["session"]
    device = data["device"]
    review = data["governance"]
    expected = list(IDS)
    guard([c["id"] for c in baseline] == expected, "prior controls mismatch")
    guard([c["control_id"] for c in owner["controls"]] == expected, "owner-control set or ordering mismatch")
    guard([c["id"] for c in review["control_register"]] == expected, "governance-control set mismatch")
    guard(owner["record_kind"] == "REVIEW_INTAKE_NOT_APPOINTMENT", "owner intake cannot claim appointments")
    for field in ("confirmed_named_human_owners", "confirmed_accountable_teams", "confirmed_collectors", "confirmed_independent_reviewers"):
        guard(owner[field] == 0, field + " cannot be inferred")
    for old, new, gov in zip(baseline, owner["controls"], review["control_register"]):
        guard(old["status"] == new["status"] == gov["status"] == "BLOCKED", "accepted control detected")
        guard(old["id"] == new["control_id"] == gov["id"], "mismatched control id")
        guard(new["proposed_team"] and new["proposed_independent_reviewer_role"], "missing role proposal")
        guard(new["team_confirmed"] is False, "unverified team claimed confirmed")
        for f in ("confirmed_team_slug", "confirmed_team_acceptance_reference", "named_accountable_owner", "owner_acknowledged_at_utc",
                  "owner_approval_reference", "named_evidence_collector", "collector_approval_reference",
                  "named_independent_reviewer", "reviewer_acceptance_reference"):
            guard(new[f] is None, f + " must be unassigned")
        guard(new["reviewer_independence_verified"] is False, "independence not verified")
        guard(new["evidence_collection_required"] == old["evidence_required"], "evidence requirement changed")
        guard(new["received_receipts"] == [], "receipt acceptance unsupported")
        guard(new["independent_review_decision"] == "NOT_PROVIDED", "review prematurely approved")
        guard(gov["named_owner_confirmed"] is False and gov["independent_reviewer_confirmed"] is False,
              "governance appointment cannot be inferred")
        guard(gov["independent_receipts"] == 0 and gov["acceptance"] == "NOT_PROVIDED", "governance receipt/acceptance")
    guard(owner["status_counts"] == {"accepted": 0, "blocked": 12}, "owner status counts")
    guard(review["totals"] == {"accepted": 0, "blocked": 12, "confirmed_human_owners": 0,
                                "independent_receipts": 0, "independent_signatures": 0}, "governance totals")
    guard(review["strict_decision"] == DECISION, "freeze or decision changed")
    guard(all(v is False for v in review["actions"].values()), "operational action claimed")
    guard(all(x["status"] in ("BLOCKED", "NOT_PROVIDED") for x in review["review_steps"]), "governance approved prematurely")
    guard(session["record_kind"] == "SOURCE_INSPECTION_AND_TEST_GAP_NOT_MFA_ACCEPTANCE", "MFA evidence promoted")
    guard(session["status"] == "CONTRACT_REVIEWED_GAPS_OPEN", "MFA contract claim unsupported")
    guard(session["mfa_production_acceptance"] == "BLOCKED", "MFA accepted without evidence")
    guard(session["device_session_production_acceptance"] == "BLOCKED", "device session accepted without evidence")
    guard(session["mfa_implemented"] is False and session["step_up_implemented"] is False
          and session["device_attestation_verified"] is False, "synthetic session elevated to MFA")
    guard(len(session["requirements"]) >= 5 and all(x["status"] == "BLOCKED" for x in session["requirements"]),
          "missing MFA/device open requirements")
    guard(session["authorization_boundary"] == {
        "production_deployment": "NO_GO",
        "live_trading": "NOT_AUTHORIZED",
        "broker_order_routing": "NOT_AUTHORIZED"}, "session authorized operation")
    guard(device["record_kind"] == "ACCEPTANCE_CRITERIA_NOT_EXECUTED", "physical acceptance falsely claimed")
    guard(device["physical_acceptance"] == "NOT_ACHIEVED" and device["actual_physical_device_sessions"] == 0,
          "physical observation not supplied")
    guard(device["real_device_acceptance_claim"] is False and device["f11_control"] == "BLOCKED",
          "physical validation falsely promoted")
    guard(device["source_run"]["browser_tests_passed"] == 6 and device["source_run"]["viewport_widths"] == [390, 1440],
          "synthetic Chromium provenance mismatch")
    guard(len(device["cases"]) >= 6 and all(x["status"] == "BLOCKED" for x in device["cases"]),
          "physical matrix acceptance bypass")
    for item in device["cases"]:
        guard(all(item[key] is None for key in ("named_test_operator", "approved_nonproduction_device",
                       "session_capture", "artifact_reference", "signed_test_result")), "fabricated physical receipt")
    guard(review["record_kind"] == "PREPARED_NO_GO_REVIEW_INTAKE", "review not source-only")
    for entry in review["sources"].values():
        path = ROOT / entry
        guard(path.is_file() and path.resolve().is_relative_to(ROOT.resolve()), "missing or unsafe source reference")
    # Inspect source anchors to prevent documentation going stale on the branch. This is
    # not proof that an MFA route is absent in the entire project.
    token_source = (ROOT / "backend/app/core/tokens.py").read_text(encoding="utf-8")
    auth_source = (ROOT / "backend/app/api/v1/endpoints/auth.py").read_text(encoding="utf-8")
    admin_source = (ROOT / "backend/app/api/v1/endpoints/admin_connections.py").read_text(encoding="utf-8")
    for token in ("ACCESS_TTL_S", "REFRESH_TTL_S", "mint_access", "mint_refresh", "expected_typ"):
        guard(token in token_source, "JWT source changed; re-review session contract")
    for token in ('"/remember"', '"/logout"', '"/refresh"', "bump_token_version"):
        guard(token in auth_source, "auth source changed; re-review session contract")
    for token in ("login-credentials", "expected_user_id", "expected_updated_at_ms"):
        guard(token in admin_source, "admin credential source changed; re-review")
    return {
        "validation": "PASS",
        "scope": "SOURCE_ONLY_NO_AUTHORITY",
        "controls_accepted": 0,
        "controls_blocked": 12,
        "owners_confirmed": 0,
        "physical_device_sessions": 0,
        "independent_signatures": 0,
        "mfa_accepted": False,
        "production_certification": "NOT_ACHIEVED",
        "production_deployment": "NO_GO",
        "live_trading": "NOT_AUTHORIZED",
        "broker_order_routing": "NOT_AUTHORIZED",
        "release_freeze": "ACTIVE",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    try:
        report = verify(load_package())
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print("INCREMENT4 BLOCKED: source package invalid: " + str(exc))
        return 1
    if options.output:
        options.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
