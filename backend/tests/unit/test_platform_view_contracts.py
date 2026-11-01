"""Charting/scalping route contracts are informational and non-executable."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from app.api.v1.endpoints import platform_surfaces as endpoint
from app.core.auth import UserContext, get_current_user
from app.core import runtime_env
from app.core.lane_registry import all_lanes
from app.services import platform_view_contracts as views

BACKEND = Path(__file__).resolve().parents[2] / "app"
FRONTEND = Path(__file__).resolve().parents[3] / "frontend/src"


def test_view_contracts_match_existing_canonical_sources():
    rows = {row["key"]: row for row in views.view_contract_snapshot()}
    assert set(rows) == {"charting", "scalping"}
    chart = rows["charting"]
    scalp = rows["scalping"]
    assert chart["proposed_global_route"] == "/trading"
    assert scalp["proposed_global_route"] == "/scalping"
    assert chart["canonical_read_api"] == "/api/v1/candles/{underlying}"
    assert scalp["canonical_read_api"] == "/api/v1/strategies/{strategy}/{mode}/status"
    assert chart["existing_component"] == "frontend/src/components/kite/InstrumentPane.tsx"
    assert scalp["existing_component"] == ""
    assert not chart["existing_lane_keys"]
    assert scalp["existing_lane_keys"] == sorted(
        lane.lane_key for lane in all_lanes() if lane.mode.value == "scalping"
    )
    assert scalp["existing_lane_keys"]  # real lane registry, no invented strategy IDs
    assert all(
        not row["route_mounted"] and not row["execution_authorized"]
        and not row["independent_feed"] for row in rows.values()
    )

    kite_tab = (FRONTEND / "components/kite/KiteTab.tsx").read_text("utf-8")
    pane = (FRONTEND / "components/kite/InstrumentPane.tsx").read_text("utf-8")
    assert "handleOpenInstrument(" in kite_tab
    assert "<InstrumentPane" in kite_tab
    assert "export function InstrumentPane" in pane
    for module, path in [
        ("candles.py", "/{underlying}"),
        ("lanes.py", "/strategies/{strategy}/{mode}/status"),
    ]:
        tree = ast.parse((BACKEND / "api/v1/endpoints" / module).read_text("utf-8"))
        assert any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(
                isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                and isinstance(dec.func.value, ast.Name)
                and dec.func.value.id == "router" and dec.func.attr == "get"
                and dec.args and isinstance(dec.args[0], ast.Constant)
                and dec.args[0].value == path
                for dec in node.decorator_list
            )
            for node in tree.body
        ), (module, path)


def test_lane_view_is_a_fresh_projection_not_a_shadow_registry(monkeypatch):
    fake = SimpleNamespace(
        lane_key="test:scalping", mode=SimpleNamespace(value="scalping")
    )
    monkeypatch.setattr(views, "all_lanes", lambda: iter([fake]))
    rows = views.view_contract_snapshot()
    rows[1]["existing_lane_keys"].append("fake:other")
    assert views.view_contract_snapshot()[1]["existing_lane_keys"] == ["test:scalping"]


def test_view_registry_has_no_broker_or_execution_dependency():
    source = (BACKEND / "services/platform_view_contracts.py").read_text("utf-8")
    tree = ast.parse(source)
    imports = [
        node.module for node in tree.body
        if isinstance(node, ast.ImportFrom)
    ]
    assert "app.core.lane_registry" in imports
    assert not any(
        name and ("broker" in name or "execution" in name or "ticker" in name
                  or "accounts" in name or "canonical_strategy_entry" in name)
        for name in imports
    )


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(endpoint.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        user_id="viewer", role="viewer"
    )
    monkeypatch.setattr(endpoint, "require_feature", lambda _user, _feature: None)
    with TestClient(app) as test_client:
        yield test_client


def test_view_contract_is_read_only_and_keeps_global_routes_absent(client):
    response = client.get("/api/v1/platform/view-contracts")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    payload = response.json()
    assert payload["execution_authorized"] is False
    assert payload["global_routes_mounted"] is False
    assert payload["surfaces"] == views.view_contract_snapshot()
    for path in ("/trading", "/scalping"):
        assert client.get(path).status_code == 404
        assert client.post(path).status_code == 404
    assert client.post("/api/v1/platform/view-contracts").status_code == 405


def test_view_contract_respects_feature_denial_and_anonymous_production(client, monkeypatch):
    def deny(_user, feature):
        assert feature == "kite"
        raise HTTPException(403, "Kite grant revoked")
    monkeypatch.setattr(endpoint, "require_feature", deny)
    assert client.get("/api/v1/platform/view-contracts").status_code == 403
    monkeypatch.setattr(runtime_env, "is_production", lambda: True)
    client.app.dependency_overrides.clear()
    assert client.get("/api/v1/platform/view-contracts").status_code == 401
