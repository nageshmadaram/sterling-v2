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
