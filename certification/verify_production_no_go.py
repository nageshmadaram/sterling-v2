"""Source-only production-certification intake gate. It NEVER issues a GO decision.

No host/network/broker/DB/credential access; no secret handling or deployment.
Success means the evidence register is complete as an explicit BLOCKED intake,
not that any production security requirement has been met.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTER = ROOT / "certification" / "production_evidence_register.json"
FROZEN_MAIN = "939e611ecec4ba7663b6df846b66ec984bfca932"
OFFLINE_CANDIDATE = "ce702ab1622e53f6233e1bf9a235ef87fc297a8f"
CONTROL_IDS = {f"{prefix}{number:02}" for prefix, number in (
    ("H", 1), ("T", 2), ("S", 3), ("I", 4), ("D", 5), ("R", 6),
    ("B", 7), ("M", 8), ("A", 9), ("L", 10), ("F", 11), ("G", 12),
)}
DENIED_FLAGS = (
    "real_host_verified", "real_operator_provisioning_verified",
    "real_broker_session_verified", "real_market_accepted",
    "production_certs_verified", "live_trading_authorized",
    "broker_order_routing_authorized", "production_deployment_authorized",
    "main_merge_authorized",
)


def evaluate_manifest(m: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(m, dict):
        return ["register must be a JSON object"]
    exact = {
        "schema": "sterling.v2.production_readiness.v1",
        "track": "independent_real_world_production_certification",
        "record_kind": "INITIAL_BLOCKED_INTAKE_ONLY",
        "repository": "nageshmadaram/sterling-v2",
        "main_sha_at_open": FROZEN_MAIN,
        "offline_candidate_sha": OFFLINE_CANDIDATE,
        "offline_ci_url": (
            "https://github.com/nageshmadaram/sterling-v2/actions/runs/40821249672"
        ),
        "offline_evidence_is_production_evidence": False,
        "production_decision": "NO_GO",
        "live_decision": "NOT_AUTHORIZED",
        "reviewer_signoff": "NOT_PROVIDED",
    }
    for key, expected in exact.items():
        if type(m.get(key)) is not type(expected) or m.get(key) != expected:
            errors.append("incorrect frozen field: " + key)
    for key in DENIED_FLAGS:
        if m.get(key) is not False:
            errors.append("unauthorized production/trading claim: " + key)
    controls = m.get("controls")
    if not isinstance(controls, list) or len(controls) != len(CONTROL_IDS):
        errors.append("all 12 distinct production acceptance controls required")
        return errors
    seen = set()
    for i, c in enumerate(controls):
        if not isinstance(c, dict):
            errors.append(f"control {i} is not an object")
            continue
        ident = c.get("id")
        if not isinstance(ident, str) or ident not in CONTROL_IDS or ident in seen:
            errors.append(f"invalid or duplicate control id: {ident}")
        else:
            seen.add(ident)
        if c.get("status") != "BLOCKED":
            errors.append(f"control {ident} must remain BLOCKED pending independent review")
        if not isinstance(c.get("name"), str) or len(c["name"]) < 8:
            errors.append(f"control {ident} missing descriptive name")
        required = c.get("evidence_required")
        if not isinstance(required, list) or len(required) < 2 or any(
            not isinstance(x, str) or len(x) < 20 for x in required
        ):
            errors.append(f"control {ident} missing real acceptance criteria")
        if c.get("independent_evidence") != []:
            errors.append(f"control {ident} contains unreviewed evidence claims")
        for field in ("observed_environment", "observed_at", "observer", "reviewer"):
            if c.get(field) is not None:
                errors.append(f"control {ident} asserts an unverified {field}")
        if c.get("decision_reason") != (
            "No independent real-world production-like acceptance evidence supplied."
        ):
            errors.append(f"control {ident} no-go rationale altered")
    if seen != CONTROL_IDS:
        errors.append("incomplete production-control IDs")
    return errors


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
    ).strip()


def verify_source_freeze() -> list[str]:
    errors = []
    try:
        if git("rev-parse", "refs/remotes/origin/main") != FROZEN_MAIN:
            errors.append("origin/main changed from recorded release freeze")
        if git("status", "--porcelain"):
            errors.append("dirty working tree; NO-GO proof refused")
        changed = [
            x for x in git("diff", "--name-only", FROZEN_MAIN, "HEAD").splitlines()
            if x
        ]
        if not changed:
            errors.append("certification branch contains no independent evidence work")
        if any(not (
            path.startswith("certification/")
            or path.startswith("docs/certification/")
            or path == ".github/workflows/production-certification-no-go.yml"
        ) for path in changed):
            errors.append("certification work touches application or deployment source")
        if not git("branch", "--show-current").startswith(
            "cert/production-readiness-evidence-"
        ):
            errors.append("certification must run on its independent evidence branch")
    except (OSError, subprocess.CalledProcessError):
        errors.append("cannot independently establish frozen branch ancestry")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed production NO-GO intake")
    parser.add_argument("--output", type=Path, help="optional sanitized JSON output")
    args = parser.parse_args()
    try:
        manifest = json.loads(REGISTER.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        print(f"BLOCKED: evidence register unavailable: {exc}", file=sys.stderr)
        return 2
    problems = evaluate_manifest(manifest) + verify_source_freeze()
    result = {
        "schema": "sterling.v2.production_readiness.review.v1",
        "scope": "source_only_initial_evidence_intake",
        "registration_integrity": "PASS" if not problems else "FAIL",
        "real_world_evidence_verified": False,
        "independently_observed_controls": 0,
        "blocked_control_count": len(CONTROL_IDS),
        "production_certification": "BLOCKED",
        "production_deployment": "NO_GO",
        "live_trading": "NOT_AUTHORIZED",
        "broker_order_routing": "NOT_AUTHORIZED",
        "main_merge": "NOT_AUTHORIZED",
        "external_approver_signoff": "NOT_PROVIDED",
        "source_issues": problems,
    }
    payload = json.dumps(result, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    # CI passing verifies a correctly maintained NO-GO intake, not any real host.
    return 0 if not problems else 2


if __name__ == "__main__":
    raise SystemExit(main())
