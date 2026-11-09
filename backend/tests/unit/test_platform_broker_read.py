"""Normalized read DTOs cannot turn session hints into execution authority."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from app.api.v1.endpoints import platform_surfaces as endpoint
from app.core.auth import UserContext, get_current_user
from app.services.platform_broker_read import normalize_broker_read
from app.services.platform_strategy_contract import strategy_contract_snapshot
from app.core.lane_registry import all_lanes


def snapshot(**updates):
    value = {
        "view": "live", "account_configured": False,
        "stored_session_present": False, "shared_owner_feed": False,
        "active_account_feed_bound": False, "feed_connected": False,
        "subscribed_count": 0, "last_tick_ms": 0,
    }
    value.update(updates)
    return value


@pytest.mark.parametrize("data,state", [
    ({}, "not_configured"),
    ({"account_configured": True}, "no_stored_token"),
    ({"account_configured": True, "stored_session_present": True}, "stored_token_unverified"),
    ({"account_configured": True, "stored_session_present": True,
      "active_account_feed_bound": True}, "feed_disconnected"),
    ({"account_configured": True, "stored_session_present": True,
      "active_account_feed_bound": True, "feed_connected": True}, "feed_connected"),
])
def test_read_status_does_not_claim_authenticated_broker_or_execution(data, state):
    read = normalize_broker_read(snapshot(**data))
    assert read["session_state"] == state
    assert read["broker_login_verified"] is False
    assert read["order_execution_authorized"] is False
    assert "account_id" not in read and "access_token" not in read


@pytest.mark.parametrize("data", [
    {"account_configured": False, "stored_session_present": True},
    {"account_configured": False, "feed_connected": True},
    {"account_configured": True, "active_account_feed_bound": True},
    {"account_configured": True, "stored_session_present": True, "feed_connected": True},
    {"view": "simulation", "shared_owner_feed": False},
    {"view": "live", "shared_owner_feed": True},
])
def test_ambiguous_state_is_not_silently_normalized(data):
    with pytest.raises(ValueError, match="inconsistent"):
        normalize_broker_read(snapshot(**data))


@pytest.mark.parametrize("data", [
    {"account_configured": 1},
    {"stored_session_present": "false"},
    {"active_account_feed_bound": 1},
    {"feed_connected": "true"},
    {"shared_owner_feed": 0},
    {"view": []},
    {"subscribed_count": -1},
    {"subscribed_count": True},
    {"last_tick_ms": -10},
    {"last_tick_ms": False},
    {"subscribed_count": 4},
    {"last_tick_ms": 7},
])
def test_malformed_or_unbound_feed_fields_are_refused(data):
    with pytest.raises(ValueError, match="inconsistent_canonical_broker_read_evidence"):
        normalize_broker_read(snapshot(**data))


def test_unbound_feed_metadata_refused_by_http_route(client, monkeypatch):
    monkeypatch.setattr(endpoint, "session_feed_snapshot",
                        lambda _: snapshot(subscribed_count=3))
    response = client.get("/api/v1/platform/broker/summary")
    assert response.status_code == 503
    assert response.json()["detail"] == "Scoped broker read unavailable"


def test_simulation_never_reports_new_trading_authority():
    read = normalize_broker_read(snapshot(
        view="simulation", shared_owner_feed=True, account_configured=True,
        stored_session_present=True, active_account_feed_bound=True,
        feed_connected=True, subscribed_count=5, last_tick_ms=300,
    ))
    assert read["view"] == "simulation" and read["shared_owner_feed"]
    assert read["feed_connected"] and read["subscriptions_count"] == 5
    assert read["order_execution_authorized"] is False


def test_strategy_catalog_matches_existing_lanes_without_permitting_trading():
    canonical = {lane.lane_key for lane in all_lanes()}
    rows = strategy_contract_snapshot()
    assert rows and {row["lane_key"] for row in rows} == canonical
    assert all(not row["platform_execute_authorized"] for row in rows)


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(endpoint.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: UserContext(user_id="owner", role="viewer")
    monkeypatch.setattr(endpoint, "require_feature", lambda *_a: None)
    monkeypatch.setattr(endpoint, "session_feed_snapshot", lambda _: snapshot())
    with TestClient(app) as c:
        yield c


def test_normalized_and_strategy_routes_are_get_only_for_authorized_viewer(client):
    broker = client.get("/api/v1/platform/broker/summary")
    assert broker.status_code == 200
    assert broker.json()["session_state"] == "not_configured"
    assert broker.json()["order_execution_authorized"] is False
    assert broker.headers["cache-control"] == "no-store, private"
    strategies = client.get("/api/v1/platform/strategies")
    assert strategies.status_code == 200
    assert strategies.json()["execution_authorized"] is False
    assert strategies.json()["strategy_dispatch_mounted"] is False
    assert strategies.json()["lanes"]
    for path in ("/api/v1/platform/broker/summary", "/api/v1/platform/strategies"):
        assert client.post(path).status_code == 405


def test_feature_denial_precedes_service_access(client, monkeypatch):
    monkeypatch.setattr(endpoint, "require_feature",
                        lambda *_: (_ for _ in ()).throw(HTTPException(403)))
    monkeypatch.setattr(endpoint, "session_feed_snapshot",
                        lambda _: (_ for _ in ()).throw(AssertionError("should not query broker data")))
    assert client.get("/api/v1/platform/broker/summary").status_code == 403
    assert client.get("/api/v1/platform/strategies").status_code == 403


def test_missing_broker_scoping_evidence_fails_closed(client, monkeypatch):
    monkeypatch.setattr(endpoint, "session_feed_snapshot",
                        lambda _: (_ for _ in ()).throw(ValueError("identity_mismatch")))
    assert client.get("/api/v1/platform/broker/summary").status_code == 503
