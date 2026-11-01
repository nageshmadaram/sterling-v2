"""Contract tests: platform discovery must not create execution authority."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from app.api.v1.endpoints import platform_surfaces as endpoint
from app.core.auth import UserContext, get_current_user
from app.core import runtime_env
from app.services.platform_surface_registry import (
    CANONICAL_SERVICES, SURFACES, catalog, surface_for_path,
)


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(endpoint.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: UserContext(user_id="owner", role="viewer")
    monkeypatch.setattr(endpoint, "require_feature", lambda _user, _feature: None)
    with TestClient(app) as test_client:
        yield test_client


def test_read_only_contract_and_registry_consistency(client):
    response = client.get("/api/v1/platform/surfaces")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    data = response.json()
    assert data["version"] == "0.1.0"
    assert data["execution_authorized"] is False
    assert data["planned_routes_mounted"] is False
    assert data["canonical_services"] == CANONICAL_SERVICES
    assert [s["key"] for s in data["surfaces"]] == [s.key for s in SURFACES]
    assert len({s["path"] for s in data["surfaces"]}) == len(SURFACES)
    assert all(s["permits_order_write"] is False for s in data["surfaces"])


def test_planned_paths_are_metadata_not_live_routes(client):
    for surface in SURFACES:
        assert surface_for_path(surface.path) == surface
        if surface.state == "planned":
            assert surface.mounts_route is False
            assert client.get(surface.path).status_code == 404
            assert client.post(surface.path).status_code == 404
    assert surface_for_path("/api/v1/placeorder") is None
    assert client.post("/api/v1/platform/surfaces").status_code == 405


def test_broker_and_ticker_are_reused_by_name_not_instantiated():
    assert CANONICAL_SERVICES["broker_sessions"] == "app.services.exchanges.kite.accounts"
    assert CANONICAL_SERVICES["market_data"] == "app.services.exchanges.kite.ticker_manager"
    assert CANONICAL_SERVICES["execution"] == "app.services.execution_service"
    changed = catalog()
    changed[0]["path"] = "/unsafe"
    assert catalog()[0]["path"] == SURFACES[0].path


def test_feature_denial_blocks_catalog(client, monkeypatch):
    def deny(_principal, feature):
        assert feature == "kite"
        raise HTTPException(403, "Kite feature denied")
    monkeypatch.setattr(endpoint, "require_feature", deny)
    assert client.get("/api/v1/platform/surfaces").status_code == 403


def test_production_rejects_anonymous_catalog_request(client, monkeypatch):
    monkeypatch.setattr(runtime_env, "is_production", lambda: True)
    client.app.dependency_overrides.clear()
    assert client.get("/api/v1/platform/surfaces").status_code == 401

def test_existing_api_entries_have_exact_source_declared_methods():
    """Discovery must not confuse an API prefix with a mounted endpoint."""
    import ast
    from pathlib import Path

    endpoint_dir = Path(__file__).resolve().parents[2] / "app" / "api" / "v1" / "endpoints"
    expected = {
        "platform_surfaces": (
            "platform_surfaces.py", "get", "/platform/surfaces",
            "/api/v1/platform/surfaces",
        ),
        "broker_api": ("kite.py", "get", "/accounts", "/api/v1/kite/accounts"),
        "openalgo_orderstatus": (
            "openalgo_compat.py", "post", "/orderstatus",
            "/api/v1/compat/openalgo/orderstatus",
        ),
        "platform_runtime": (
            "platform_surfaces.py", "get", "/platform/runtime",
            "/api/v1/platform/runtime",
        ),
        "platform_tools": (
            "platform_surfaces.py", "get", "/platform/tools",
            "/api/v1/platform/tools",
        ),
        "view_contracts": (
            "platform_surfaces.py", "get", "/platform/view-contracts",
            "/api/v1/platform/view-contracts",
        ),

        "strategy_contract": (
            "platform_surfaces.py", "get", "/platform/strategies",
            "/api/v1/platform/strategies",
        ),        "api_normalization": (
            "platform_surfaces.py", "get", "/platform/api-normalization",
            "/api/v1/platform/api-normalization",
        ),
    }
    for key, (filename, method, relative, canonical) in expected.items():
        item = next(s for s in SURFACES if s.key == key)
        assert item.mounts_route is True
        assert item.path == canonical
        assert item.http_method == method.upper()
        tree = ast.parse((endpoint_dir / filename).read_text(encoding="utf-8"))
        assert any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(
                isinstance(d, ast.Call)
                and isinstance(d.func, ast.Attribute)
                and isinstance(d.func.value, ast.Name)
                and d.func.value.id == "router"
                and d.func.attr == method
                and bool(d.args)
                and isinstance(d.args[0], ast.Constant)
                and d.args[0].value == relative
                for d in node.decorator_list
            )
            for node in tree.body
        ), (key, filename, method, relative)


def test_planned_platform_entries_have_no_execution_metadata():
    planned = [s for s in SURFACES if s.state == "planned"]
    assert {s.key for s in planned} == {
        "python", "flow", "strategy", "tools", "trading", "scalping",
    }
    assert all(not s.mounts_route and not s.permits_order_write for s in planned)
    assert all(not s.backing for s in planned)


def test_surface_discovery_is_complete_for_its_own_routes(client):
    """Every mounted platform-discovery GET must appear once in registry."""
    import ast
    from pathlib import Path

    file = Path(__file__).resolve().parents[2] / "app/api/v1/endpoints/platform_surfaces.py"
    tree = ast.parse(file.read_text("utf-8"))
    declared = {
        "/api/v1" + dec.args[0].value
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for dec in node.decorator_list
        if isinstance(dec, ast.Call)
        and isinstance(dec.func, ast.Attribute)
        and isinstance(dec.func.value, ast.Name)
        and dec.func.value.id == "router" and dec.func.attr == "get"
        and dec.args and isinstance(dec.args[0], ast.Constant)
    }
    registered = {
        surface.path
        for surface in SURFACES
        if surface.backing == "app.api.v1.endpoints.platform_surfaces"
        and surface.mounts_route and surface.http_method == "GET"
    }
    assert registered == declared
    response = client.get("/api/v1/platform/surfaces")
    assert response.status_code == 200
    reported = {
        item["path"] for item in response.json()["surfaces"]
        if item["backing"] == "app.api.v1.endpoints.platform_surfaces"
    }
    assert reported == declared
