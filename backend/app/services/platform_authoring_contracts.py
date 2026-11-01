"""Offline authoring *validation* contracts, not execution sandboxes.

No exec/eval/compile/subprocess, imports of user code, broker clients, feed
subscriptions, graph interpreter or state persistence. Passing validation
must NEVER be treated as a safety guarantee for executing submitted Python.
"""
from __future__ import annotations

import ast
import re
from typing import Any

MAX_PYTHON_BYTES = 8192
MAX_PYTHON_AST_NODES = 256
MAX_FLOW_NODES = 32
MAX_FLOW_EDGES = 64
FLOW_NODE_KINDS = frozenset({"source", "compare", "note"})
_IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")

# Syntax-only subset. This is a useful lint check, NOT an isolated runtime.
# Reject calls/attributes/imports/loops/comprehensions, including broker APIs.
_ALLOWED_PYTHON_NODES = (
    ast.Module, ast.FunctionDef, ast.arguments, ast.arg,
    ast.Return, ast.Assign, ast.If, ast.Pass,
    ast.Name, ast.Load, ast.Store, ast.Constant,
    ast.Dict, ast.List, ast.Tuple, ast.Subscript,
    ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.USub, ast.UAdd,
    ast.Not, ast.And, ast.Or, ast.Eq, ast.NotEq,
    ast.Lt, ast.LtE, ast.Gt, ast.GtE,
)


class AuthoringRejected(ValueError):
    """Invalid source manifest or flow graph; no execution has occurred."""


def validate_python_source(source: str) -> dict[str, Any]:
    """Parse one on_bar(bar) declaration; never load or run the source."""
    if not isinstance(source, str):
        raise AuthoringRejected("python_source_type_invalid")
    try:
        source_size = len(source.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise AuthoringRejected("python_source_encoding_invalid") from exc
    if not 0 < source_size <= MAX_PYTHON_BYTES:
        raise AuthoringRejected("python_source_size_invalid")
    if "\x00" in source:
        raise AuthoringRejected("python_source_nul_refused")
    try:
        tree = ast.parse(source, mode="exec")
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        raise AuthoringRejected("python_source_syntax_invalid") from exc
    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_PYTHON_AST_NODES:
        raise AuthoringRejected("python_source_ast_limit")
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        raise AuthoringRejected("single_on_bar_function_required")
    function = tree.body[0]
    args = function.args
    if (function.name != "on_bar" or function.decorator_list
            or function.returns is not None or args.posonlyargs
            or len(args.args) != 1 or args.args[0].arg != "bar"
            or args.args[0].annotation is not None or args.vararg is not None
            or args.kwonlyargs or args.kw_defaults or args.kwarg is not None
            or args.defaults):
        raise AuthoringRejected("on_bar_signature_invalid")
    for node in nodes:
        if not isinstance(node, _ALLOWED_PYTHON_NODES):
            raise AuthoringRejected("python_node_not_allowed")
        if isinstance(node, ast.Name) and (
            node.id.startswith("_") or not node.id.isidentifier()
        ):
            raise AuthoringRejected("python_name_not_allowed")
    if not any(isinstance(node, ast.Return) for node in ast.walk(function)):
        raise AuthoringRejected("on_bar_return_required")
    return {
        "syntax_valid": True,
        "execution_enabled": False,
        "runtime_sandbox_certified": False,
        "function": "on_bar",
        "ast_nodes": len(nodes),
    }


def validate_flow_graph(graph: object) -> dict[str, Any]:
    """Validate a small DAG against a fixed inert node vocabulary.

    This does not evaluate nodes or register data sources. Unknown keys and
    actions (including buy/sell/order) fail closed, not silently omitted.
    """
    if not isinstance(graph, dict) or set(graph) != {"nodes", "edges"}:
        raise AuthoringRejected("flow_schema_invalid")
    nodes, edges = graph["nodes"], graph["edges"]
    if (not isinstance(nodes, list) or not 1 <= len(nodes) <= MAX_FLOW_NODES
            or not isinstance(edges, list) or len(edges) > MAX_FLOW_EDGES):
        raise AuthoringRejected("flow_bounds_invalid")
    kinds: dict[str, str] = {}
    for item in nodes:
        if not isinstance(item, dict) or set(item) != {"id", "kind"}:
            raise AuthoringRejected("flow_node_schema_invalid")
        ident, kind = item["id"], item["kind"]
        if (not isinstance(ident, str) or not _IDENT.fullmatch(ident)
                or not isinstance(kind, str) or kind not in FLOW_NODE_KINDS
                or ident in kinds):
            raise AuthoringRejected("flow_node_refused")
        kinds[ident] = kind
    if "source" not in kinds.values():
        raise AuthoringRejected("flow_source_required")

    children = {key: set() for key in kinds}
    incoming = {key: 0 for key in kinds}
    for edge in edges:
        if not isinstance(edge, dict) or set(edge) != {"from", "to"}:
            raise AuthoringRejected("flow_edge_schema_invalid")
        start, end = edge["from"], edge["to"]
        if (not isinstance(start, str) or not isinstance(end, str)
                or start not in kinds or end not in kinds or start == end
                or end in children[start] or kinds[start] == "note"
                or kinds[end] == "source"):
            raise AuthoringRejected("flow_edge_refused")
        children[start].add(end)
        incoming[end] += 1

    # Kahn's algorithm proves acyclicity and completeness with bounded effort.
    ready = [key for key, count in incoming.items() if count == 0]
    if any(kinds[key] != "source" for key in ready):
        raise AuthoringRejected("flow_orphan_node")
    visited = 0
    while ready:
        node = ready.pop()
        visited += 1
        for next_id in children[node]:
            incoming[next_id] -= 1
            if incoming[next_id] == 0:
                ready.append(next_id)
    if visited != len(kinds):
        raise AuthoringRejected("flow_cycle_refused")
    return {
        "schema_valid": True,
        "execution_enabled": False,
        "order_execution_authorized": False,
        "node_count": len(nodes),
        "edge_count": len(edges),
    }


def authoring_capabilities() -> dict[str, Any]:
    """Immutable contract targets; proposed global UI routes stay unmounted."""
    return {
        "version": "0.1.0",
        "python": {
            "source_validation_only": True,
            "execute_enabled": False,
            "sandbox_certified": False,
            "max_source_bytes": MAX_PYTHON_BYTES,
        },
        "flow": {
            "schema_validation_only": True,
            "execute_enabled": False,
            "node_kinds": sorted(FLOW_NODE_KINDS),
            "max_nodes": MAX_FLOW_NODES,
            "max_edges": MAX_FLOW_EDGES,
        },
        "charting": {
            "existing_data_route": "/api/v1/candles/{underlying}",
            "global_workspace_mounted": False,
        },
        "scalping": {
            "existing_strategy_modes_route": "/api/v1/strategies/{strategy}/modes",
            "global_workspace_mounted": False,
            "new_order_controls_enabled": False,
        },
    }
