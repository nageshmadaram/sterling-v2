#!/usr/bin/env python3
"""Read-only Alembic rollback risk inventory. NEVER applies a migration.

Production rollback is not an automatic downgrade; an existing SQLAlchemy
baseline downgrade drops position, mirrored-record and equity tables.
Inspect each revision in review, preserve the durable journal, and use only
independently approved forward-repair and database restore procedures.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

DESTRUCTIVE = frozenset({"drop_table", "drop_column", "drop_constraint", "execute"})


def analyze_revision(path: Path) -> dict:
    tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    functions = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "downgrade"
    ]
    operations = []
    for fn in functions:
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "op"
                    and node.func.attr in DESTRUCTIVE):
                operations.append({
                    "operation": node.func.attr,
                    "line": node.lineno,
                    "target": (node.args[0].value
                               if node.args and isinstance(node.args[0], ast.Constant)
                               and isinstance(node.args[0].value, str) else "review_required"),
                })
    return {
        "revision_file": path.name,
        "downgrade_present": len(functions) == 1,
        "destructive_or_unreviewed_operations": operations,
    }


def audit(root: Path) -> dict:
    paths = sorted(p for p in root.glob("*.py") if not p.name.startswith("__"))
    if not paths:
        raise ValueError("No Alembic revisions found: refuse to infer rollback safety")
    revisions = [analyze_revision(path) for path in paths]
    blocked = any(
        not entry["downgrade_present"] or entry["destructive_or_unreviewed_operations"]
        for entry in revisions
    )
    return {
        "status": "BLOCKED_AUTOMATIC_DOWNGRADE" if blocked else "REVIEW_REQUIRED",
        "production_database_touched": False,
        "postgres_restore_proven": False,
        "automatic_rollback_authorized": False,
        "revisions": revisions,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--revisions",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "backend" / "alembic" / "versions",
    )
    parser.add_argument("--enforce", action="store_true",
                        help="Return nonzero if downgrade requires manual review")
    opts = parser.parse_args()
    report = audit(opts.revisions)
    print(json.dumps(report, sort_keys=True))
    if opts.enforce:
        raise SystemExit(2 if report["status"] != "REVIEW_REQUIRED" else 0)


if __name__ == "__main__":
    main()
