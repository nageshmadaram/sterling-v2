#!/usr/bin/env python3
"""Fail-closed SOURCE-ONLY review of next-upgrade traceability files.

A PASS means structurally complete NO-GO preparation, not runtime validation,
independent control acceptance, production safety, or trading authority.
No network, credentials, broker, DB, production environment or deployment access.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "docs/evidence/2026-10-29-next-upgrade"
IDS = ("H01", "T02", "S03", "I04", "D05", "R06", "B07", "M08", "A09", "L10", "F11", "G12")
DENIED = {
    "production_certification": "NOT_ACHIEVED",
    "production_deployment": "NO_GO",
    "live_trading": "NOT_AUTHORIZED",
    "broker_order_routing": "NOT_AUTHORIZED",
    "main_merge": "NOT_AUTHORIZED",
    "independent_signoff": "NOT_PROVIDED",
}
SPEC = "docs/implementation/2026-10-29-next-upgrade-spec.md"
SPEC_BLOB = "3591f4d15d0d0f5bc6b7c8c79309cad48ad0c0b4"
BASE = "27e39e954ee67dbd5ec1d68ab9d15ff15de7dd7f"
CERT = "e35eb40b76df5796e335312e4866a00c0a977db8"


def is_file_under(root: Path, relative: object) -> bool:
    if not isinstance(relative, str) or not relative or relative.startswith("/"):
        return False
    p = Path(relative)
    if ".." in p.parts or "\\" in relative or ":" in relative or p.is_absolute():
        return False
    return (root / p).is_file()


def validate_bundle(manifest: object, mapping: object, exceptions: object, root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    if not all(isinstance(x, dict) for x in (manifest, mapping, exceptions)):
        return ["all three package artifacts must be objects"]
    if manifest.get("schema") != "sterling.v2.next_upgrade.release_package.v1":
        errors.append("invalid manifest schema")
    if manifest.get("record_kind") != "SOURCE_TRACEABILITY_AND_EVIDENCE_PREPARATION_ONLY":
        errors.append("invalid manifest release scope")
    if manifest.get("main_observed_sha") != BASE or manifest.get("historical_certification_main_sha") != "939e611ecec4ba7663b6df846b66ec984bfca932":
        errors.append("release snapshot drift or historical baseline conflated")
    ref = manifest.get("certification_register_source")
    if not isinstance(ref, dict) or ref.get("observed_commit") != CERT or ref.get("mode") != "REFERENCE_ONLY_NOT_COPIED_OR_AMENDED":
        errors.append("independent certification register reference changed")
    if manifest.get("source_spec") != SPEC or manifest.get("source_spec_git_blob") != SPEC_BLOB:
        errors.append("authoritative spec reference changed")
    if is_file_under(root, SPEC):
        data = (root / SPEC).read_bytes()
        blob = hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\\0" + data).hexdigest()
        if blob != SPEC_BLOB:
            errors.append("source spec content drift from pinned git blob")
    else:
        errors.append("source spec unavailable")
    for key in ("freeze_active", "no_production_or_broker_actions"):
        if manifest.get(key) is not True:
            errors.append(f"unsafe release state: {key}")
    if manifest.get("offline_evidence_is_production_evidence") is not False:
        errors.append("offline evidence promoted to production evidence")
    decisions = manifest.get("decisions")
    if not isinstance(decisions, dict) or decisions != DENIED:
        errors.append("unauthorized decision, merge or signoff claim")
    features = manifest.get("features")
    if not isinstance(features, list) or len(features) != 5:
        errors.append("exactly five source inventory features required")
    else:
        seen: set[str] = set()
        for item in features:
            if not isinstance(item, dict):
                errors.append("invalid feature row")
                continue
            ident = item.get("id")
            if not isinstance(ident, str) or ident in seen:
                errors.append("missing or duplicate feature id")
            else:
                seen.add(ident)
            if item.get("implementation_state") != "DOCUMENTED_IN_BASELINE" or item.get("slice_validation") != "SOURCE_PATHS_ONLY":
                errors.append(f"feature {ident} overclaims implementation validation")
            if item.get("runtime_reverified_this_slice") is not False or item.get("production_accepted") is not False:
                errors.append(f"feature {ident} has unsupported acceptance claim")
            for field in ("source_files", "prior_evidence"):
                values = item.get(field)
                if not isinstance(values, list) or not values:
                    errors.append(f"feature {ident} missing {field}")
                elif not all(is_file_under(root, p) for p in values):
                    errors.append(f"feature {ident} has nonexistent/unsafe {field}")
            ids = item.get("control_ids")
            if not isinstance(ids, list) or not ids or any(x not in IDS for x in ids):
                errors.append(f"feature {ident} invalid control references")
    if mapping.get("schema") != "sterling.v2.next_upgrade.control_map.v1":
        errors.append("invalid control map schema")
    if mapping.get("record_kind") != "REQUIREMENTS_NOT_ACCEPTANCE" or mapping.get("register_snapshot") != ref:
        errors.append("control map falsely implies acceptance or changes register source")
    controls = mapping.get("controls")
    if not isinstance(controls, list) or len(controls) != 12:
        errors.append("exactly 12 controls required")
    else:
        observed: list[str] = []
        for entry in controls:
            if not isinstance(entry, dict):
                errors.append("invalid control row")
                continue
            ident = entry.get("id")
            observed.append(ident)
            if entry.get("status") != "BLOCKED" or entry.get("review_decision") != "NOT_PROVIDED":
                errors.append(f"control {ident} improperly accepted")
            if entry.get("named_owner") is not None or entry.get("independent_reviewer") is not None or entry.get("received_independent_evidence") != []:
                errors.append(f"control {ident} invents owner, reviewer or receipt")
            if not isinstance(entry.get("accountable_role"), str) or not entry["accountable_role"].strip():
                errors.append(f"control {ident} lacks accountable role")
            if not isinstance(entry.get("verification_method"), str) or len(entry["verification_method"]) < 12:
                errors.append(f"control {ident} lacks verification method")
            evidence = entry.get("evidence_required")
            if not isinstance(evidence, list) or len(evidence) < 2 or any(not isinstance(v, str) or len(v) < 15 for v in evidence):
                errors.append(f"control {ident} lacks evidence requirements")
        if observed != list(IDS):
            errors.append("missing, reordered or duplicate control IDs")
    if exceptions.get("schema") != "sterling.v2.next_upgrade.exceptions.v1":
        errors.append("invalid exception schema")
    ex = exceptions.get("exceptions")
    if not isinstance(ex, list) or len(ex) < 1:
        errors.append("missing exception register")
    else:
        refs: set[str] = set()
        used: set[str] = set()
        for item in ex:
            if not isinstance(item, dict):
                errors.append("invalid exception row")
                continue
            if not isinstance(item.get("id"), str) or item["id"] in used:
                errors.append("duplicate or missing exception ID")
            else:
                used.add(item["id"])
            if item.get("status") != "OPEN" or item.get("severity") != "BLOCKER":
                errors.append("exception incorrectly closed")
            if not isinstance(item.get("mitigation"), str) or len(item["mitigation"]) < 15:
                errors.append("exception lacks mitigation")
            ids = item.get("controls")
            if not isinstance(ids, list) or not ids or any(ident not in IDS for ident in ids):
                errors.append("invalid exception control links")
            else:
                refs.update(ids)
        if refs != set(IDS):
            errors.append("exception register does not cover all blocked controls")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Source-only evidence-preparation gate; never production certification")
    parser.add_argument("--output", type=Path, help="write a sanitized JSON report")
    args = parser.parse_args()
    try:
        m = json.loads((PACKAGE / "release-package.json").read_text(encoding="utf-8"))
        c = json.loads((PACKAGE / "control-evidence-map.json").read_text(encoding="utf-8"))
        e = json.loads((PACKAGE / "exceptions.json").read_text(encoding="utf-8"))
        problems = validate_bundle(m, c, e)
    except (OSError, ValueError, TypeError) as exc:
        problems = [f"source package unreadable: {type(exc).__name__}"]
    report = {
        "schema": "sterling.v2.next_upgrade.source_review.v1",
        "validation": "PASS" if not problems else "FAIL",
        "scope": "source_only_no_external_services",
        "product_runtime_reverified": False,
        "real_environment_evidence_verified": False,
        "accepted_controls": 0,
        "blocked_controls": 12,
        "production_certification": "NOT_ACHIEVED",
        "production_deployment": "NO_GO",
        "live_trading": "NOT_AUTHORIZED",
        "broker_order_routing": "NOT_AUTHORIZED",
        "main_merge": "NOT_AUTHORIZED",
        "independent_signoff": "NOT_PROVIDED",
        "issues": problems,
    }
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if not problems else 2


if __name__ == "__main__":
    sys.exit(main())
