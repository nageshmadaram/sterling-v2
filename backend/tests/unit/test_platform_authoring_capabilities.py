"""Authoring metadata must reflect #25 source, not unmounted #22 schemas."""
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from app.api.v1.endpoints import platform_surfaces as endpoint
from app.core.auth import UserContext, get_current_user
from app.core import runtime_env
from app.services.platform_offline_authoring import (
    MAX_SOURCE_BYTES, MAX_AST_NODES, MAX_FLOW_NODES, MAX_FLOW_EDGES,
)


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(endpoint.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: UserContext(user_id="owner", role="viewer")
    monkeypatch.setattr(endpoint, "require_feature", lambda *_: None)
    with TestClient(app) as test_client:
        yield test_client


def test_authoring_route_exposes_actual_bounds_without_execution(client):
    response = client.get("/api/v1/platform/authoring/capabilities")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    body = response.json()
    assert body["execution_authorized"] is False
    assert body["python_sandbox_available"] is False
    assert body["flow_executor_available"] is False
    assert body["global_authoring_routes_mounted"] is False
    assert body["python_max_source_bytes"] == MAX_SOURCE_BYTES
    assert body["python_max_ast_nodes"] == MAX_AST_NODES
    assert body["flow_max_nodes"] == MAX_FLOW_NODES
    assert body["flow_max_edges"] == MAX_FLOW_EDGES
    assert body["flow_node_schema"] == "id_only"
    assert client.post("/api/v1/platform/authoring/capabilities").status_code == 405
    for path in ("/python", "/flow"):
        assert client.get(path).status_code == 404


def test_authoring_feature_denial_does_not_consult_source_services(client, monkeypatch):
    monkeypatch.setattr(endpoint, "require_feature", lambda *_: (_ for _ in ()).throw(HTTPException(403)))
    assert client.get("/api/v1/platform/authoring/capabilities").status_code == 403


def test_authoring_contract_requires_authenticated_identity_in_production(client, monkeypatch):
    monkeypatch.setattr(runtime_env, "is_production", lambda: True)
    client.app.dependency_overrides.clear()
    assert client.get("/api/v1/platform/authoring/capabilities").status_code == 401
