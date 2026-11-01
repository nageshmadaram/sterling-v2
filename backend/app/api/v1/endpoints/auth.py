"""
Authentication endpoints — OAuth2 password flow issuing signed JWTs.

* ``POST /api/v1/auth/login``   — OAuth2PasswordRequestForm → access + refresh
* ``POST /api/v1/auth/refresh`` — refresh token → fresh access token
* ``POST /api/v1/auth/logout``  — bump token_version (invalidate all tokens)
* ``GET  /api/v1/auth/me``      — current identity

Login is rate-limited (brute-force guard, all environments) and does a dummy
password verify on the user-miss path so a bad username costs the same time as a
bad password (no user enumeration via timing).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from app.core import tokens
from app.core.auth import UserContext, get_current_user
from app.core.passwords import hash_password, verify_password
from app.core.rate_limit import check_login, check_refresh
from app.core.logging import get_logger
from app.services.auth import user_store

router = APIRouter(prefix="/auth", tags=["auth"])
from .sterling_users import router as users_router
router.include_router(users_router)
from .ui_preferences import router as preferences_router
router.include_router(preferences_router)
from .profile import router as profile_router
router.include_router(profile_router)
from .push import router as push_router
router.include_router(push_router)
from .feed_test import router as feed_test_router
router.include_router(feed_test_router)
log = get_logger(__name__)


def _peer(request: Request) -> str:
    return request.client.host if request.client else "unknown"

# A real Argon2id hash of a random string, computed once at import. Verifying
# against it on the user-miss path spends ~the same time as a real check, so a
# nonexistent username can't be distinguished from a wrong password by timing.
import secrets as _secrets
_DUMMY_HASH = hash_password(_secrets.token_urlsafe(32))


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class MeResponse(BaseModel):
    user_id: str
    username: str
    role: str
    features: dict[str, bool]
    visibility: dict[str, bool]
    policy_revision: int
    administrator: bool


@router.post("/login", response_model=TokenResponse)
async def login(request: Request, form: OAuth2PasswordRequestForm = Depends()) -> TokenResponse:
    check_login(request)
    user = user_store.get_by_username(form.username)
    if user is None or not user.is_active:
        # Spend the verify time anyway, then fail identically to a wrong password.
        verify_password(form.password, _DUMMY_HASH)
        log.warning("login failed (unknown or inactive user) user=%.64r peer=%s", form.username, _peer(request))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not verify_password(form.password, user.password_hash):
        log.warning("login failed (bad password) user=%.64r peer=%s", form.username, _peer(request))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    log.info("login ok user=%.64r peer=%s", user.username, _peer(request))
    from app.services.auth import login_activity
    import time
    login_activity.record(user.id,user.token_version,request,login=True,expires_ms=int(time.time()*1000)+tokens.ACCESS_TTL_S*1000)
    return TokenResponse(
        access_token=tokens.mint_access(user.id, user.username, user.role, user.token_version),
        refresh_token=tokens.mint_refresh(user.id, user.username, user.role, user.token_version),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(request: Request, body: RefreshRequest) -> TokenResponse:
    check_refresh(request)
    try:
        claims = tokens.decode(body.refresh_token, expected_typ="refresh")
    except tokens.TokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = user_store.get_by_id(claims.get("sub") or "")
    if user is None or not user.is_active or int(claims.get("ver", -1)) != int(user.token_version):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenResponse(
        access_token=tokens.mint_access(user.id, user.username, user.role, user.token_version),
        refresh_token=tokens.mint_refresh(user.id, user.username, user.role, user.token_version, remembered=bool(claims.get("remembered"))),
    )


@router.post("/remember", response_model=TokenResponse)
async def remember(user: UserContext = Depends(get_current_user)) -> TokenResponse:
    """Remember this authenticated operator device; logout/version revocation remains authoritative."""
    stored = user_store.get_by_id(user.user_id)
    if stored is None or not stored.is_active:
        raise HTTPException(status_code=401, detail="Invalid session")
    return TokenResponse(access_token=tokens.mint_access(stored.id,stored.username,stored.role,stored.token_version),refresh_token=tokens.mint_refresh(stored.id,stored.username,stored.role,stored.token_version,remembered=True))


@router.post("/logout")
async def logout(user: UserContext = Depends(get_current_user)) -> dict:
    """Invalidate every outstanding token for the caller (logout everywhere)."""
    user_store.bump_token_version(user.user_id)
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
async def me(response: Response, user: UserContext = Depends(get_current_user)) -> MeResponse:
    from app.services.auth import account_policy
    response.headers['Cache-Control'] = 'no-store, private'
    try:
        policy = account_policy.read(user.user_id)
    except account_policy.PolicyUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    return MeResponse(user_id=user.user_id, username=user.username, role=policy.role,
                      features=policy.features, visibility=policy.visibility, policy_revision=policy.revision,
                      administrator=policy.administrator)
