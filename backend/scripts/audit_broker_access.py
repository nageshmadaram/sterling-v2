"""Conservative, auditable broker-call inventory and no-new-direct-access gate.

Reads repository Python sources with AST. The pinned baseline is the last
validated read-only Phase 2A revision. All existing legacy exceptions are
*visible* in a JSON/Markdown inventory; NEW exceptions fail validation.

This is a source scanner, NOT a security sandbox or proof of runtime isolation.
Dynamic getattr, aliases, indirect imports, vendored JS and broker SDK internals
need a separate review.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "89eda42aff0f687ac7cadcdead3cc3d9f598c797"
ROOTS = ("backend/app/", "backend/study/", "backend/scripts/", "kitelake/")
READ_METHODS = frozenset({
    "get_profile", "get_margins", "get_positions", "get_positions_raw",
    "get_holdings", "get_orders", "get_open_orders", "get_trades", "get_fills",
    "get_order_history", "get_order_trades", "get_quote", "get_ltp", "get_ohlc",
    "get_historical", "get_candles", "get_option_chain", "get_balances",
    "get_portfolio_snapshot", "test_connection",
})
WRITE_METHODS = frozenset({
    "place_order", "place_order_option", "place_order_future",
    "modify_order", "cancel_order", "convert_position", "place_gtt",
    "modify_gtt", "delete_gtt", "market_reduce_close", "cancel_replace_stop",
    "emergency_square_off_all", "invalidate_session",
})
CTOR_NAMES = frozenset({"KiteClient", "ZerodhaAdapter", "KiteConnect", "KiteTicker"})
PROVIDER_DOMAINS = ("api.kite.trade", "kite.trade", "truedata.in")
REGISTRY_TABLES = ("platform_strategies", "platform_strategy_versions",
                   "platform_strategy_audit", "platform_strategy_runs")
_SQL_WRITE = ("UPDATE", "DELETE FROM", "INSERT INTO", "REPLACE INTO", "TRUNCATE", "ALTER TABLE")


def _files_current():
    for root in ROOTS:
        base = ROOT / root
        if base.is_dir():
            for p in sorted(base.rglob("*.py")):
                if "__pycache__" not in p.parts:
                    yield p.relative_to(ROOT).as_posix(), p.read_text(encoding="utf-8")


def _files_baseline(revision: str):
    names = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", revision, "--", *ROOTS],
        cwd=ROOT, text=True,
    ).splitlines()
    for path in names:
        if path.endswith(".py"):
            contents = subprocess.check_output(
                ["git", "show", f"{revision}:{path}"], cwd=ROOT,
            ).decode("utf-8")
            yield path, contents


def _read_boundary(path: str) -> bool:
    return path == "backend/app/services/exchanges/normalized_read.py"


def _transport_boundary(path: str) -> bool:
    return path.startswith("backend/app/services/exchanges/kite/")


def _classified(path: str, method: str, kind: str) -> tuple[str, str]:
    if _transport_boundary(path):
        return "approved_broker_transport", "approved boundary; provider network calls stay here"
    if _read_boundary(path):
        if method in READ_METHODS:
            return "approved_normalized_read_service", "approved observation-only contract"
        return "forbidden_direct_access", "normalized read facade cannot include broker writes"
    if path.startswith(("backend/app/services/exchanges/", "backend/app/services/providers/")):
        if kind == "provider_http":
            return "unknown_requires_review", "provider diagnostic or alternate provider transport"
        return "legacy_adapter_or_provider_exception", "review adapter/diagnostic transport ownership"
    if path.startswith("backend/app/services/simulation/"):
        return "legacy_simulation_exception", "verify simulation-only boundary and client isolation"
    if kind in {"sdk_constructor", "sdk_import", "provider_http"}:
        return "forbidden_direct_access", "raw SDK or provider transport outside adapter"
    if kind == "sql_write":
        return "forbidden_direct_access", "direct strategy registry SQL mutation outside service"
    if method in WRITE_METHODS:
        if path.startswith("backend/app/services/execution/"):
            return "legacy_execution_authority_exception", "existing order authority; safety review required"
        return "legacy_broker_write_exception", "existing exposure-changing path; urgent execution-authority review"
    if path.startswith(("kitelake/", "backend/study/", "backend/scripts/")):
        return "legacy_offline_ingestion_exception", "offline research/import path; no live admission"
    if path.startswith("backend/app/api/"):
        return "legacy_route_read_exception", "API endpoint delegates raw broker payload; normalize before rollout"
    return "legacy_read_exception", "unmigrated read; do not infer freshness or canonical shape"


def _functions(tree: ast.AST):
    """Walk functions and nested closures, yielding direct executable call sites."""
    stack: list[str] = []
    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.out = []
        def visit_FunctionDef(self, node):
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()
        visit_AsyncFunctionDef = visit_FunctionDef
        def visit_ClassDef(self, node):
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()
        def visit_Call(self, node):
            self.out.append((tuple(stack) or ("<module>",), node))
            self.generic_visit(node)
        def visit_Import(self, node):
            for alias in node.names:
                if alias.name == "kiteconnect":
                    self.out.append((tuple(stack) or ("<module>",), node))
        def visit_ImportFrom(self, node):
            if (node.module or "").startswith("kiteconnect"):
                self.out.append((tuple(stack) or ("<module>",), node))
    visitor = Visitor()
    visitor.visit(tree)
    return visitor.out


def _line(source: str, node: ast.AST) -> str:
    lines = source.splitlines()
    num = getattr(node, "lineno", 1)
    return lines[num - 1].strip() if 1 <= num <= len(lines) else ""


def _event(path: str, source: str, qualname: tuple[str, ...], node: ast.AST):
    kind = method = None
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        kind, method = "sdk_import", "kiteconnect"
    elif isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute):
            method = func.attr
            if method in READ_METHODS | WRITE_METHODS:
                kind = "broker_read" if method in READ_METHODS else "broker_write"
            elif method in CTOR_NAMES:
                kind = "sdk_constructor"
            else:
                method = None
        elif isinstance(func, ast.Name) and func.id in CTOR_NAMES:
            method, kind = func.id, "sdk_constructor"
        if method is None and isinstance(func, ast.Attribute):
            # A provider URL plus httpx call in a function is audited below
            # at the URL literal site; no ambient method magic.
            pass
    if kind is None:
        return None
    classification, reason = _classified(path, method, kind)
    # Normalize whitespace, preserving the exact executable call expression
    # as a baseline fingerprint. Repeated occurrences are count-compared.
    expression = ast.unparse(node)
    fingerprint = hashlib.sha256(expression.encode("utf-8")).hexdigest()[:16]
    return {
        "file": path, "function": ".".join(qualname),
        "line": getattr(node, "lineno", 0), "method": method,
        "kind": kind, "classification": classification,
        "risk": ("high" if classification in {"forbidden_direct_access", "legacy_broker_write_exception"}
                 else "review" if "exception" in classification or classification == "unknown_requires_review"
                 else "controlled"),
        "reason": reason, "expression_hash": fingerprint,
    }


def scan(files):
    found = []
    for path, source in files:
        if not path.endswith(".py"):
            continue
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as exc:
            raise ValueError(f"cannot audit {path}: syntax error {exc.lineno}") from exc
        for qualname, node in _functions(tree):
            event = _event(path, source, qualname, node)
            if event:
                found.append(event)
        # Detect hard-coded vendor transport usage (not just SDK method names).
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            value = node.value
            if any(host in value.lower() for host in PROVIDER_DOMAINS):
                # Some module docstrings mention a vendor URL. Record only
                # statements with URL-like values, not incidental prose.
                if value.lower().startswith(("https://", "http://")):
                    classification, reason = _classified(path, "provider_url", "provider_http")
                    found.append({
                        "file": path, "function": "<literal>", "line": node.lineno,
                        "method": "provider_url", "kind": "provider_http",
                        "classification": classification, "reason": reason,
                        "risk": "high" if classification == "forbidden_direct_access" else "review",
                        "expression_hash": hashlib.sha256(value.encode()).hexdigest()[:16],
                    })
            upper = value.strip().upper()
            if any(upper.startswith(op) for op in _SQL_WRITE) and any(tbl.upper() in upper for tbl in REGISTRY_TABLES):
                classification, reason = _classified(path, "registry_sql", "sql_write")
                found.append({
                    "file": path, "function": "<literal>", "line": node.lineno,
                    "method": "registry_sql", "kind": "sql_write",
                    "classification": classification, "reason": reason,
                    "risk": "high", "expression_hash": hashlib.sha256(value.encode()).hexdigest()[:16],
                })
    return sorted(found, key=lambda x: (x["file"], x["line"], x["method"]))


def _identity(event):
    return (event["file"], event["function"], event["method"], event["kind"],
            event["expression_hash"])


def new_unreviewed(before, after):
    allowed = Counter(_identity(x) for x in before)
    seen = Counter()
    unexpected = []
    for item in after:
        key = _identity(item)
        seen[key] += 1
        if seen[key] > allowed[key] and item["classification"] not in {
            "approved_broker_transport", "approved_normalized_read_service",
        }:
            unexpected.append(item)
    return unexpected


def report(events, unreviewed, baseline: str):
    counts = dict(Counter(item["classification"] for item in events))
    return {"schema": 1, "baseline_sha": baseline, "counts": counts,
            "total": len(events), "unreviewed_new": unreviewed,
            "findings": events}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", default=BASELINE)
    parser.add_argument("--json", default="")
    parser.add_argument("--markdown", default="")
    args = parser.parse_args(argv)
    current = scan(_files_current())
    prior = scan(_files_baseline(args.baseline))
    unexpected = new_unreviewed(prior, current)
    result = report(current, unexpected, args.baseline)
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    if args.markdown:
        rows = ["# Sterling broker access inventory", "",
                f"Baseline: \`{args.baseline}\`", "",
                f"Findings: {len(current)}; new unreviewed: {len(unexpected)}.", "",
                "| File | Function | Line | Method | Classification | Risk |",
                "|---|---|---:|---|---|---|"]
        for e in current:
            rows.append(f"| \`{e['file']}\` | \`{e['function']}\` | {e['line']} | "
                        f"\`{e['method']}\` | {e['classification']} | {e['risk']} |")
        Path(args.markdown).write_text("\n".join(rows) + "\n")
    print(json.dumps({"total": len(current), "counts": result["counts"],
                      "new_unreviewed": len(unexpected)}, sort_keys=True))
    for e in unexpected[:30]:
        print("BLOCKED:", json.dumps(e, sort_keys=True))
    return 1 if unexpected else 0


if __name__ == "__main__":
    sys.exit(main())
