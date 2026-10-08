"""
Authenticated user context.

Production trusts only a verified Sterling access JWT. The historical
X-User-Id seam remains available in development/test so the local single-user
workbench and existing integration fixtures do not need a fake identity provider,
but it is deliberately ignored in production.
"""
from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, status

from app.core import runtime_env, tokens

DEFAULT_USER_ID = "default"
USER_ID_HEADER = "X-User-Id"


@dataclass(frozen=True)
class UserContext:
    user_id: str
    username: str = ""
    role: str = "user"


def _production() -> bool:
    # Reads backend/.env as well as the process environment (app.core.runtime_env):
    # the launch script does not export ENVIRONMENT, and a production flag that only
    # some code could see left the development identity seam open.
    return runtime_env.is_production()


# ─── Development identity seam: loopback only ────────────────────────────────
#
# Outside production an unauthenticated caller is the local single operator
# ("default", admin). That is only safe if "unauthenticated caller" can only mean
# "a process on this machine, driven by this operator". A backend started with
# `--host 0.0.0.0` (or reached through DNS rebinding or a cross-site request from
# the operator's own browser) broke that assumption and exposed orders, the kill
# switch and config writes to everything on the network.
_LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1", "testserver", "testclient", "test"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
ALLOW_REMOTE_DEV_ENV = "STERLING_DEV_AUTH_ALLOW_REMOTE"
ALLOW_ANONYMOUS_LIVE_ENV = "STERLING_DEV_AUTH_ALLOW_ANONYMOUS_LIVE"


def _env_flag(name: str) -> bool:
    return (os.environ.get(name, "") or "").strip().lower() in {"1", "true", "yes", "on"}


def _live_account_exists() -> bool:
    """Whether any stored Kite account (any tenant) is a LIVE, non-paper account.

    Fails closed: if the store cannot be read, assume a live account exists, so an
    unreadable account list can never be the reason an anonymous caller is let in.
    """
    try:
        from app.services.exchanges.kite import accounts as kite_accounts

        return any(getattr(a, "is_paper", True) is False for a in kite_accounts.all_accounts())
    except Exception:  # noqa: BLE001
        return True


def _remote_dev_auth_allowed() -> bool:
    return _env_flag(ALLOW_REMOTE_DEV_ENV)


def _configured_origin_hosts() -> set[str]:
    try:
        from app.core.config import settings

        hosts = set()
        for origin in settings.cors_origins or []:
            host = (urlsplit(str(origin).strip()).hostname or "").lower()
            if host:
                hosts.add(host)
        return hosts
    except Exception:  # noqa: BLE001
        return set()


def _is_loopback_name(name: str) -> bool:
    name = (name or "").strip().strip("[]").lower()
    if not name:
        return False
    if name in _LOOPBACK_NAMES or name.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None) is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback


def _host_header_name(value: str) -> str:
    value = (value or "").strip()
    if value.startswith("["):  # [::1]:8000
        return value[1:].split("]", 1)[0]
    if value.count(":") == 1:  # host:port
        return value.split(":", 1)[0]
    return value  # bare name, or an unbracketed IPv6 literal


def dev_identity_refusal(
    *, peer: Optional[str], host_header: str, origin: str, method: str
) -> Optional[tuple[int, str]]:
    """Why the development header identity must NOT be honoured, or ``None``.

    ``method`` is the HTTP method, or ``"WEBSOCKET"`` (always origin-checked: a
    browser sends ``Origin`` on every WebSocket handshake and the socket carries
    live order updates).
    """
    # The anonymous development identity is for paper work. Once a LIVE broker
    # account is stored, the same identity could place orders, flip the kill
    # switch and rewrite config with real money behind it, so a signed-in
    # operator is required whatever ENVIRONMENT says. (Deliberate opt-out for
    # throw-away fixtures only: STERLING_DEV_AUTH_ALLOW_ANONYMOUS_LIVE=1.)
    if not _env_flag(ALLOW_ANONYMOUS_LIVE_ENV) and _live_account_exists():
        return (
            status.HTTP_401_UNAUTHORIZED,
            "Authentication required: a live broker account is configured, so the "
            "anonymous development identity is disabled. Sign in as the operator.",
        )

    if _remote_dev_auth_allowed():
        return None

    if peer and not _is_loopback_name(peer):
        return (
            status.HTTP_401_UNAUTHORIZED,
            "Authentication required (the unauthenticated development identity "
            "is limited to loopback clients)",
        )

    configured = _configured_origin_hosts()
    host = _host_header_name(host_header).lower()
    if host and not _is_loopback_name(host) and host not in configured:
        return (
            status.HTTP_401_UNAUTHORIZED,
            "Authentication required (unrecognised Host header)",
        )

    origin = (origin or "").strip()
    if origin and (method.upper() not in _SAFE_METHODS):
        origin_host = (urlsplit(origin).hostname or "").lower()
        if not origin_host or not (_is_loopback_name(origin_host) or origin_host in configured):
            return (status.HTTP_403_FORBIDDEN, "Cross-origin request refused")
    return None


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


def _remember(request: Request, ctx: UserContext) -> UserContext:
    """Expose the resolved identity to the operator audit middleware."""
    try:
        request.state.sterling_user_id = ctx.user_id
        request.state.sterling_username = ctx.username
    except Exception:  # noqa: BLE001 - audit enrichment must never break auth
        pass
    return ctx


async def get_current_user(request: Request) -> UserContext:
    """Return the authenticated caller.

    Production requires a valid Bearer access JWT and never trusts X-User-Id.
    Development/test still accepts the legacy header only when no bearer token
    was supplied, preserving local tooling without leaving a production bypass.
    """
    token = _bearer(request)
    if token:
        return _remember(request, _context_from_access_token(token))

    if _production():
        raise _unauthorized()

    refusal = dev_identity_refusal(
        peer=request.client.host if request.client else None,
        host_header=request.headers.get("host", ""),
        origin=request.headers.get("origin", ""),
        method=request.method,
    )
    if refusal is not None:
        code, detail = refusal
        raise HTTPException(
            status_code=code,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"} if code == status.HTTP_401_UNAUTHORIZED else None,
        )

    uid = (request.headers.get(USER_ID_HEADER) or "").strip() or DEFAULT_USER_ID
    try:
        from app.services.auth import user_store

        user_store.bootstrap()
        user = user_store.get_by_id(uid)
        if user is not None and user.is_active:
            return _remember(request, UserContext(user_id=user.id, username=user.username, role=user.role))
    except Exception:
        # Development/test compatibility only. Production never reaches this path.
        pass
    return _remember(request, UserContext(
        user_id=uid,
        username=uid,
        role="admin" if uid == DEFAULT_USER_ID else "user",
    ))


async def require_admin(
    user: UserContext = Depends(get_current_user),
) -> UserContext:
    """Require administrator authority for deployment-global controls."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator privileges required",
        )
    return user
