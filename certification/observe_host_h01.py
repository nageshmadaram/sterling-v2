"""H01 read-only host observation for separately approved production-like assets.

NEVER certify, deploy, alter host configuration, reach a network/broker/DB,
or interact with credentials. A local *observation* requires independent
review, separate segmentation/privilege-denial receipts, and human sign-off.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import re
import stat
import sys
from pathlib import Path
from typing import Any

CONTROL_ID = "H01"
STATUS = "NOT_VERIFIED"
SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{3,95}$")
STATUS_KEYS = ("Uid", "Gid", "NoNewPrivs", "Seccomp", "CapEff")
MAX_READ_BYTES = 8192


def read_text(path: Path) -> str | None:
    """Best-effort fixed-path read; no filesystem traversal or wildcards."""
    try:
        with path.open("rb") as file:
            value = file.read(MAX_READ_BYTES + 1)
        if len(value) > MAX_READ_BYTES:
            return None
        return value.decode("utf-8", errors="replace")
    except (OSError, ValueError):
        return None


def os_release() -> dict[str, str | None]:
    src = read_text(Path("/etc/os-release")) or ""
    result: dict[str, str | None] = {"id": None, "version_id": None}
    for line in src.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key not in ("ID", "VERSION_ID"):
            continue
        value = value.strip().strip('"').strip("'")
        if re.fullmatch(r"[A-Za-z0-9._+-]{1,64}", value):
            result[key.lower()] = value
    return result


def status_bits() -> dict[str, Any]:
    src = read_text(Path("/proc/self/status")) or ""
    values: dict[str, Any] = {
        "no_new_privileges": None,
        "seccomp_mode": None,
        "effective_caps_empty": None,
        "running_as_root": os.geteuid() == 0,
    }
    found: dict[str, str] = {}
    for line in src.splitlines():
        k, sep, v = line.partition(":")
        if sep and k in STATUS_KEYS:
            found[k] = v.strip()
    if found.get("NoNewPrivs") in ("0", "1"):
        values["no_new_privileges"] = found["NoNewPrivs"] == "1"
    if found.get("Seccomp") in ("0", "1", "2"):
        values["seccomp_mode"] = int(found["Seccomp"])
    if re.fullmatch(r"[0-9a-fA-F]{1,32}", found.get("CapEff", "")):
        values["effective_caps_empty"] = int(found["CapEff"], 16) == 0
    return values


def kernel_isolation() -> dict[str, bool | None]:
    apparmor = read_text(Path("/sys/module/apparmor/parameters/enabled"))
    selinux = read_text(Path("/sys/fs/selinux/enforce"))
    uid_map = read_text(Path("/proc/self/uid_map"))
    # Signals, NOT independent proof of the actual deployed container policy.
    remapped: bool | None = None
    if uid_map:
        lines = uid_map.splitlines()
        if len(lines) == 1:
            parts = lines[0].split()
            if len(parts) == 3 and all(x.isdecimal() for x in parts):
                remapped = tuple(map(int, parts)) != (0, 0, 4294967295)
    return {
        "apparmor_module_enabled": (
            apparmor.strip().lower().startswith("y") if apparmor else None
        ),
        "selinux_enforcing": (
            selinux.strip() == "1" if selinux and selinux.strip() in ("0", "1") else None
        ),
        "user_namespace_mapping_present": remapped,
    }


def build_observation(environment_id: str, authorization_ref: str) -> dict[str, Any]:
    for name, value in (
        ("environment_id", environment_id),
        ("authorization_ref", authorization_ref),
    ):
        if not SAFE_REF.fullmatch(value):
            raise ValueError(f"{name} must be an opaque, safe, non-secret reference")
    return {
        "schema": "sterling.v2.h01.host_observation.v1",
        "control_id": CONTROL_ID,
        "environment_id": environment_id,
        "authorization_ref": authorization_ref,
        "observed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": "local_host_unprivileged_readonly",
        "candidate_commit": None,  # must be independently attested by reviewer
        "observed_facts": {
            "os_release": os_release(),
            "kernel_release": platform.release()[:128],
            "process_security": status_bits(),
            "kernel_isolation_signals": kernel_isolation(),
        },
        "independent_reviewer": None,
        "independent_approval": None,
        "network_segmentation_verified": False,
        "runtime_boundary_verified": False,
        "production_security_certified": False,
        "control_acceptance": STATUS,
        "production_deployment": "NO_GO",
        "broker_order_routing": "NOT_AUTHORIZED",
        "live_trading": "NOT_AUTHORIZED",
        "interpretation": (
            "Partial, untrusted local observations only. H01 requires signed actual "
            "host identity, separate network/privilege-denial receipts and review."
        ),
    }


def write_new_private_file(path: Path, value: dict[str, Any]) -> None:
    """Exclusively create a private regular file. Refuse symlink and overwrite."""
    if not path.is_absolute():
        raise ValueError("output must be an absolute path")
    if not path.parent.is_dir():
        raise ValueError("output parent directory must exist")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            fd = -1
            json.dump(value, out, indent=2, sort_keys=True)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
    except BaseException:
        if fd >= 0:
            os.close(fd)
        try:
            path.unlink()
        except OSError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Non-certifying read-only H01 host observation")
    parser.add_argument("--environment-id", required=True)
    parser.add_argument("--authorization-ref", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--approved-readonly-observation", action="store_true", required=True)
    args = parser.parse_args(argv)

    if os.geteuid() == 0:
        parser.error("refusing privileged/root execution; use approved unprivileged operator")
    if not args.approved_readonly_observation:
        parser.error("explicit approval acknowledgment required")
    try:
        report = build_observation(args.environment_id, args.authorization_ref)
        write_new_private_file(args.output, report)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(
        "H01 local observation written privately. "
        "STATUS: NOT_VERIFIED; PRODUCTION NO-GO; LIVE TRADING NOT AUTHORIZED."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
