#!/usr/bin/env python3
"""Read-only pre-production source inventory. Never connects to a broker or database.

Output is a reproducible observation, NOT execution-authority approval.
Static AST evidence is intentionally not presented as runtime or production proof.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys

ENTRY_METHODS = frozenset({"place_order", "place_order_option", "place_order_future"})
OTHER_MUTATIONS = frozenset({
    "cancel_order", "modify_order", "place_gtt", "modify_gtt", "cancel_gtt",
    "place_mf_order", "cancel_mf_order", "modify_mf_order", "convert_position",
})
METHODS = ENTRY_METHODS | OTHER_MUTATIONS
ALLOW_ENTRY = frozenset({
    "backend/app/services/execution_service.py",
    "backend/app/services/exchanges/kite/client.py",
    "backend/app/services/simulation/sim_client.py",
})
CATEGORIES = (
    ("account", ("account", "profile", "identity")),
    ("workspace", ("workspace", "feature_access")),
    ("broker", ("broker", "kite", "exchange")),
    ("admin", ("admin", "configuration", "user")),
    ("execution", ("execution", "order", "trading", "risk")),
    ("session", ("session", "auth", "token")),
    ("lifecycle", ("lifecycle", "safe_mode", "recovery")),
)


def _locate(root: Path):
    for directory in (root / "backend/app", root / "backend/scripts"):
        if directory.is_dir():
            yield from sorted(directory.rglob("*.py"))
    scripts = root / "scripts"
    if scripts.is_dir():
        yield from sorted(scripts.glob("*.py"))


def _kind(path: str) -> str:
    if path == "backend/app/services/execution_service.py":
        return "CANONICAL_SERVICE"
    if path == "backend/app/services/exchanges/kite/client.py":
        return "TRANSPORT"
    if path == "backend/app/services/simulation/sim_client.py":
        return "SIMULATION"
    return "REQUIRES_REVIEW"


def _literal(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return "DYNAMIC"


def _route_declarations(root: Path, path: Path, tree: ast.AST) -> list[dict]:
    if not path.is_relative_to(root / "backend/app/api"):
        return []
    results = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            if not (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute)):
                continue
            method = deco.func.attr.upper()
            if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "API_ROUTE"}:
                continue
            if not (isinstance(deco.func.value, ast.Name) and deco.func.value.id == "router"):
                continue
            value = _literal(deco.args[0]) if deco.args else "DYNAMIC"
            rel = path.relative_to(root).as_posix()
            name = node.name.lower()
            categories = [category for category, terms in CATEGORIES
                          if any(term in (rel + " " + name).lower() for term in terms)]
            results.append({
                "method": method, "path_fragment": value, "source": rel,
                "line": deco.lineno, "handler": node.name,
                "categories": categories or ["other"],
                "effective_authorization": "REVIEW_REQUIRED",
                "owner": "UNASSIGNED",
            })
    return results


def inventory(root: Path) -> dict:
    root = root.resolve()
    if not (root / "backend/app").is_dir():
        raise ValueError("Expected backend/app directory; refusing an empty inventory.")
    methods, routes, errors = [], [], []
    scanned = 0
    for path in _locate(root):
        rel = path.relative_to(root).as_posix()
        scanned += 1
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=rel)
        except (SyntaxError, UnicodeError, OSError) as exc:
            errors.append({"source": rel, "error_type": type(exc).__name__})
            continue
        routes.extend(_route_declarations(root, path, tree))
        classification = _kind(rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                method = node.func.attr
                if method in METHODS:
                    methods.append({"source": rel, "line": node.lineno,
                                    "method": method, "kind": "CALL",
                                    "boundary": classification})
            # Method references can be rebound and called under an arbitrary
            # identifier. A syntax-only check must report those aliases too.
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                value = node.value
                if isinstance(value, ast.Attribute) and value.attr in METHODS:
                    methods.append({"source": rel, "line": node.lineno,
                                    "method": value.attr, "kind": "ALIAS",
                                    "boundary": classification})
    methods.sort(key=lambda row: (row["source"], row["line"], row["method"], row["kind"]))
    routes.sort(key=lambda row: (row["source"], row["line"], row["method"]))
    prohibited = [row for row in methods
                  if row["method"] in ENTRY_METHODS and row["source"] not in ALLOW_ENTRY]
    # A call to a similarly-named method is not proof of broker access.
    # Static observations are marked POTENTIAL rather than asserted direct sends.
    return {
        "schema_version": 1,
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "SOURCE_AST_ONLY_NO_RUNTIME_OR_BROKER_VERIFICATION",
        "production_decision": "NO_GO",
        "live_broker_authorized": False,
        "scanned_python_files": scanned,
        "routes": routes,
        "broker_method_references": methods,
        "unapproved_order_method_references": prohibited,
        "parsing_errors": errors,
        "full_runtime_execution_route_coverage": "UNKNOWN",
        "route_to_effective_policy_verified": False,
        "approval_evidence_verified": False,
    }


def markdown(report: dict) -> str:
    lines = [
        "# Sterling V2: pre-production source inventory",
        "",
        "This is a static discovery artifact, NOT a validated runtime route map.",
        "**Production NO-GO; live broker execution NOT AUTHORIZED.**",
        "",
        f"Python files scanned: {report['scanned_python_files']}. "
        f"API decorators discovered: {len(report['routes'])}. "
        f"Broker-method references: {len(report['broker_method_references'])}.",
        f"Unapproved ordinary order references: {len(report['unapproved_order_method_references'])}. "
        f"Parse failures: {len(report['parsing_errors'])}.",
        "",
        "## Potential broker mutations (inspect owner, authority, journal and lifecycle)",
        "",
        "| Source:line | Method | Syntax | Boundary | Owner / validation |",
        "|---|---|---|---|---|",
    ]
    for row in report["broker_method_references"]:
        lines.append("| `{}:{}` | `{}` | {} | {} | UNASSIGNED / REVIEW_REQUIRED |".format(
            row["source"], row["line"], row["method"], row["kind"], row["boundary"]))
    lines += [
        "", "## Discovered backend API declarations", "",
        "| Method | Path fragment | Handler | File:line | Category | Status |",
        "|---|---|---|---|---|---|",
    ]
    for row in report["routes"]:
        lines.append("| {} | `{}` | `{}` | `{}:{}` | {} | REVIEW_REQUIRED |".format(
            row["method"], row["path_fragment"], row["handler"],
            row["source"], row["line"], ",".join(row["categories"])))
    lines += ["", "## Limitations", "",
              "- Decorator fragments do not include router include prefixes.",
              "- Indirect calls, dynamic imports, reflection, and third-party clients need human review.",
              "- Method name matches are potential mutations; a similarly named mock is not proof of a real broker send.",
              "- Empty unapproved list is not certification; actual runtime journal, identity and recovery proof are required.",
              "- File owners and approval gates require human assignment and independent evidence.",
              ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True,
                        help="New output directory; no existing files will be overwritten")
    parser.add_argument("--strict", action="store_true",
                        help="Fail if direct ordinary order calls/aliases or parse errors are found")
    args = parser.parse_args(argv)
    try:
        report = inventory(args.root)
        args.output.mkdir(parents=True, exist_ok=False)
        payload = json.dumps(report, sort_keys=True, indent=2) + "\n"
        (args.output / "inventory.json").write_text(payload, encoding="utf-8")
        (args.output / "inventory.md").write_text(markdown(report), encoding="utf-8")
        (args.output / "SHA256SUMS").write_text(
            sha256(payload.encode()).hexdigest() + "  inventory.json\n", encoding="utf-8")
        print(json.dumps({
            "status": "SOURCE_ONLY", "production": "NO_GO",
            "unapproved": len(report["unapproved_order_method_references"]),
            "parse_errors": len(report["parsing_errors"]),
            "parsing_error_files": report["parsing_errors"],
            "output": str(args.output),
        }))
        if args.strict and (report["unapproved_order_method_references"] or report["parsing_errors"]):
            return 1
        return 0
    except (ValueError, OSError) as exc:
        print(f"inventory failed: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
