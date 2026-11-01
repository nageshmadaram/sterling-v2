#!/usr/bin/env python3
"""Validate governance structure only; never promote control acceptance."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

IDS = ("H01", "T02", "S03", "I04", "D05", "R06",
       "B07", "M08", "A09", "L10", "F11", "G12")


def validate(controls: dict, evidence: dict) -> list[str]:
    errors = []
    rows = controls.get("controls")
    items = evidence.get("evidence")
    if not isinstance(rows, list) or not isinstance(items, list):
        return ["Missing control or evidence list."]
    names = [row.get("id") for row in rows]
    observed = [row.get("control") for row in items]
    if len(rows) != 12 or set(names) != set(IDS) or len(set(names)) != 12:
        errors.append("Twelve unique, exact control IDs required.")
    if len(items) != 12 or set(observed) != set(IDS) or len(set(observed)) != 12:
        errors.append("One evidence record per control required.")
    if controls.get("decision") != "NO_GO" or evidence.get("decision") != "NO_GO":
        errors.append("Pre-production registry decision must remain NO_GO.")
    for field in ("production_certified", "live_trading_authorized", "broker_order_routing_authorized"):
        if controls.get(field) is not False:
            errors.append(field + " must be false.")
    if controls.get("acceptance_count") != 0 or controls.get("blocked_count") != 12:
        errors.append("Acceptance count must remain 0/12 until separately certified.")
    for row in rows:
        if row.get("current_status") != "BLOCKED" or row.get("independent_signoff") != "NOT_PROVIDED":
            errors.append("Unsupported acceptance claim in " + str(row.get("id")))
        for field in ("issue", "owner", "evidence_required", "next_action", "approval_gate"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                errors.append("Missing " + field + " in " + str(row.get("id")))
        if row.get("accepted_evidence_ids") != []:
            errors.append("Evidence accepted without external approval in " + str(row.get("id")))
    for item in items:
        if item.get("status") != "NOT_SUBMITTED" or item.get("independent_reviewer") is not None:
            errors.append("Unreviewed evidence must be NOT_SUBMITTED for " + str(item.get("control")))
    return errors


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1] / "docs/ops"
    parser.add_argument("--controls", type=Path, default=root / "PREPRODUCTION_CONTROLS.json")
    parser.add_argument("--evidence", type=Path, default=root / "PREPRODUCTION_EVIDENCE_REGISTRY.json")
    args = parser.parse_args(argv)
    try:
        controls = json.loads(args.controls.read_text(encoding="utf-8"))
        evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
        errors = validate(controls, evidence)
    except (OSError, ValueError) as exc:
        print("Governance registry could not be read: " + type(exc).__name__, file=sys.stderr)
        return 2
    for error in errors:
        print("BLOCKED: " + error, file=sys.stderr)
    if errors:
        return 1
    print("Governance schema: structurally valid; independent acceptance: 0/12; production: NO_GO.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
