"""Phase 10 isolated offline-only ASGI app: *no* legacy order routes or workers.

This ASGI module is a separate process entrypoint. Importing backend/main.py
or mounting legacy trading routers is forbidden by the Phase10 CI gate.
"""
from __future__ import annotations
from contextlib import asynccontextmanager
import os
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from app.core import offline_security
from app.persistence.base import make_engine

class StepUp(BaseModel):
    otp: str=Field(pattern=r"^\d{6}$",min_length=6,max_length=6)

def _token(header:str|None)->str:
    if not header or not header.startswith("Bearer ") or not header[7:].strip():
        raise PermissionError("signed bearer access token required")
    return header[7:].strip()

def create_app()->FastAPI:
    @asynccontextmanager
    async def lifespan(app:FastAPI):
        offline_security.validate_profile_config()
        from app.core.offline_database_privileges import assert_runtime_privileges
        assert_runtime_privileges()
        yield

    app=FastAPI(title="Sterling offline operator observability only",
                docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)

    @app.middleware("http")
    async def enforce_offline_perimeter(request:Request,call_next):
        path=request.url.path
        try:
            if path.startswith("/api/v1/offline-validation/"):
                bearer=_token(request.headers.get("Authorization"))
                offline_security.check_access(bearer,request.headers.get("X-Sterling-Offline-MFA"))
                # Never permit bearer credentials in URL or identity from headers.
                if "access_token" in request.query_params or "token" in request.query_params:
                    raise PermissionError("URL credentials forbidden")
            response=await call_next(request)
        except (PermissionError,ValueError) as exc:
            response=JSONResponse(status_code=403,content={"detail":"Offline operator security gate denied"})
        response.headers["Cache-Control"]="no-store, private"
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["X-Frame-Options"]="DENY"
        response.headers["Referrer-Policy"]="no-referrer"
        return response

    from app.api.v1.endpoints import auth as auth_endpoints
    auth=auth_endpoints
    # Strict allowlist of existing signed-token auth handlers, NOT entire auth router.
    app.add_api_route("/api/v1/auth/login",auth.login,methods=["POST"],response_model=auth.TokenResponse)
    app.add_api_route("/api/v1/auth/refresh",auth.refresh,methods=["POST"],response_model=auth.TokenResponse)
    app.add_api_route("/api/v1/auth/logout",auth.logout,methods=["POST"])
    app.add_api_route("/api/v1/auth/me",auth.me,methods=["GET"],response_model=auth.MeResponse)

    @app.post("/api/v1/offline-security/mfa/verify")
    async def verify_mfa(request:Request,body:StepUp,authorization:str|None=Header(default=None)):
        bearer=_token(authorization)
        from app.core.rate_limit import check_login
        check_login(request)
        # Existing rate limiter and signed account identity, plus a private
        # per-operator TOTP secret file. No browser-stored TOTP secrets.
        try:
            from app.persistence import strategy_registry_service as registry
            actor=registry._identity(bearer)
            if actor.role!="admin": raise PermissionError("only admin may step up")
            proof=offline_security.issue_proof(bearer,body.otp)
        except (PermissionError,ValueError,KeyError) as exc:
            raise HTTPException(status_code=403,detail="MFA verification refused") from exc
        return {"proof":proof,"expires_in_seconds":offline_security.TTL,
                "scope":"offline_observability_admin_only",
                "execution_authorized":False}

    # Mount exactly seven evidence route groups, no dynamic imports from main.py.
    from app.api.v1.endpoints.offline_validation_journal import router as journal
    from app.api.v1.endpoints.offline_performance import router as analytics
    from app.api.v1.endpoints.offline_observability import router as status
    from app.api.v1.endpoints.offline_alerts import router as alerts
    for router in (journal,analytics,status,alerts):
        app.include_router(router,prefix="/api/v1")

    @app.get("/api/v1/offline-validation/strategies/{strategy_id}/health-history")
    def offline_health_history(strategy_id:str,limit:int=50,
                               authorization:str|None=Header(default=None)):
        bearer=_token(authorization)
        if not 1<=limit<=100: raise HTTPException(status_code=422,detail="Invalid history limit")
        from app.persistence.offline_health_service import OfflineHealthRecorder
        from app.persistence.session import make_session_factory
        engine=make_engine()
        try:
            return OfflineHealthRecorder(make_session_factory(engine)).read(
                access_token=bearer,strategy_id=strategy_id,limit=limit)
        except (LookupError,PermissionError) as exc:
            raise HTTPException(status_code=403,detail="Offline health evidence refused") from exc
        finally: engine.dispose()

    @app.get("/healthz")
    def liveness():
        return {"application":"offline-operator-observability","alive":True,
                "broker":"not_configured","execution_authorized":False}

    @app.get("/readyz")
    def readiness(response:Response):
        try:
            engine=make_engine()
            try:
                with engine.connect() as db:
                    db.execute(text("SELECT 1")).scalar_one()
            finally:
                engine.dispose()
        except Exception:
            response.status_code=503
            return {"ready":False,"database":"unavailable",
                    "broker":"not_connected","execution_authorized":False}
        return {"ready":True,"database":"connected",
                "broker":"not_connected","worker":"disabled",
                "market_data":"not_probed","queue":"not_probed",
                "alerts":"not_dispatched_externally","execution_authorized":False}

    @app.get("/api/v1/offline-validation/monitor")
    def monitoring():
        # Authenticated by the same strict step-up middleware. No broker probe.
        return {"read_only":True,"execution_authorized":False,
                "app":"offline_observation_only",
                "broker":{"status":"not_connected","has_execution_authority":False},
                "market_data":{"status":"not_independently_probed"},
                "worker":{"status":"disabled"},
                "queue":{"status":"query_strategy_reconciliation_for_evidence"},
                "alerts":{"status":"no_real_external_delivery_proof"},
                "operator_session":{"status":"signed_access_token_required"},
                "mfa":{"admin":"step_up_required"}}
    return app

app=create_app()
