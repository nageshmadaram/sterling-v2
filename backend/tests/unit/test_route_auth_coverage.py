"""Every route is authenticated unless it is on this short, reviewed allow-list.

A new endpoint added without ``Depends(get_current_user)`` fails here, so the
broker-touching / state-changing surface cannot grow an unauthenticated door by
accident. Reads are included: replay, signal and position tables are tenant data.
"""
from __future__ import annotations

import importlib
import pkgutil

from fastapi.routing import APIRoute

from app.api.v1 import endpoints
from app.core.auth import get_current_user, require_admin
from app.services.notifications.telegram_webapp_auth import tma_user

#: (METHOD, path) -> why it is deliberately open.
PUBLIC = {
    ("GET", "/media/{feature}/document"): "finite media-only cookie; current user/version/feature validated in handler",
    ("GET", "/tma"): "generic Mini App shell; private data uses signed Telegram authentication",
    ("GET", "/api/v1/tma"): "generic Mini App shell alias",
    ("GET", "/tma/operator"): "generic Sterling login form; management requires signed authentication",
    ("POST", "/auth/login"): "obtains the token; rate limited",
    ("POST", "/auth/refresh"): "exchanges a refresh token; rate limited",
    ("GET", "/health"): "liveness for load balancers",
    ("GET", "/health/live"): "process liveness, no I/O",
    ("GET", "/health/ready"): "readiness probe for the supervisor",
    ("GET", "/snapback/prospective/health"): "supervisor health probe",
    ("GET", "/ops/runtime"): "start script probe; loopback-only inside the handler",
    ("GET", "/kite/callback"): "Kite OAuth redirect; signed state is mandatory",
    ("POST", "/kite/postback"): "Kite server push; sha256 checksum verified per account",
    ("GET", "/backtest/unified/strategies"): "static catalogue",
    ("GET", "/backtest/unified/presets"): "static catalogue",
    ("GET", "/kite/engine/stock-registry"): "static catalogue",
    ("GET", "/opening-volume-leaders/contract"): "static contract description",
}


def _has_auth(dependant) -> bool:
    pending = list(getattr(dependant, "dependencies", ()) or ())
    while pending:
        dep = pending.pop()
        if getattr(dep, "call", None) in (get_current_user, require_admin, tma_user):
            return True
        pending.extend(list(getattr(dep, "dependencies", ()) or ()))
    return False


def _routes():
    for info in pkgutil.iter_modules(endpoints.__path__):
        module = importlib.import_module(f"{endpoints.__name__}.{info.name}")
        for name, value in vars(module).items():
            if name.endswith("router") and hasattr(value, "routes"):
                for route in value.routes:
                    if isinstance(route, APIRoute):
                        yield info.name, route


def test_every_route_requires_auth_unless_allow_listed():
    seen = 0
    unauthenticated = []
    for module, route in _routes():
        seen += 1
        if _has_auth(route.dependant):
            continue
        for method in sorted((route.methods or set()) - {"HEAD", "OPTIONS"}):
            if (method, route.path) not in PUBLIC:
                unauthenticated.append(f"{method} {route.path} ({module})")
    assert seen > 200, "route discovery found too few routes - the guard would pass vacuously"
    assert not unauthenticated, "routes without auth: " + ", ".join(sorted(unauthenticated))


def test_public_allow_list_has_no_stale_entries():
    present = set()
    for _module, route in _routes():
        for method in (route.methods or set()):
            present.add((method, route.path))
    stale = sorted(set(PUBLIC) - present)
    assert not stale, f"allow-listed routes that no longer exist: {stale}"


def test_trading_control_routes_are_authenticated():
    wanted = {
        ("POST", "/kite/orders"), ("PUT", "/kite/orders/{order_id}"),
        ("DELETE", "/kite/orders/{order_id}"), ("POST", "/kite/trading/kill-switch"),
        ("POST", "/kite/engine/kite/engine/emergency-square-off"),
        ("POST", "/kite/engine/kite/engine/emergency-halt"),
        ("POST", "/ops/safe-mode/resume"), ("GET", "/ops/live-status"),
        ("POST", "/kite/accounts"), ("POST", "/kite/session"),
    }
    authed = set()
    for _module, route in _routes():
        if _has_auth(route.dependant):
            for method in (route.methods or set()):
                authed.add((method, route.path))
    # Routers declare their own prefix inside route.path, so compare by suffix.
    missing = sorted(
        w for w in wanted
        if not any(m == w[0] and p.endswith(w[1].replace("/kite/engine", "")) for m, p in authed)
    )
    assert not missing, f"trading-control routes lost their auth dependency: {missing}"


def test_filesystem_touching_datalake_routes_require_admin():
    admin_only = {
        ("GET", "/datalake/browse"), ("POST", "/datalake/root"),
        ("POST", "/datalake/root/activate"), ("DELETE", "/datalake/root/{lake_id}"),
    }
    found = set()
    for module, route in _routes():
        if module != "datalake":
            continue
        pending = list(route.dependant.dependencies)
        while pending:
            dep = pending.pop()
            if dep.call is require_admin:
                for method in route.methods or set():
                    found.add((method, route.path))
            pending.extend(dep.dependencies)
    normalised = {(m, p.replace("/datalake", "", 1) if p.startswith("/datalake/datalake") else p) for m, p in found}
    for method, path in admin_only:
        assert any(m == method and p.endswith(path.replace("/datalake", "", 1)) for m, p in normalised), (method, path)
