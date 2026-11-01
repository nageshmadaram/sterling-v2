#!/usr/bin/env python3
"""Static, non-executing Sterling/OpenAlgo API route compatibility inventory.

Never imports the application, loads credentials, opens a broker, or registers
any order route. Reports decorator declarations, not actual mounted runtime
proof; use full HTTP tests for acceptance.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

METHODS = frozenset({"get", "post", "put", "patch", "delete", "head"})
# Grounded OpenAlgo API v1 routes from docs.openalgo.in (Oct 2026).
# These are compatibility targets, not permission to expose live writes.
TARGETS = {
    "POST /api/v1/placeorder": "POST /api/v1/kite/orders",
    "POST /api/v1/orderstatus": "POST /api/v1/compat/openalgo/orderstatus",
    "POST /api/v1/orderbook": "GET /api/v1/kite/orders",
    "POST /api/v1/cancelorder": "DELETE /api/v1/kite/orders/{order_id}",
}
READ_ONLY_ADAPTER = "POST /api/v1/compat/openalgo/orderstatus"


def _router_prefix(tree: ast.Module) -> str:
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        names = [x.id for x in node.targets if isinstance(x, ast.Name)] if isinstance(
            node, ast.Assign
        ) else [node.target.id] if isinstance(node.target, ast.Name) else []
        if "router" not in names or not isinstance(node.value, ast.Call):
            continue
        if not isinstance(node.value.func, ast.Name) or node.value.func.id != "APIRouter":
            continue
        for word in node.value.keywords:
            if word.arg == "prefix" and isinstance(word.value, ast.Constant):
                if isinstance(word.value.value, str):
                    return word.value.value
    return ""


def declared_routes(source: Path) -> list[str]:
    tree = ast.parse(source.read_text("utf-8"), filename=str(source))
    prefix = _router_prefix(tree)
    routes = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                continue
            receiver = dec.func.value
            if (not isinstance(receiver, ast.Name) or receiver.id != "router"
                    or dec.func.attr not in METHODS or not dec.args):
                continue
            arg = dec.args[0]
            if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
                raise ValueError("Dynamic API path requires manual compatibility review")
            route = "/api/v1" + prefix + arg.value
            routes.append(f"{dec.func.attr.upper()} {route}")
    return sorted(set(routes))


def report(endpoint_dir: Path) -> dict:
    root = Path(endpoint_dir)
    if not root.is_dir():
        raise ValueError("Endpoint directory does not exist")
    files = {
        "kite": root / "kite.py",
        "openalgo_compat": root / "openalgo_compat.py",
    }
    routes = {}
    for name, file in files.items():
        if not file.is_file():
            raise ValueError(f"Required Sterling route module missing: {name}")
        routes[name] = declared_routes(file)
    all_routes = set().union(*(set(x) for x in routes.values()))
    deltas = []
    for target, existing in TARGETS.items():
        found = existing in all_routes
        deltas.append({
            "openalgo": target,
            "sterling": existing,
            "sterling_route_declared": found,
            "status": ("JWT_READ_ONLY_ADAPTER_SOURCE_PRESENT"
                       if existing == READ_ONLY_ADAPTER and found
                       else "NO_COMPATIBILITY_WRAPPER"),
        })
    return {
        "status": "COMPATIBILITY_INCOMPLETE",
        "mode": "STATIC_SOURCE_ONLY",
        "routes": routes,
        "openalgo_route_delta": deltas,
        "broker_connection": "NOT_ATTEMPTED",
        "production_certification": "NOT_ACHIEVED",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--endpoint-dir", type=Path,
        default=Path(__file__).resolve().parents[1] / "backend" / "app" / "api" / "v1" / "endpoints",
    )
    args = parser.parse_args()
    print(json.dumps(report(args.endpoint_dir), sort_keys=True))


if __name__ == "__main__":
    main()
