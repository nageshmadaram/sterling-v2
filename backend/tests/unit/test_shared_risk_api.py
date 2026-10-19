from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from app.core.auth import UserContext, get_current_user
from app.api.v1.endpoints.config import router
from app.services import db


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.setattr(db, "_available", False)
    assert db.init()
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: UserContext(user_id="owner")
    with TestClient(app) as client:
        yield client


def test_own_profile_save_revision_and_tenant_isolation(client):
    original = client.get("/api/v1/config/shared-risk").json()
    body = {
        **original,
        "enabled": True,
        "strategies": ["snapback", "gamma_unwind"],
        "allocation_inr": 10000,
        "per_trade_risk_inr": 500,
    }
    saved = client.put("/api/v1/config/shared-risk", json=body)
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1
    assert client.put("/api/v1/config/shared-risk", json=body).status_code == 409
    client.app.dependency_overrides[get_current_user] = lambda: UserContext(
        user_id="other"
    )
    assert client.get("/api/v1/config/shared-risk").json() == original


def test_unknown_identity_or_invalid_limits_cannot_be_saved(client):
    assert (
        client.put(
            "/api/v1/config/shared-risk", json={"uid": "other", "allocation_inr": 10}
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/api/v1/config/shared-risk",
            json={"enabled": True, "strategies": ["snapback"]},
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/api/v1/config/shared-risk", json={"allocation_inr": -1}
        ).status_code
        == 422
    )


def test_production_requires_normal_authentication(client, monkeypatch):
    from app.core import runtime_env

    monkeypatch.setattr(runtime_env, "is_production", lambda: True)
    client.app.dependency_overrides.clear()
    assert client.get("/api/v1/config/shared-risk").status_code == 401
    assert client.put("/api/v1/config/shared-risk", json={}).status_code == 401
