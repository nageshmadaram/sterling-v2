"""Offline-only Python/Flow authoring and chart/scalp metadata contracts."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from app.api.v1.endpoints import platform_surfaces as endpoint
from app.core.auth import UserContext, get_current_user
from app.core import runtime_env
from app.services.platform_authoring_contracts import (
    AuthoringRejected, authoring_capabilities, validate_flow_graph,
    validate_python_source,
)


def test_python_authoring_accepts_syntax_only_without_executing():
    source = "def on_bar(bar):\n    return {'alert': bar['close'] > 100}\n"
    result = validate_python_source(source)
    assert result["syntax_valid"]
    assert not result["execution_enabled"]
    assert not result["runtime_sandbox_certified"]


@pytest.mark.parametrize("source", [
    "import os\ndef on_bar(bar): return os.system('echo wrong')",
    "def on_bar(bar): return open('/tmp/secret').read()",
    "def on_bar(bar): return __import__('os')",
    "def on_bar(bar): return bar.__class__",
    "def on_bar(bar):\n    while True: pass\n    return None",
    "def on_bar(bar):\n    for x in bar: pass\n    return None",
    "def on_bar(bar): return [x for x in bar]",
    "def other(bar): return None",
    "def on_bar(bar, broker): return None",
    "def on_bar(bar): pass",
    "def on_bar(bar): return 1\non_bar({})",
    "def on_bar(bar): return 0\n" + "# pad\n" * 4000,
])
def test_python_source_is_rejected_when_unbounded_or_executable(source):
    with pytest.raises(AuthoringRejected):
        validate_python_source(source)


def valid_graph():
    return {
        "nodes": [{"id": "source1", "kind": "source"},
                  {"id": "threshold1", "kind": "compare"},
                  {"id": "note1", "kind": "note"}],
        "edges": [{"from": "source1", "to": "threshold1"},
                  {"from": "threshold1", "to": "note1"}],
    }


def test_flow_graph_checks_declarative_topology_but_does_not_execute():
    result = validate_flow_graph(valid_graph())
    assert result == {
        "schema_valid": True, "execution_enabled": False,
        "order_execution_authorized": False, "node_count": 3, "edge_count": 2,
    }


@pytest.mark.parametrize("mutate", [
    lambda g: g["nodes"].append({"id": "buy1", "kind": "place_order"}),
    lambda g: g["nodes"].append({"id": "source1", "kind": "source"}),
    lambda g: g["nodes"].append({"id": "orphan", "kind": "compare"}),
    lambda g: g["edges"].append({"from": "note1", "to": "source1"}),
    lambda g: g["edges"].append({"from": "note1", "to": "threshold1"}),
    lambda g: g["edges"].append({"from": "threshold1", "to": "source1"}),
    lambda g: g["nodes"][0].update({"broker_token": "synthetic"}),
    lambda g: g["edges"].append({"from": "source1", "to": "threshold1"}),
    lambda g: g.update({"execute": True}),
])
def test_flow_rejects_orders_unknown_nodes_or_bad_edges(mutate):
    graph = valid_graph()
    mutate(graph)
    with pytest.raises(AuthoringRejected):
        validate_flow_graph(graph)


def test_flow_bounds_and_cycles_fail_closed():
    with pytest.raises(AuthoringRejected):
        validate_flow_graph({"nodes": [{"id": f"n{i}", "kind": "source"}
                                      for i in range(33)], "edges": []})
    with pytest.raises(AuthoringRejected):
        validate_flow_graph({
            "nodes": [{"id": "source", "kind": "source"},
                      {"id": "a", "kind": "compare"}, {"id": "b", "kind": "compare"}],
            "edges": [{"from": "source", "to": "a"}, {"from": "a", "to": "b"},
                      {"from": "b", "to": "a"}],
        })


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(endpoint.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: UserContext(user_id="owner", role="viewer")
    monkeypatch.setattr(endpoint, "require_feature", lambda *_: None)
    with TestClient(app) as c:
        yield c


def test_authoring_capabilities_are_read_only_and_unmounted(client):
    response = client.get("/api/v1/platform/authoring/capabilities")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    data = response.json()
    assert data == authoring_capabilities()
    assert data["python"]["execute_enabled"] is False
    assert data["flow"]["execute_enabled"] is False
    assert data["scalping"]["new_order_controls_enabled"] is False
    assert client.post("/api/v1/platform/authoring/capabilities").status_code == 405
    for path in ("/python", "/flow", "/trading", "/scalping"):
        assert client.get(path).status_code == 404


def test_authoring_capabilities_require_kite_grant_and_production_auth(client, monkeypatch):
    monkeypatch.setattr(endpoint, "require_feature",
                        lambda *_: (_ for _ in ()).throw(HTTPException(403)))
    assert client.get("/api/v1/platform/authoring/capabilities").status_code == 403
    monkeypatch.setattr(runtime_env, "is_production", lambda: True)
    client.app.dependency_overrides.clear()
    assert client.get("/api/v1/platform/authoring/capabilities").status_code == 401
