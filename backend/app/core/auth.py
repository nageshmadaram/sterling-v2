"""
Authenticated user context.

Production trusts only a verified Sterling access JWT. The historical
X-User-Id seam remains available in development/test so the local single-user
workbench and existing integration fixtures do not need a fake identity provider,
but it is deliberately ignored in production.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from app.core import tokens

DEFAULT_USER_ID = "default"
USER_ID_HEADER = "X-User-Id"


@dataclass(frozen=True)
class UserContext:
    user_id: str
    username: str = ""
    role: str = "user"


def _production() -> bool:
    return (os.environ.get("ENVIRONMENT", "development") or "development").strip().lower() == "production"


def _unauthorized(detail: str = "Authentication required") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _bearer(request: Request) -> str:
    header = (request.headers.get("Authorization") or "").strip()
    if not header:
        return ""
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        raise _unauthorized("Invalid Authorization header")
    return value.strip()


def _context_from_access_token(token: str) -> UserContext:
    try:
        claims = tokens.decode(token, expected_typ="access")
    except tokens.TokenError as exc:
        raise _unauthorized("Invalid or expired access token") from exc

    from app.services.auth import user_store

    # Tests may call a dependency without entering the application lifespan;
    # bootstrap is idempotent and keeps the token-version check authoritative.
    user_store.bootstrap()
    user = user_store.get_by_id(str(claims.get("sub") or ""))
    if user is None or not user.is_active:
        raise _unauthorized("User is inactive or does not exist")
    if int(claims.get("ver", -1)) != int(user.token_version):
        raise _unauthorized("Access token has been revoked")

    return UserContext(user_id=user.id, username=user.username, role=user.role)


async def get_current_user(request: Request) -> UserContext:
    """Return the authenticated caller.

    Production requires a valid Bearer access JWT and never trusts X-User-Id.
    Development/test still accepts the legacy header only when no bearer token
    was supplied, preserving local tooling without leaving a production bypass.
    """
    token = _bearer(request)
    if token:
        return _context_from_access_token(token)

    if _production():
        raise _unauthorized()

    uid = (request.headers.get(USER_ID_HEADER) or "").strip() or DEFAULT_USER_ID
    try:
        from app.services.auth import user_store

        user_store.bootstrap()
        user = user_store.get_by_id(uid)
        if user is not None and user.is_active:
            return UserContext(user_id=user.id, username=user.username, role=user.role)
    except Exception:
        # Development/test compatibility only. Production never reaches this path.
        pass
    return UserContext(
        user_id=uid,
        username=uid,
        role="admin" if uid == DEFAULT_USER_ID else "user",
    )
