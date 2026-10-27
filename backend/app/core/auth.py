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
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, status

from app.core import runtime_env, tokens
from app.services.simulation import identity as _sim

DEFAULT_USER_ID = "default"
USER_ID_HEADER = "X-User-Id"
#: ``X-Sterling-View: simulation`` (or ``?view=simulation`` on stream URLs, which cannot carry
#: headers) points every route at the owner's virtual Simulation tenant instead of the live one.
VIEW_HEADER = "X-Sterling-View"
VIEW_QUERY = "view"
VIEW_SIMULATION = "simulation"


@dataclass(frozen=True)
class UserContext:
    user_id: str
    username: str = ""
    role: str = "user"
    #: ``"simulation"`` when the request is scoped to the virtual tenant (``user_id`` is then
    #: the sim uid and ``owner_uid`` the real owner behind it); empty for the live view.
    view: str = ""
    owner_uid: str = ""


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
        try:
            from pathlib import Path
            import json
            status_file = Path(__file__).resolve().parents[3] / ".runtime" / "stack" / "status.json"
            if status_file.is_file():
                data = json.loads(status_file.read_text("utf-8"))
                for key in ("app_url", "tunnel_url", "public_url"):
                    val = data.get(key)
                    if val:
                        h = (urlsplit(str(val).strip()).hostname or "").lower()
                        if h:
                            hosts.add(h)
        except Exception:
            pass
        return hosts
    except Exception:  # noqa: BLE001
        return set()


def _is_private_ip(name: str) -> bool:
    try:
        ip = ipaddress.ip_address(name)
        if getattr(ip, "ipv4_mapped", None) is not None:
            ip = ip.ipv4_mapped
        return ip.is_private
    except ValueError:
        return False


def _is_recognized_host(name: str, configured: set[str]) -> bool:
    name = (name or "").strip().lower()
    if not name:
        return False
    return (
        _is_loopback_name(name)
        or name in configured
        or name.endswith(".trycloudflare.com")
        or _is_private_ip(name)
    )


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
    if host and not _is_recognized_host(host, configured):
        return (
            status.HTTP_401_UNAUTHORIZED,
            "Authentication required (unrecognised Host header)",
        )

    origin = (origin or "").strip()
    if origin and (method.upper() not in _SAFE_METHODS):
        origin_host = (urlsplit(origin).hostname or "").lower()
        if not origin_host or not _is_recognized_host(origin_host, configured):
            return (status.HTTP_403_FORBIDDEN, "Cross-origin request refused")
    return None


# ─── Simulation view ─────────────────────────────────────────────────────────
#
# In the Simulation view the same boards, positions and config screens show the virtual
# tenant. Anything that acts on GLOBAL or BROKER state must not be reachable from it: the
# virtual tenant may run, trade and square off its own book, but it may never flip the live
# kill switch, resume SAFE_MODE, log a broker in or out, edit broker accounts, or change the
# live daily-loss limit. Those routes refuse with 403 here, centrally, so a route added later
# that forgets about the simulation is still covered by ``get_current_user``.
SIM_VIEW_DENIED_MESSAGE = "Not available in Simulation view"
_ANY = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
_WRITES = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: (methods, path regex under the API). First match denies.
SIM_VIEW_DENY_RULES: tuple[tuple[frozenset, "re.Pattern[str]"], ...] = tuple(
    (methods, re.compile(pattern))
    for methods, pattern in (
        # the process-wide halt, and the emergency action that engages it
        (_WRITES, r"^/api/v1/kite/trading/kill-switch/?$"),
        (_WRITES, r"^/api/v1/kite/engine/emergency-halt/?$"),
        # SAFE_MODE and every operator/ops write
        (_WRITES, r"^/api/v1/ops(/|$)"),
        (_WRITES, r"^/api/v1/operator(/|$)"),
        (_WRITES, r"^/api/v1/snapback/family(/|$)"),
        (_WRITES, r"^/api/v1/continuity(/|$)"),
        # broker accounts, sessions, login and logout
        (_WRITES, r"^/api/v1/kite/accounts(/|$)"),
        (_ANY, r"^/api/v1/kite/(login-url|callback|session|logout)(/|$)"),
        (_ANY, r"^/api/v1/kite/holdings/authorise/?$"),
        (_WRITES, r"^/api/v1/truedata(/|$)"),
        # the live daily-loss limit and the process-wide retry queue
        (_WRITES, r"^/api/v1/kite/risk(/|$)"),
        # alert destinations belong to the owner's chat, not to the virtual tenant
        (_ANY, r"^/api/v1/kite/telegram(/|$)"),
        # user and token management
        (_WRITES, r"^/api/v1/auth(/|$)"),
        # global data-lake root, and the retired Opening Leaders order path
        (_WRITES, r"^/api/v1/datalake(/|$)"),
        (_WRITES, r"^/api/v1/opening-volume-leaders/execute/?$"),
    )
)


