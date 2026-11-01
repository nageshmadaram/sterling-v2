"""Python draft/Flow topology foundations do not execute or contact brokers."""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

from app.services.platform_offline_authoring import (
    MAX_AST_NODES,
    MAX_FLOW_EDGES,
    MAX_FLOW_NODES,
    MAX_SOURCE_BYTES,
    review_python_draft,
    validate_flow_topology,
)


def test_syntax_only_never_executes_or_echoes_code():
    source = "import os\nraise RuntimeError('must never run')\n"
    result = review_python_draft(source)
    assert result == {
        "syntax_valid": True,
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "execution_authorized": False,
        "sandbox_available": False,
    }
    assert source not in repr(result)


def test_invalid_draft_does_not_claim_permission():
    result = review_python_draft("if (:\n    pass")
    assert result["syntax_valid"] is False
    assert not result["execution_authorized"]
    assert not result["sandbox_available"]


@pytest.mark.parametrize("source", ["", "   ", None, 123, "\ud800"])
def test_invalid_or_non_utf8_source_refused(source):
    with pytest.raises(ValueError):
        review_python_draft(source)


def test_python_source_size_and_ast_complexity_are_bounded():
    with pytest.raises(ValueError, match="source limit"):
        review_python_draft("x" * (MAX_SOURCE_BYTES + 1))
    many_nodes = "x=1\n" * (MAX_AST_NODES // 2)
    with pytest.raises(ValueError, match="AST limit"):
        review_python_draft(many_nodes)


def test_valid_flow_dag_deterministically_sorted_without_execution():
    graph = {
        "nodes": [{"id": "c"}, {"id": "b"}, {"id": "a"}],
        "edges": [{"source": "a", "target": "c"}, {"source": "b", "target": "c"}],
    }
    result = validate_flow_topology(graph)
    assert result == {
        "topological_order": ["a", "b", "c"], "node_count": 3,
        "edge_count": 2, "execution_authorized": False,
        "graph_executor_available": False,
    }
    assert graph["nodes"][0]["id"] == "c"  # no mutation


@pytest.mark.parametrize("graph", [
    None,
    {},
    {"nodes": [], "edges": []},
    {"nodes": [{"id": "a"}], "edges": [], "actions": []},
    {"nodes": [{"id": "a", "action": "placeorder"}], "edges": []},
    {"nodes": [{"id": "a"}, {"id": "a"}], "edges": []},
    {"nodes": [{"id": "a"}], "edges": [{"source": "a", "target": "a"}]},
    {"nodes": [{"id": "a"}], "edges": [{"source": "a", "target": "missing"}]},
    {"nodes": [{"id": "a"}, {"id": "b"}], "edges": [{"source": "a", "target": "b", "method": "POST"}]},
    {"nodes": [{"id": "a"}, {"id": "b"}], "edges": [{"source": "a", "target": "b"}, {"source": "a", "target": "b"}]},
    {"nodes": [{"id": "a"}, {"id": "b"}], "edges": [{"source": "a", "target": "b"}, {"source": "b", "target": "a"}]},
    {"nodes": [{"id": "a"}], "edges": "no"},
    {"nodes": [{"id": "/orders"}], "edges": []},
])
def test_flow_refuses_invalid_graphs_and_order_actions(graph):
    with pytest.raises(ValueError):
        validate_flow_topology(graph)


def test_graph_limits_enforced_before_traversal():
    with pytest.raises(ValueError, match="bounds"):
        validate_flow_topology({
            "nodes": [{"id": f"n{i}"} for i in range(MAX_FLOW_NODES + 1)],
            "edges": [],
        })
    with pytest.raises(ValueError, match="bounds"):
        validate_flow_topology({
            "nodes": [{"id": "a"}],
            "edges": [{"source": "a", "target": "a"}] * (MAX_FLOW_EDGES + 1),
        })


def test_offline_authoring_imports_no_broker_or_execution_modules():
    source = (
        Path(__file__).resolve().parents[2] / "app/services/platform_offline_authoring.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    assert set(imported) <= {
        "__future__", "ast", "hashlib", "heapq", "re", "collections.abc",
    }
    assert all(not isinstance(node, ast.Call) or not (
        isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec", "compile"}
    ) for node in ast.walk(tree))
