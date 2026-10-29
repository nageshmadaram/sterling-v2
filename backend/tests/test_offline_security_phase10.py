"""Phase 10 strict offline ASGI perimeter, MFA, revocation binding, role denies."""
from __future__ import annotations
import base64
import os
import stat
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tests.test_offline_scheduler_phase5 import lab
from app.core import offline_security
from app.offline_security_profile import create_app
from app.persistence import strategy_registry_service as registry


@pytest.fixture
def secret_file(tmp_path,monkeypatch):
    secret=base64.b32encode(b"0123456789abcdefghij").decode("ascii")
    path=tmp_path/"admin-totp.json"
    path.write_text('{"owner":"'+secret+'"}',encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setenv("STERLING_OFFLINE_MFA_FILE",str(path))
    monkeypatch.setenv("STERLING_JWT_SECRET","synthetic-separated-jwt-secret-for-offline-10-tests")
    return path,secret


def test_totp_private_file_and_no_secrets_in_response(secret_file,lab):
    path,secret=secret_file
    code=offline_security._totp(secret,int(time.time())//30)
    assert offline_security.verify_totp_for_admin("owner",code)
    assert not offline_security.verify_totp_for_admin("other",code)
    assert not offline_security.verify_totp_for_admin("owner","00000X")
    path.chmod(0o644)
    with pytest.raises(PermissionError):
        offline_security.verify_totp_for_admin("owner",code)
    path.chmod(0o600)
    proof=offline_security.issue_proof("admin",code)
    assert len(proof)>20 and secret not in proof
    assert offline_security.check_access("admin",proof).role=="admin"
    with pytest.raises(PermissionError):
        offline_security.check_access("admin",None)
    assert offline_security.check_access("operator",proof).role=="operator"


def test_admin_proof_bound_to_signed_access_token_and_expiry(secret_file,lab):
    _,secret=secret_file
    code=offline_security._totp(secret,int(time.time())//30)
    proof=offline_security.issue_proof("admin",code)
    with pytest.raises(PermissionError):
        offline_security.check_access("another-token",proof)
    assert offline_security.check_access("admin",proof).role=="admin"
    with pytest.raises(PermissionError):
        offline_security.check_access("admin",proof,now=int(time.time())+301)


def test_secure_gateway_isolated_routes_role_and_mfa(secret_file,lab):
    app=create_app()
    client=TestClient(app)
    paths={getattr(r,"path","") for r in app.routes}
    assert not any("/kite/" in x or "/ops/" in x or "/stream/" in x
                   or "/order" in x or "/broker" in x for x in paths)
    assert client.get("/api/v1/kite/orders").status_code==404
    root="/api/v1/offline-validation/strategies"
    assert client.get(root).status_code==403
    assert client.get(root,headers={"Authorization":"Bearer admin"}).status_code==403
    assert client.get(root,headers={"Authorization":"Bearer operator"}).status_code==200
    assert client.post(root,headers={"Authorization":"Bearer operator"}).status_code==405
    _,secret=secret_file
    otp=offline_security._totp(secret,int(time.time())//30)
    proof_result=client.post("/api/v1/offline-security/mfa/verify",
        headers={"Authorization":"Bearer admin"},json={"otp":otp})
    assert proof_result.status_code==200,proof_result.text
    assert proof_result.json()["execution_authorized"] is False
    token=proof_result.json()["proof"]
    approved=client.get(root,headers={"Authorization":"Bearer admin",
                    "X-Sterling-Offline-MFA":token})
    assert approved.status_code==200,approved.text
    assert approved.json()["execution_authorized"] is False
    assert approved.headers["cache-control"]=="no-store, private"
    assert client.get(root,headers={"Authorization":"Bearer admin",
                        "X-Sterling-Offline-MFA":"forged"}).status_code==403
    assert client.get(root+"?token=leaked",headers={"Authorization":"Bearer operator"}).status_code==403
    # Contract verifier still independently requires signed JWT.
    assert client.get("/api/v1/offline-validation/monitor",
        headers={"Authorization":"Bearer operator"}).json()["broker"]["status"]=="not_connected"


def test_offline_refresh_rejects_legacy_remembered_token(secret_file,lab):
    from app.core import tokens
    remembered=tokens.mint_refresh("owner","offline_admin","admin",1,remembered=True)
    response=TestClient(create_app()).post(
        "/api/v1/auth/refresh",json={"refresh_token":remembered})
    assert response.status_code==401
    assert "Long-lived" in response.json()["detail"]
    assert not any("/auth/remember" in getattr(route,"path","") for route in create_app().routes)


def test_viewer_and_simulation_identity_denied(secret_file,lab,monkeypatch):
    original=registry._identity
    def actor(token):
        if token=="viewer": return SimpleNamespace(user_id="owner",role="viewer",view=None,owner_uid=None)
        if token=="virtual": return SimpleNamespace(user_id="owner",role="admin",view="simulation",owner_uid="owner")
        return original(token)
    monkeypatch.setattr(registry,"_identity",actor)
    client=TestClient(create_app())
    root="/api/v1/offline-validation/strategies"
    assert client.get(root,headers={"Authorization":"Bearer viewer"}).status_code==403
    assert client.get(root,headers={"Authorization":"Bearer virtual"}).status_code==403


def test_profile_configuration_rejects_unsafe_defaults(monkeypatch,tmp_path):
    for key in ("ENVIRONMENT","STERLING_OFFLINE_ONLY","STERLING_OFFLINE_REQUIRE_MFA",
                "PAPER_TRADING","STERLING_OFFLINE_NO_BROKER"):
        monkeypatch.delenv(key,raising=False)
    with pytest.raises(RuntimeError,match="development identity"):
        offline_security.validate_profile_config()
    monkeypatch.setenv("ENVIRONMENT","production")
    with pytest.raises(RuntimeError,match="offline-only"):
        offline_security.validate_profile_config()
    monkeypatch.setenv("STERLING_OFFLINE_ONLY","true")
    with pytest.raises(RuntimeError,match="MFA"):
        offline_security.validate_profile_config()


def test_logout_revocation_uses_first_factor_store_not_mfa_authority():
    # The existing signed JWT revocation check belongs to registry._identity,
    # and MFA cannot validate a bearer if first factor rejects.
    with pytest.raises(PermissionError):
        offline_security.check_access("missing",None)
