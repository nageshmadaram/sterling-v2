"""Validate an H01 local observation's structure, NOT its real-world truth.

This verifier is intentionally offline. It neither connects to external
systems nor accepts a host evidence control. A SHA256 digest only identifies
the observed file; it is not a signature or independent witness.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

from certification.observe_host_h01 import SAFE_REF

MAX_FILE_BYTES = 32768
HEX_ID = re.compile(r"^[A-Za-z0-9._+~-]{1,128}$")
TOP_KEYS = frozenset({
    "schema", "control_id", "environment_id", "authorization_ref",
    "observed_at_utc", "source", "candidate_commit", "observed_facts",
    "independent_reviewer", "independent_approval",
    "network_segmentation_verified", "runtime_boundary_verified",
    "production_security_certified", "control_acceptance",
    "production_deployment", "broker_order_routing", "live_trading",
    "interpretation",
})
EXPECTED = {
    "schema": "sterling.v2.h01.host_observation.v1",
    "control_id": "H01",
    "source": "local_host_unprivileged_readonly",
    "candidate_commit": None,
    "independent_reviewer": None,
    "independent_approval": None,
    "network_segmentation_verified": False,
    "runtime_boundary_verified": False,
    "production_security_certified": False,
    "control_acceptance": "NOT_VERIFIED",
    "production_deployment": "NO_GO",
    "broker_order_routing": "NOT_AUTHORIZED",
    "live_trading": "NOT_AUTHORIZED",
    "interpretation": (
        "Partial, untrusted local observations only. H01 requires signed actual "
        "host identity, separate network/privilege-denial receipts and review."
    ),
}


def unique_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate JSON keys instead of accepting a forged last value."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _exact_boolean_or_none(value: object) -> bool:
    return value is None or type(value) is bool


def validate_observation(payload: object, env: str, approval: str) -> list[str]:
    """Pure structural validation; never produces an acceptance decision."""
    issues: list[str] = []
    if not isinstance(payload, dict):
        return ["observation is not an object"]
    if not SAFE_REF.fullmatch(env) or not SAFE_REF.fullmatch(approval):
        return ["expected environment and authorization references must be opaque"]
    if set(payload) != TOP_KEYS:
        issues.append("top-level schema differs from fixed H01 observation")
    for field, value in EXPECTED.items():
        if type(payload.get(field)) is not type(value) or payload.get(field) != value:
            issues.append("forbidden acceptance/authority mutation: " + field)
    for field, value in (("environment_id", env), ("authorization_ref", approval)):
        if payload.get(field) != value:
            issues.append("mismatched " + field)
    timestamp = payload.get("observed_at_utc")
    if not isinstance(timestamp, str) or len(timestamp) > 64:
        issues.append("missing UTC timestamp")
    else:
        try:
            date = dt.datetime.fromisoformat(timestamp)
            if date.tzinfo is None or date.utcoffset() != dt.timedelta(0):
                issues.append("timestamp must include UTC offset")
        except ValueError:
            issues.append("invalid observation timestamp")
    facts = payload.get("observed_facts")
    if not isinstance(facts, dict) or set(facts) != {
        "os_release", "kernel_release", "process_security", "kernel_isolation_signals"
    }:
        return issues + ["observation facts do not match the fixed allowlist"]

    os_data = facts["os_release"]
    if not isinstance(os_data, dict) or set(os_data) != {"id", "version_id"}:
        issues.append("OS identity is not constrained")
    elif any(v is not None and (not isinstance(v, str) or not HEX_ID.fullmatch(v))
             for v in os_data.values()):
        issues.append("unexpected OS metadata")

    kernel = facts["kernel_release"]
    if not isinstance(kernel, str) or not HEX_ID.fullmatch(kernel):
        issues.append("unexpected kernel release value")

    proc = facts["process_security"]
    if not isinstance(proc, dict) or set(proc) != {
        "no_new_privileges", "seccomp_mode",
        "effective_caps_empty", "running_as_root"
    }:
        issues.append("process security fields are not constrained")
    else:
        if not all(_exact_boolean_or_none(proc[x]) for x in (
            "no_new_privileges", "effective_caps_empty"
        )):
            issues.append("invalid process security booleans")
        if type(proc["running_as_root"]) is not bool:
            issues.append("unknown effective privilege state")
        if proc["seccomp_mode"] is not None and (
            type(proc["seccomp_mode"]) is not int
            or proc["seccomp_mode"] not in (0, 1, 2)
        ):
            issues.append("unknown seccomp state")

    signals = facts["kernel_isolation_signals"]
    if not isinstance(signals, dict) or set(signals) != {
        "apparmor_module_enabled", "selinux_enforcing",
        "user_namespace_mapping_present"
    }:
        issues.append("kernel isolation signals are not constrained")
    elif not all(_exact_boolean_or_none(v) for v in signals.values()):
        issues.append("invalid kernel isolation signal")
    return issues


def read_private_observation(path: Path) -> tuple[dict[str, Any], str]:
    if not path.is_absolute():
        raise ValueError("absolute path required")
    details = path.lstat()
    if not stat.S_ISREG(details.st_mode):
        raise ValueError("regular, non-symlink observation file required")
    if stat.S_IMODE(details.st_mode) & 0o077:
        raise ValueError("observation file must not be group/world readable")
    # No-follow / regular-file recheck closes path replacement where supported.
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        props = os.fstat(fd)
        if not stat.S_ISREG(props.st_mode) or props.st_ino != details.st_ino:
            raise ValueError("observation file changed during inspection")
        if props.st_dev != details.st_dev or stat.S_IMODE(props.st_mode) & 0o077:
            raise ValueError("observation file not securely stored")
        if props.st_size > MAX_FILE_BYTES:
            raise ValueError("observation exceeds size limit")
        with os.fdopen(fd, "rb") as fh:
            fd = -1
            raw = fh.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError("observation exceeds size limit")
    finally:
        if fd >= 0:
            os.close(fd)
    parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_json_pairs)
    if not isinstance(parsed, dict):
        raise ValueError("observation is not a JSON object")
    return parsed, hashlib.sha256(raw).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check H01 report shape ONLY (never certify)")
    parser.add_argument("--observation", type=Path, required=True)
    parser.add_argument("--environment-id", required=True)
    parser.add_argument("--authorization-ref", required=True)
    args = parser.parse_args(argv)
    try:
        payload, digest = read_private_observation(args.observation)
        errors = validate_observation(payload, args.environment_id, args.authorization_ref)
    except (ValueError, UnicodeError, OSError, json.JSONDecodeError) as exc:
        errors, digest = [f"invalid or unreadable observation: {type(exc).__name__}"], None
    # Do not print host facts or requested refs to CI/terminal output.
    result = {
        "control": "H01",
        "structural_validation": "PASS" if not errors else "FAIL",
        "file_sha256": digest if not errors else None,
        "independently_verified": False,
        "independent_signoff": "NOT_PROVIDED",
        "control_acceptance": "BLOCKED",
        "production_certification": "BLOCKED",
        "production_deployment": "NO_GO",
        "live_trading": "NOT_AUTHORIZED",
        "broker_order_routing": "NOT_AUTHORIZED",
        "issues": errors,
    }
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main())