def simulation_denied(method: str, path: str) -> bool:
    """Whether ``method path`` is unavailable in the Simulation view."""
    verb = (method or "").upper()
    target = re.sub(r"/{2,}", "/", path or "")
    return any(verb in methods and rule.search(target) for methods, rule in SIM_VIEW_DENY_RULES)


def requested_view(request: Request) -> str:
    """``"simulation"`` or ``""`` (live). Anything else is a client error, not a silent live view."""
    # EventSource and WebSocket cannot set headers, so a GET may carry the view in the URL;
    # anything that changes state must say so in the header.
    query = request.query_params.get(VIEW_QUERY) if request.method.upper() == "GET" else ""
    raw = (request.headers.get(VIEW_HEADER) or query or "").strip().lower()
    if raw in ("", "live"):
        return ""
    if raw == VIEW_SIMULATION:
        return VIEW_SIMULATION
    raise HTTPException(status_code=400, detail=f"Unknown view {raw!r}")


def _owner_uid() -> str:
    from app.services.simulation.tenant import configured_owner_uid

    return configured_owner_uid()


def scope_to_simulation(ctx: UserContext) -> UserContext:
    """The simulation view of the owner ``ctx``; 403 for anyone else.

    Only the owner (the administrator whose id is the configured owner) may look at, and act
    inside, the virtual tenant: it shares the owner's broker data feed and chat.
    """
    if ctx.role != "admin" or ctx.user_id != _owner_uid():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Simulation view is available to the owner only")
    return UserContext(user_id=_sim.sim_uid(ctx.user_id), username=ctx.username, role=ctx.role,
                       view=VIEW_SIMULATION, owner_uid=ctx.user_id)


def _refuse_reserved_identity(user_id: str) -> None:
    """A login or header can never BE the virtual tenant: that identity is reachable only
    through the view switch, which applies the deny list and the owner check."""
    if _sim.is_sim_uid(user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Reserved identity")


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
        token = _bearer(request)
        if token:
            from app.services.auth import login_activity
            claims = tokens.decode(token,expected_typ='access')
            login_activity.record(ctx.owner_uid or ctx.user_id,int(claims.get('ver',0)),request,expires_ms=int(claims['exp'])*1000)
    except Exception:  # noqa: BLE001 - audit enrichment must never break auth
        pass
    return ctx


async def _authenticate(request: Request) -> UserContext:
    """Who is calling (always a REAL identity; the view is applied afterwards)."""
    token = _bearer(request)
    if token:
        return _context_from_access_token(token)

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
    _refuse_reserved_identity(uid)
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


async def get_current_user(request: Request) -> UserContext:
    """Return the authenticated caller, scoped to the requested view.

    Production requires a valid Bearer access JWT and never trusts X-User-Id.
    Development/test still accepts the legacy header only when no bearer token
    was supplied, preserving local tooling without leaving a production bypass.

    With ``X-Sterling-View: simulation`` the returned context IS the owner's virtual tenant
    (``user_id == sim:<owner>``), so every existing route, board and stream shows and acts on
    the simulation without knowing about it; routes that act on global or broker state are
    refused (403) there. Without the header nothing changes.
    """
    ctx = await _authenticate(request)
    _refuse_reserved_identity(ctx.user_id)
    if requested_view(request) == VIEW_SIMULATION:
        if simulation_denied(request.method, request.url.path):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=SIM_VIEW_DENIED_MESSAGE)
        ctx = scope_to_simulation(ctx)
    return _remember(request, ctx)


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
