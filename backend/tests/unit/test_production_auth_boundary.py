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



def _route_uses_current_user(router, path: str, method: str) -> bool:
    """Inspect FastAPI's dependency graph rather than relying on source text."""
    from fastapi.routing import APIRoute

    def contains_auth(dependant) -> bool:
        pending = list(getattr(dependant, "dependencies", ()) or ())
        while pending:
            dep = pending.pop()
            if getattr(dep, "call", None) is get_current_user:
                return True
            pending.extend(list(getattr(dep, "dependencies", ()) or ()))
        return False

    for route in router.routes:
        if (
            isinstance(route, APIRoute)
            and route.path == path
            and method.upper() in (route.methods or set())
        ):
            return contains_auth(route.dependant)
    raise AssertionError(f"route not found: {method} {path}")


def test_state_changing_routes_require_verified_identity():
    """Production mutations must not depend on the historical X-User-Id seam."""
    from app.api.v1.endpoints.adaptive_edge import router as adaptive_router
    from app.api.v1.endpoints.backtest import router as backtest_router
    from app.api.v1.endpoints.config import router as config_router
    from app.api.v1.endpoints.simulation import router as simulation_router

    protected = [
        (adaptive_router, "/adaptive-edge/settings", "PUT"),
        (config_router, "/config/adaptive-edge", "PUT"),
        (backtest_router, "/backtest/unified/run", "POST"),
        (backtest_router, "/backtest/adaptive-edge/compare", "POST"),
        (simulation_router, "/simulation/stop", "POST"),
        (simulation_router, "/simulation/clear", "POST"),
        (simulation_router, "/simulation/pause", "POST"),
        (simulation_router, "/simulation/resume", "POST"),
        (simulation_router, "/simulation/speed", "POST"),
        (simulation_router, "/simulation/seek", "POST"),
    ]
    for router, path, method in protected:
        assert _route_uses_current_user(router, path, method), f"{method} {path} is unauthenticated"


def test_replay_sse_remains_read_only_and_public_transport():
    """EventSource cannot attach our Authorization header; keep this read-only stream usable."""
    from app.api.v1.endpoints.simulation import router as simulation_router

    assert not _route_uses_current_user(simulation_router, "/simulation/stream", "GET")



def test_all_mutating_api_routes_are_authenticated_or_explicitly_public():
    """No new state-changing API may silently bypass production identity.

    The allowlist contains only routes whose request body itself carries the
    authentication/proof or whose operation is purely functional/read-only.
    """
    from fastapi.routing import APIRoute
    from main import create_app

    public = {
        ("POST", "/api/v1/auth/login"),      # password is the credential
        ("POST", "/api/v1/auth/refresh"),    # signed refresh JWT is the credential
        ("POST", "/api/v1/kite/postback"),   # broker checksum signature is verified
        ("POST", "/api/v1/opening-volume-leaders/evaluate"),  # pure deterministic evaluation
    }

    def contains_auth(dependant) -> bool:
        pending = list(getattr(dependant, "dependencies", ()) or ())
        while pending:
            dep = pending.pop()
            if getattr(dep, "call", None) is get_current_user:
                return True
            pending.extend(list(getattr(dep, "dependencies", ()) or ()))
        return False

    violations = []
    for route in create_app().routes:
        if not isinstance(route, APIRoute):
            continue
        methods = set(route.methods or set()) & {"POST", "PUT", "PATCH", "DELETE"}
        for method in methods:
            key = (method, route.path)
            if key in public:
                continue
            if not contains_auth(route.dependant):
                violations.append(key)

    assert violations == [], f"unauthenticated mutating API routes: {sorted(violations)}"
