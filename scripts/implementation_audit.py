#!/usr/bin/env python3
"""Read-only source/host baseline and explicit acceptance-evidence recording.

Baseline never imports the application, loads .env, opens a broker connection,
or writes operator state. Its only writes are to the requested output directory.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True, timeout=30).strip()


def baseline(output: Path) -> dict:
    # Never overwrite a prior snapshot, even if the caller reuses a directory.
    output.mkdir(parents=True, exist_ok=False)
    sha = git("rev-parse", "HEAD")
    tracked = subprocess.check_output(["git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard", "-z"], timeout=30).split(b"\0")
    digest = hashlib.sha256()
    for name in sorted(set(filter(None, tracked))):
        path = ROOT / os.fsdecode(name)
        digest.update(name + b"\0")
        if path.is_symlink():
            digest.update(os.readlink(path).encode())
        elif path.is_file():
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(65536), b""):
                    digest.update(chunk)
        else:
            digest.update(b"MISSING")
        digest.update(b"\0")
    status = git("status", "--porcelain=v1", "--untracked-files=normal")
    # The output is evidence, not an input source change. Report pre-existing
    # dirtiness independently of the newly created snapshot directory.
    disk = shutil.disk_usage(ROOT)
    issue_file = ROOT / "docs/operations/ISSUE_REGISTER_151_20261018.csv"
    issues = []
    if issue_file.is_file():
        with issue_file.open(newline="") as handle:
            issues = list(csv.DictReader(line for line in handle if line.strip() and not line.startswith("#")))
    manifest_path = ROOT / "docs/operations/implementation-2026-10-26.json"
    requirements = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    result = {
        "schema_version": 1, "observed_at": datetime.now(timezone.utc).isoformat(),
        "observation_scope": "LOCAL_CHECKOUT_AND_HOST_ONLY",
        "source": {"sha": sha, "branch": git("branch", "--show-current"),
                   "clean": not bool(status), "working_tree_sha256": digest.hexdigest(),
                   "status": status.splitlines(),
                   "upstream_divergence": None},
        "host": {"disk_total_bytes": disk.total, "disk_free_bytes": disk.free,
                 "cpu_count": os.cpu_count(), "production_host_verified": False},
        "runtime": {"serving_sha": "UNKNOWN", "live_entries": "UNKNOWN", "safe_mode": "UNKNOWN",
                    "broker_exposure": "UNKNOWN", "binding": "UNKNOWN",
                    "static_ip_console_approval": "UNKNOWN", "telegram_device_receipt": "UNKNOWN",
                    "off_host_restore": "UNKNOWN"},
        "historical_issue_count": len(issues),
        "historical_issues_are_current_acceptance": False,
        "requirements": requirements,
        "gates": {f"G{i}": "UNKNOWN" for i in range(10)},
        "r1": "NOT_CERTIFIED", "r2": "BLOCKED", "r3": "UNSUPPORTED",
        "new_live_entries_authorized": False,
    }
    try:
        result["source"]["upstream_divergence"] = git("rev-list", "--left-right", "--count", "HEAD...@{upstream}")
    except subprocess.CalledProcessError:
        pass
    (output / "baseline.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    lines = ["# Blocking gates", "", f"Observed source: `{sha}`.",
             "Scope: local checkout and host; no broker or production instance inspected.", "",
             "R1: not certified. R2: blocked. R3: unsupported. New live entries are not authorized.", "",
             "| Gate | State | Required evidence |", "|---|---|---|"]
    descriptions = ["Clean immutable source and dependency provenance", "Executed current-SHA CI jobs, nonzero runner and steps",
                    "Measured production host SLO and storage", "Owner-approved static IP, callback, binding and fresh broker inventory",
                    "Lifecycle, risk and protection acceptance", "Independent encrypted off-host restore and failure drills",
                    "Telegram receipt on operator device", "Independent per-mode after-cost OOS and forward evidence",
                    "Independent operator handoff", "Separately authorized broker order, protection and exit receipts"]
    lines.extend(f"| G{i} | UNKNOWN | {detail} |" for i, detail in enumerate(descriptions))
    lines += ["", "Local tests are recorded separately and cannot become CI, broker or human acceptance.",
              "Code present in a source file does not establish that its acceptance criterion passed."]
    (output / "blocking-gates.md").write_text("\n".join(lines) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    base = sub.add_parser("baseline")
    base.add_argument("--output", required=True, type=Path)
    record = sub.add_parser("record", help="append a reviewed claim; never enables live entries")
    record.add_argument("claim", type=Path)
    record.add_argument("--register", required=True, type=Path)
    report = sub.add_parser("report", help="verify register integrity; print claim identities and results")
    report.add_argument("--register", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "baseline":
        result = baseline(args.output)
        print(json.dumps({"output": str(args.output.resolve()), "source_sha": result["source"]["sha"],
                          "r1": result["r1"], "r2": result["r2"], "r3": result["r3"]}))
    else:
        sys.path.insert(0, str(ROOT / "backend"))
        from app.core.acceptance_evidence import AcceptanceClaim, AcceptanceRegister, gate_status
        store = AcceptanceRegister(args.register)
        if args.command == "record":
            claim = AcceptanceClaim(**json.loads(args.claim.read_text()))
            print(json.dumps({"event_hash": store.append(claim), "claim_id": claim.claim_id}))
        else:
            print(json.dumps([{"claim_id": c.claim_id, "source_sha": c.source_sha,
                               "result": store.status(c), "observed_at": c.observed_at} for c in store.read()], indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print(f"audit failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
