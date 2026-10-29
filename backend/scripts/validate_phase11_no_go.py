"""Fail-closed, offline-only Phase 11 source/config/branch release gate.

Returns success only for the evidence-review posture, never for deployment.
No network, broker, DB connection, launch command, credential or Git mutation.
Run from the checked-out repository on a branch based on validated Phase 10.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FROZEN_MAIN = "939e611ecec4ba7663b6df846b66ec984bfca932"
PHASE10 = "0a7c4e3cfeba0db4421b591d7a99a3db7424fff9"
ALLOWED_PHASE11 = frozenset({
    ".github/workflows/phase11-offline-final-certification.yml",
    "backend/scripts/validate_phase11_no_go.py",
    "backend/tests/test_offline_final_certification_phase11.py",
    "docs/architecture/PHASE11_FINAL_OFFLINE_ARCHITECTURE.md",
    "docs/operations/STERLING_V2_PHASE11_FINAL_VALIDATION_20261029.md",
    "docs/operations/STERLING_V2_PHASE11_BLOCKERS_20261029.md",
    "docs/operations/STERLING_V2_PHASE11_RELEASE_DECISION_20261029.md",
    "docs/operations/STERLING_V2_PHASE11_PRODUCTION_GAPS_20261029.md",
    "docs/operations/STERLING_V2_PHASE11_BRANCH_STATUS_20261029.md",
})
BLOCKERS = (
    "real_host_os_identity_and_runtime_isolation",
    "verified_tls_certificates_hsts_and_public_edge",
    "operator_role_enrollment_admin_mfa_rotation_recovery_and_sessions",
    "production_postgres_cluster_ownership_and_external_append_integrity",
    "real_monitoring_external_alert_receipts_and_disaster_recovery",
    "fresh_broker_session_identity_exposure_and_market_acceptance",
    "legacy_direct_broker_access_remediation",
    "full_host_browser_mobile_application_regression",
    "independent_production_restore_and_controlled_rollback",
    "live_execution_safety_and_production_certification",
)


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
    ).strip()


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise RuntimeError(reason)


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def verify() -> dict:
    main = git("rev-parse", "refs/remotes/origin/main")
    head = git("rev-parse", "HEAD")
    require(main == FROZEN_MAIN, "main branch changed since Phase 10 freeze")
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", PHASE10, head],
        cwd=ROOT, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    require(not git("status", "--porcelain"), "unclean checkout: certification refused")
    diff = tuple(filter(None, git("diff", "--name-only", PHASE10, head).splitlines()))
    require(bool(diff), "no Phase 11 evidence changes present")
    require(not (set(diff) - ALLOWED_PHASE11),
            "Phase 11 attempted to alter pre-existing implementation or execution authority")
    require(set(ALLOWED_PHASE11).issubset(diff),
            "Phase 11 certification artifacts are incomplete")

    entry = source("backend/app/offline_security_profile.py")
    docker = source("backend/Dockerfile.offline")
    compose = source("deploy/offline-only/compose.yml")
    proxy = source("frontend/nginx.offline.conf")
    grant = source("deploy/offline-only/privileges.sql")
    policy = source("backend/app/core/offline_security.py")
    for required in (
        "app.offline_security_profile:app",
        'USER 10001:10001',
    ):
        require(required in docker, "offline container isolation changed")
    require("main:app" not in docker, "legacy ASGI entrypoint forbidden")
    require("from main import" not in entry and "import backend.main" not in entry,
            "legacy execution router mounted in offline profile")
    require('execution_authorized":False' in entry, "offline monitoring authority invariant absent")
    for expected in (
        'STERLING_OFFLINE_ONLY: "true"', 'STERLING_OFFLINE_NO_BROKER: "true"',
        'STERLING_OFFLINE_REQUIRE_MFA: "true"', 'PAPER_TRADING: "true"',
        'internal: true', '127.0.0.1:8088:80', 'restart: "no"',
    ):
        require(expected in compose, "missing isolated deployment config: " + expected)
    require("location ^~ /api/ { return 404; }" in proxy,
            "proxy default-deny guard absent")
    require("location ^~ /api/v1/offline-validation/" in proxy,
            "offline evidence route allowlist absent")
    for expected in (
        "REVOKE CREATE, TEMPORARY", "REVOKE UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES",
        "Phase 10 privilege setup refused: non-isolated database",
    ):
        require(expected in grant, "database privilege boundary missing")
    for expected in ("per-admin MFA secret file required", "admin MFA step-up required",
                     'STERLING_OFFLINE_NO_BROKER', "offline_mfa"):
        require(expected in policy, "offline auth/MFA guard missing")

    for path in (
        "docs/architecture/PHASE11_FINAL_OFFLINE_ARCHITECTURE.md",
        "docs/operations/STERLING_V2_PHASE11_FINAL_VALIDATION_20261029.md",
        "docs/operations/STERLING_V2_PHASE11_BLOCKERS_20261029.md",
        "docs/operations/STERLING_V2_PHASE11_RELEASE_DECISION_20261029.md",
        "docs/operations/STERLING_V2_PHASE11_PRODUCTION_GAPS_20261029.md",
        "docs/operations/STERLING_V2_PHASE11_BRANCH_STATUS_20261029.md",
    ):
        document = source(path)
        require("NO-GO" in document and "live trading" in document.lower(),
                "certification document omits production no-go or live disclaimer: " + path)

    return {
        "schema": "sterling.v2.phase11.offline_certification.v1",
        "phase": 11,
        "source_commit": head,
        "phase10_validated_base": PHASE10,
        "main_frozen": main,
        "review_scope": "non_authoritative_offline_only",
        "offline_source_safety": "PASS",
        "integration_and_runtime_tests": "SEPARATE_CI_JOB_REQUIRED",
        "production_deployment": "NO_GO",
        "production_ready": False,
        "live_trading_authorized": False,
        "broker_writes_enabled": False,
        "execution_authorized": False,
        "external_service_credentials_needed": False,
        "merge_authorized": False,
        "deployment_authorized": False,
        "remaining_blockers": list(BLOCKERS),
        "phase11_changed_paths": list(diff),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 11 offline NO-GO gate")
    parser.add_argument("--output", type=Path, help="optional sanitized JSON output path")
    args = parser.parse_args()
    try:
        result = verify()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print("BLOCKED: Phase 11 source gate cannot certify the offline stack: " + str(exc),
              file=sys.stderr)
        return 2
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
