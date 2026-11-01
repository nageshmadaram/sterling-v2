"""Offline-only Python draft and Flow topology validation foundations.

These functions are **not a Python host, sandbox or Flow executor**. They do
not import/run draft code, persist it, interpolate it into a strategy, contact
a broker or instantiate a new execution engine. No API route is mounted.
Source syntax validity does not establish safety for subsequent execution.
"""
from __future__ import annotations

import ast
import hashlib
import heapq
import re
from collections.abc import Mapping

MAX_SOURCE_BYTES = 65536
MAX_AST_NODES = 4096
MAX_FLOW_NODES = 64
MAX_FLOW_EDGES = 256
_NODE_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,39}\Z", re.ASCII)


def review_python_draft(source: str) -> dict:
    """Bounded syntax inspection only; never evaluate or retain source text."""
    if not isinstance(source, str):
        raise ValueError("python draft must be text")
    try:
        raw = source.encode("utf-8", errors="strict")
    except UnicodeError:
        raise ValueError("python draft encoding is invalid") from None
    if not raw or not source.strip():
        raise ValueError("python draft cannot be empty")
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("python draft exceeds source limit")
    digest = hashlib.sha256(raw).hexdigest()
    try:
        tree = ast.parse(source, mode="exec")
    except (SyntaxError, ValueError, RecursionError):
        return {
            "syntax_valid": False,
            "source_sha256": digest,
            "execution_authorized": False,
            "sandbox_available": False,
        }
    # A large/deep AST must not be treated as a reviewed strategy. The built-in
    # parser performs syntax checking only; do not compile the tree into code.
    count = 0
    try:
        for _ in ast.walk(tree):
            count += 1
            if count > MAX_AST_NODES:
                raise ValueError("python draft exceeds AST limit")
    except RecursionError:
        raise ValueError("python draft exceeds AST depth") from None
    return {
        "syntax_valid": True,
        "source_sha256": digest,
        "execution_authorized": False,
        "sandbox_available": False,
    }


def validate_flow_topology(graph: Mapping) -> dict:
    """Check a bounded, inert DAG with no action/strategy/broker node schema.

    Node objects may contain ONLY an id, and edges ONLY source/target; no
    fields capable of expressing execution are admitted. This provides graph
    identity/topology validation, not a Flow interpretation or dry run.
    """
    if not isinstance(graph, Mapping) or set(graph) != {"nodes", "edges"}:
        raise ValueError("flow requires only nodes and edges")
    nodes, edges = graph["nodes"], graph["edges"]
    if (not isinstance(nodes, list) or not 1 <= len(nodes) <= MAX_FLOW_NODES
            or not isinstance(edges, list) or len(edges) > MAX_FLOW_EDGES):
        raise ValueError("flow exceeds structural bounds or is empty")
    ids = []
    for node in nodes:
        if not isinstance(node, dict) or set(node) != {"id"}:
            raise ValueError("flow nodes accept only id")
        ident = node["id"]
        if not isinstance(ident, str) or not _NODE_ID.fullmatch(ident):
            raise ValueError("invalid flow node identifier")
        ids.append(ident)
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate flow node identifier")

    adjacency = {ident: set() for ident in ids}
    indegree = {ident: 0 for ident in ids}
    for edge in edges:
        if not isinstance(edge, dict) or set(edge) != {"source", "target"}:
            raise ValueError("flow edges accept only source/target")
        source, target = edge["source"], edge["target"]
        if not isinstance(source, str) or not isinstance(target, str):
            raise ValueError("flow edge ids must be strings")
        if source not in adjacency or target not in adjacency or source == target:
            raise ValueError("invalid flow edge reference")
        if target in adjacency[source]:
            raise ValueError("duplicate flow edge")
        adjacency[source].add(target)
        indegree[target] += 1

    ready = [ident for ident in ids if indegree[ident] == 0]
    heapq.heapify(ready)
    order = []
    while ready:
        current = heapq.heappop(ready)
        order.append(current)
        for nxt in sorted(adjacency[current]):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                heapq.heappush(ready, nxt)
    if len(order) != len(ids):
        raise ValueError("flow cycle detected")
    return {
        "topological_order": order,
        "node_count": len(ids),
        "edge_count": len(edges),
        "execution_authorized": False,
        "graph_executor_available": False,
    }
