"""Tools are exact pointers to existing reads, never extra order capability."""
from __future__ import annotations

import ast
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from app.api.v1.endpoints import platform_surfaces as endpoint
from app.core.auth import UserContext, get_current_user
from app.core import runtime_env
from app.services.platform_tool_registry import TOOLS, tool_by_key, tools_snapshot

_ENDPOINTS = Path(__file__).resolve().parents[2] / "app" / "api" / "v1" / "endpoints"


def test_all_tool_paths_refer_to_existing_get_declarations():
    by_module = {
        "app.api.v1.endpoints.pcr": "/api/v1/pcr",
        "app.api.v1.endpoints.candles": "/api/v1/candles",
        "app.api.v1.endpoints.lanes": "/api/v1",
        "app.api.v1.endpoints.kite": "/api/v1/kite",
    }
    for tool in TOOLS:
        source_module = tool.backend_module
        assert source_module in by_module
        source = _ENDPOINTS / (source_module.rsplit(".", 1)[1] + ".py")
        tree = ast.parse(source.read_text(encoding="utf-8"))
        local_path = tool.api_path[len(by_module[source_module]):]
        assert any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(
                isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                and isinstance(dec.func.value, ast.Name)
                and dec.func.value.id == "router" and dec.func.attr == "get"
                and dec.args and isinstance(dec.args[0], ast.Constant)
                and dec.args[0].value == local_path
                for dec in node.decorator_list
            )
            for node in tree.body
        ), (tool.key, source, local_path)


def test_tool_lookup_is_allowlisted_and_inert():
    assert tool_by_key("ticker_health") in TOOLS
    assert tool_by_key("placeorder") is None
    assert tool_by_key("../../orders") is None
    assert all(t.method == "GET" for t in TOOLS)
    assert all(t.requires_kite_feature and not t.permits_order_write
               and not t.launches_runtime for t in TOOLS)
    a = tools_snapshot()
    a[0]["api_path"] = "/api/v1/kite/orders"
    assert tools_snapshot()[0]["api_path"] != a[0]["api_path"]


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(endpoint.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: UserContext(user_id="owner", role="viewer")
    monkeypatch.setattr(endpoint, "require_feature", lambda *_args: None)
    with TestClient(app) as c:
        yield c


def test_read_only_tools_discovery_viewer_and_unmounted_ui(client):
    response = client.get("/api/v1/platform/tools")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    body = response.json()
    assert body["version"] == "0.1.0"
    assert body["execution_authorized"] is False
    assert body["tools_ui_mounted"] is False
    assert [t["key"] for t in body["tools"]] == [t.key for t in TOOLS]
    assert all(t["method"] == "GET" and not t["permits_order_write"] for t in body["tools"])
    assert client.get("/tools").status_code == 404
    assert client.post("/api/v1/platform/tools").status_code == 405


def test_tools_discovery_rejects_no_kite_grant(client, monkeypatch):
    def deny(_principal, _feature):
        raise HTTPException(403, "Kite grant required")
    monkeypatch.setattr(endpoint, "require_feature", deny)
    assert client.get("/api/v1/platform/tools").status_code == 403


def test_tools_discovery_rejects_production_anonymous(client, monkeypatch):
    monkeypatch.setattr(runtime_env, "is_production", lambda: True)
    client.app.dependency_overrides.clear()
    assert client.get("/api/v1/platform/tools").status_code == 401
