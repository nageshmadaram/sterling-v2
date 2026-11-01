"""Platform strategy surface reads lanes, never creates execution authority."""
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
from app.services import platform_strategy_contract as strategy

ROOT = Path(__file__).resolve().parents[2] / "app"


def test_lane_data_is_from_existing_source_and_never_grants_platform_execution():
    original = list(all_lanes())
    records = strategy.strategy_contract_snapshot()
    assert len(records) == len(original)
    assert {row["lane_key"] for row in records} == {lane.lane_key for lane in original}
    assert all(row["platform_execute_authorized"] is False for row in records)
    assert all(set(row) == {
        "strategy_id", "mode", "lane_key", "state", "rules_defined",
        "platform_execute_authorized",
    } for row in records)


def test_lane_contract_adapts_registry_changes_without_shadow_registry(monkeypatch):
    synthetic = SimpleNamespace(
        strategy_id="synthetic", mode=SimpleNamespace(value="offline"),
        lane_key="synthetic:offline", state=SimpleNamespace(value="research"),
        rules_defined=False,
    )
    monkeypatch.setattr(strategy, "all_lanes", lambda: iter([synthetic]))
    assert strategy.strategy_contract_snapshot() == [{
        "strategy_id": "synthetic", "mode": "offline",
        "lane_key": "synthetic:offline", "state": "research",
        "rules_defined": False, "platform_execute_authorized": False,
    }]


def test_platform_has_no_execution_import_or_dispatch():
    source = (ROOT / "services" / "platform_strategy_contract.py").read_text("utf-8")
    tree = ast.parse(source)
    imported = [
        module for node in tree.body if isinstance(node, ast.ImportFrom)
        for module in [node.module]
    ]
    assert "app.core.lane_registry" in imported
    assert not any(module and (
        module.startswith("app.services.execution")
        or module.startswith("app.services.exchanges")
        or module.startswith("app.services.canonical_strategy_entry")
    ) for module in imported)
    assert strategy.CANONICAL_ENTRY == "app.services.canonical_strategy_entry.submit_entry"
    assert strategy.CANONICAL_EXECUTION == "app.services.execution_service.CanonicalExecutionService"
    assert strategy.CANONICAL_LANE_AUTHORITY == "app.core.lane_registry.may_originate"
    # Canonical strategy entry (not this module) continues to call canonical
    # execution, preserving one route for risk, journal and order submission.
    canonical_tree = ast.parse(
        (ROOT / "services" / "canonical_strategy_entry.py").read_text("utf-8")
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "submit_order"
        and isinstance(node.func.value, ast.Call)
        and isinstance(node.func.value.func, ast.Name)
        and node.func.value.func.id == "CanonicalExecutionService"
        for node in ast.walk(canonical_tree)
    )


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(endpoint.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: UserContext(user_id="viewer", role="viewer")
    monkeypatch.setattr(endpoint, "require_feature", lambda user, feature: None)
    with TestClient(app) as c:
        yield c


def test_strategies_are_metadata_only_with_no_mutating_method(client):
    res = client.get("/api/v1/platform/strategies")
    assert res.status_code == 200
    assert res.headers["cache-control"] == "no-store, private"
    payload = res.json()
    assert payload["execution_authorized"] is False
    assert payload["strategy_dispatch_mounted"] is False
    assert payload["canonical_entry"] == strategy.CANONICAL_ENTRY
    assert payload["canonical_execution"] == strategy.CANONICAL_EXECUTION
    assert payload["canonical_lane_authority"] == strategy.CANONICAL_LANE_AUTHORITY
    assert payload["lanes"] == strategy.strategy_contract_snapshot()
    assert all(not row["platform_execute_authorized"] for row in payload["lanes"])
    assert client.post("/api/v1/platform/strategies").status_code == 405
    assert client.post("/api/v1/platform/strategies/execute").status_code == 404


def test_feature_denial_and_anonymous_production_refuse(client, monkeypatch):
    monkeypatch.setattr(endpoint, "require_feature",
                        lambda *args: (_ for _ in ()).throw(HTTPException(403)))
    assert client.get("/api/v1/platform/strategies").status_code == 403
    monkeypatch.setattr(runtime_env, "is_production", lambda: True)
    client.app.dependency_overrides.clear()
    assert client.get("/api/v1/platform/strategies").status_code == 401
