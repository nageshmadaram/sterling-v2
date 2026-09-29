"""Production identity boundary regression tests.

The historical X-User-Id header remains a local/test convenience, never a
production authentication mechanism.
"""
from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

from app.core import tokens
from app.core.auth import UserContext, get_current_user
from app.services import db
from app.services.auth import user_store


@pytest.fixture()
def production_identity(tmp_path, monkeypatch):
    db_path = str(tmp_path / "auth.db")
    monkeypatch.setenv("STERLING_DB_PATH", db_path)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("STERLING_SECRET_KEY", "enc-" + "a" * 48)
    monkeypatch.setenv("STERLING_JWT_SECRET", "jwt-" + "b" * 48)
    monkeypatch.delenv("STERLING_ADMIN_PASSWORD", raising=False)
    monkeypatch.setattr(db, "_DB_PATH", db_path)
    monkeypatch.setattr(db, "_available", True)
    db.init()
    user_store.clear()
    user_store.bootstrap()
    user = user_store.add("alice", "correct horse battery staple", role="admin", user_id="alice-id")

    app = FastAPI()

    @app.get("/who")
    async def who(current: UserContext = Depends(get_current_user)):
        return {"id": current.user_id, "username": current.username, "role": current.role}

    yield TestClient(app), user
    user_store.clear()


def test_production_rejects_spoofed_user_header(production_identity):
    client, _ = production_identity
    response = client.get("/who", headers={"X-User-Id": "alice-id"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_production_accepts_verified_access_token(production_identity):
    client, user = production_identity
    access = tokens.mint_access(user.id, user.username, user.role, user.token_version)
    response = client.get("/who", headers={"Authorization": f"Bearer {access}", "X-User-Id": "victim"})
    assert response.status_code == 200
    assert response.json() == {"id": user.id, "username": "alice", "role": "admin"}


def test_refresh_token_cannot_authenticate_api_request(production_identity):
    client, user = production_identity
    refresh = tokens.mint_refresh(user.id, user.username, user.role, user.token_version)
    response = client.get("/who", headers={"Authorization": f"Bearer {refresh}"})
    assert response.status_code == 401


def test_revoked_access_token_is_rejected(production_identity):
    client, user = production_identity
    access = tokens.mint_access(user.id, user.username, user.role, user.token_version)
    user_store.bump_token_version(user.id)
    response = client.get("/who", headers={"Authorization": f"Bearer {access}"})
    assert response.status_code == 401


def test_development_header_fallback_remains_local_only(tmp_path, monkeypatch):
    db_path = str(tmp_path / "dev-auth.db")
    monkeypatch.setenv("STERLING_DB_PATH", db_path)
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setattr(db, "_DB_PATH", db_path)
    monkeypatch.setattr(db, "_available", True)
    db.init()
    user_store.clear()

    app = FastAPI()

    @app.get("/who")
    async def who(current: UserContext = Depends(get_current_user)):
        return {"id": current.user_id}

    response = TestClient(app).get("/who", headers={"X-User-Id": "fixture-user"})
    assert response.status_code == 200
    assert response.json()["id"] == "fixture-user"
    user_store.clear()


def test_auth_router_is_mounted_on_application():
    from main import create_app
    from tests.route_surface import routes_under

    routes = {route_path for route_path, _methods in routes_under(create_app(), "/api/v1/auth")}
    assert "/api/v1/auth/login" in routes
    assert "/api/v1/auth/refresh" in routes
    assert "/api/v1/auth/me" in routes
