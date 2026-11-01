"""Non-authorizing OpenAlgo API mapping must preserve canonical route truth."""
import ast
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from app.api.v1.endpoints import platform_surfaces as endpoint
from app.core.auth import UserContext, get_current_user
from app.core import runtime_env
from app.services.platform_api_normalization import MAPPINGS, normalization_snapshot

ENDPOINTS = Path(__file__).resolve().parents[2] / "app" / "api" / "v1" / "endpoints"


def test_canonical_targets_are_actual_existing_router_declarations():
    prefixes = {"kite": "/api/v1/kite", "openalgo_compat": "/api/v1/compat/openalgo"}
    for item in MAPPINGS:
        module = item.canonical_module.rsplit(".", 1)[-1]
        assert module in prefixes
        path = item.canonical_path.removeprefix(prefixes[module])
        assert path.startswith("/")
        tree = ast.parse((ENDPOINTS / f"{module}.py").read_text("utf-8"))
        assert any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(
                isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                and isinstance(dec.func.value, ast.Name)
                and dec.func.value.id == "router"
                and dec.func.attr.upper() == item.canonical_method
                and dec.args and isinstance(dec.args[0], ast.Constant)
                and dec.args[0].value == path
                for dec in node.decorator_list
            )
            for node in tree.body
        ), (item.key, item.canonical_path)


def test_only_order_status_has_a_read_only_adapter():
    mappings = {entry.key: entry for entry in MAPPINGS}
    assert set(mappings) == {"order_status", "order_book", "place_order", "cancel_order"}
    assert mappings["order_status"].compatibility == "read_only_adapter"
    assert all(
        item.compatibility == "no_wrapper" for key, item in mappings.items()
        if key != "order_status"
    )
    assert all(not item.executable_through_platform for item in MAPPINGS)
    records = normalization_snapshot()
    records[0]["canonical_path"] = "/api/v1/unsafe"
    assert normalization_snapshot()[0]["canonical_path"] == MAPPINGS[0].canonical_path


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(endpoint.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: UserContext(user_id="owner", role="viewer")
    monkeypatch.setattr(endpoint, "require_feature", lambda *_args: None)
    with TestClient(app) as c:
        yield c


def test_viewer_can_read_but_not_dispatch_through_inventory(client):
    response = client.get("/api/v1/platform/api-normalization")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    data = response.json()
    assert data["execution_authorized"] is False
    assert data["proxy_mounted"] is False
    assert {entry["key"] for entry in data["mappings"]} == {entry.key for entry in MAPPINGS}
    assert all(not entry["executable_through_platform"] for entry in data["mappings"])
    assert client.post("/api/v1/platform/api-normalization").status_code == 405
    assert client.post("/api/v1/placeorder").status_code == 404


def test_feature_denial_and_anonymous_production_are_enforced(client, monkeypatch):
    monkeypatch.setattr(
        endpoint, "require_feature",
        lambda *_args: (_ for _ in ()).throw(HTTPException(403)),
    )
    assert client.get("/api/v1/platform/api-normalization").status_code == 403
    monkeypatch.setattr(runtime_env, "is_production", lambda: True)
    client.app.dependency_overrides.clear()
    assert client.get("/api/v1/platform/api-normalization").status_code == 401
