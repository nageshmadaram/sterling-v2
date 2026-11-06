#!/usr/bin/env python3
"""Generate an exact-SHA, non-authorizing preproduction engineering evidence manifest.

No broker, database, user credentials or production execution are contacted.
An Actions PASS establishes only that technical checks ran; it never approves
the twelve independent controls or authorizes funded broker routing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from datetime import datetime, timezone

from scripts.validate_preproduction_controls import IDS, validate

_SHA = re.compile(r"[0-9a-f]{40}\Z")


def build_manifest(root: Path, head_sha: str, *, run_id: str = "") -> dict:
    """Return a closed review-only manifest or refuse missing/invalid provenance."""
    if not isinstance(head_sha, str) or not _SHA.fullmatch(head_sha):
        raise ValueError("An exact lowercase 40-character Git commit SHA is required")
    if run_id and (not run_id.isascii() or not run_id.isdecimal() or int(run_id) <= 0):
        raise ValueError("GitHub Actions run ID must be a positive integer")
    paths = (
        "docs/ops/PREPRODUCTION_CONTROLS.json",
        "docs/ops/PREPRODUCTION_EVIDENCE_REGISTRY.json",
    )
    sources = []
    for relative in paths:
        path = root / relative
        contents = path.read_bytes()
        sources.append({
            "path": relative,
            "sha256": hashlib.sha256(contents).hexdigest(),
        })
    controls = json.loads((root / paths[0]).read_text(encoding="utf-8"))
    evidence = json.loads((root / paths[1]).read_text(encoding="utf-8"))
    errors = validate(controls, evidence)
    if errors:
        raise ValueError("Control/evidence registries are not in fail-closed NO_GO form: "
                         + "; ".join(errors))
    rows = {row["id"]: row for row in controls["controls"]}
    items = {item["control"]: item for item in evidence["evidence"]}
    # A source-side registry cannot create sign-off or certify external proof.
    review = [
        {
            "control": ident,
            "owner": rows[ident]["owner"],
            "evidence_required": rows[ident]["evidence_required"],
            "next_action": rows[ident]["next_action"],
            "approval_gate": rows[ident]["approval_gate"],
            "technical_status": "NOT_INDEPENDENTLY_VERIFIED",
            "independent_signoff": "NOT_PROVIDED",
            "external_evidence": items[ident]["status"],
        }
        for ident in IDS
    ]
    return {
        "schema_version": 1,
        "kind": "ENGINEERING_REVIEW_MANIFEST_NOT_APPROVAL",
        "commit_sha": head_sha,
        "github_actions_run_id": run_id or None,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "decision": "NO_GO",
        "technical_review_complete": False,
        "production_certified": False,
        "broker_order_routing_authorized": False,
        "live_trading_authorized": False,
        "accepted_controls": 0,
        "blocked_controls": len(IDS),
        "source_registry_hashes": sources,
        "controls": review,
        "limitations": [
            "Static/CI evidence is not broker-side or operator sign-off",
            "Exact SHA binding does not certify runtime deployment or network/credential ownership",
            "UNKNOWN fills, partial journals and broker ACKs do not prove completed execution",
            "Unassigned or unsubmitted controls cannot be promoted by this manifest",
        ],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = build_manifest(args.root.resolve(), args.head_sha, run_id=args.run_id)
        target = args.output.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp." + str(os.getpid()))
        try:
            tmp.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        print("BLOCKED: finish review manifest unavailable: " + type(exc).__name__,
              file=sys.stderr)
        return 1
    print("Review manifest created for exact SHA; 0/12 approved; live broker routing: NO_GO.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
