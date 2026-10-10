"""Simulation view: ``X-Sterling-View: simulation`` scopes every request to the virtual tenant,
for the owner only, and routes that act on global or broker state refuse (403) there."""
from __future__ import annotations

import pytest
from fastapi.routing import APIRoute
from starlette.requests import Request
from starlette.testclient import TestClient
from tenancy_support import OWNER, SIM_ACCOUNT_ID, SIM_UID, install_fake_sim_client, live_account

import main
from app.api.v1.endpoints import kite, kite_engine, kite_telegram, navigator
from app.core import auth, tokens
from app.services import live_safety
from app.services.auth import user_store
from app.services.exchanges.kite import accounts as kite_accounts
from app.services.kite_engine import state as engine_state

VIEW = {"X-Sterling-View": "simulation"}
DENIED = "Not available in Simulation view"


@pytest.fixture
def client():
    return TestClient(main.app, raise_server_exceptions=False)


@pytest.fixture
def tenants():
    live = live_account()
    sim = kite_accounts.ensure_sim_account(OWNER)
    return live, sim


def _request(method="GET", path="/api/v1/kite/orders", headers=None, query=""):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    raw.append((b"host", b"testserver"))
    scope = {"type": "http", "method": method, "path": path, "raw_path": path.encode(),
             "query_string": query.encode(), "headers": raw, "client": ("testclient", 5000),
             "server": ("testserver", 80), "scheme": "http", "root_path": "", "state": {}}
    return Request(scope)


# ── scoping ──────────────────────────────────────────────────────────────────
async def test_without_the_header_the_request_is_the_live_owner():
    ctx = await auth.get_current_user(_request())
    assert (ctx.user_id, ctx.role, ctx.view, ctx.owner_uid) == (OWNER, "admin", "", "")


async def test_the_header_scopes_the_owner_to_the_virtual_tenant():
    req = _request(headers=VIEW)
    ctx = await auth.get_current_user(req)
    assert (ctx.user_id, ctx.view, ctx.owner_uid, ctx.role) == (SIM_UID, "simulation", OWNER, "admin")
    assert req.state.sterling_user_id == SIM_UID            # the operator audit trail names the tenant


async def test_query_view_works_for_a_get_only():
    assert (await auth.get_current_user(_request(query="view=simulation"))).user_id == SIM_UID
    assert (await auth.get_current_user(_request("POST", query="view=simulation"))).user_id == OWNER


async def test_header_wins_and_live_is_spelled_live():
    assert (await auth.get_current_user(_request(headers={"X-Sterling-View": "live"}))).user_id == OWNER
    assert (await auth.get_current_user(_request(headers=VIEW, query="view=live"))).user_id == SIM_UID


async def test_an_unknown_view_is_a_client_error_not_a_silent_live_view():
    with pytest.raises(auth.HTTPException) as err:
        await auth.get_current_user(_request(headers={"X-Sterling-View": "paper"}))
    assert err.value.status_code == 400


async def test_only_the_owner_may_enter_the_simulation_view():
    with pytest.raises(auth.HTTPException) as err:
        await auth.get_current_user(_request(headers={**VIEW, "X-User-Id": "someone-else"}))
    assert err.value.status_code == 403 and "owner" in err.value.detail


async def test_an_admin_who_is_not_the_owner_is_refused(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    user_store.clear()
    user_store._loaded = True
    user_store._users["boss"] = user_store._User(id="boss", username="boss", role="admin", token_version=1)
    bearer = {"Authorization": "Bearer " + tokens.mint_access("boss", "boss", "admin", 1)}
    assert (await auth.get_current_user(_request(headers=bearer))).user_id == "boss"
    with pytest.raises(auth.HTTPException) as err:
        await auth.get_current_user(_request(headers={**bearer, **VIEW}))
    assert err.value.status_code == 403
    # ...unless the deployment names that admin as its owner
    monkeypatch.setenv("STERLING_OWNER_UID", "boss")
    ctx = await auth.get_current_user(_request(headers={**bearer, **VIEW}))
    assert (ctx.user_id, ctx.owner_uid) == ("sim:boss", "boss")
    user_store.clear()


async def test_a_non_admin_owner_id_is_still_refused(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    user_store.clear()
    user_store._loaded = True
    user_store._users["default"] = user_store._User(id="default", username="d", role="trader", token_version=1)
    bearer = {"Authorization": "Bearer " + tokens.mint_access("default", "d", "trader", 1)}
    with pytest.raises(auth.HTTPException) as err:
        await auth.get_current_user(_request(headers={**bearer, **VIEW}))
    assert err.value.status_code == 403
    user_store.clear()


@pytest.mark.parametrize("claimed", ["sim:default", "sim:anything"])
async def test_no_login_or_header_can_BE_the_virtual_tenant(claimed):
    """The sim uid is reachable only through the view switch (which applies the deny list)."""
    with pytest.raises(auth.HTTPException) as err:
        await auth.get_current_user(_request(headers={"X-User-Id": claimed}))
    assert err.value.status_code == 403 and err.value.detail == "Reserved identity"


async def test_a_token_for_a_user_whose_id_is_a_sim_uid_is_refused(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    user_store.clear()
    user_store._loaded = True
    user_store._users["sim:default"] = user_store._User(id="sim:default", username="x", role="admin", token_version=1)
    bearer = {"Authorization": "Bearer " + tokens.mint_access("sim:default", "x", "admin", 1)}
    with pytest.raises(auth.HTTPException) as err:
        await auth.get_current_user(_request(headers=bearer))
    assert err.value.status_code == 403
    user_store.clear()


def test_the_view_scopes_http_routes_end_to_end(client, tenants):
    live, sim = tenants
    body = client.get("/api/v1/kite/accounts", headers=VIEW).json()["accounts"]
    assert [a["id"] for a in body] == [SIM_ACCOUNT_ID]                      # never the live account
    assert [a["id"] for a in client.get("/api/v1/kite/accounts").json()["accounts"]] == [live.id]
    status = client.get("/api/v1/kite/status", headers=VIEW).json()
    assert status["connected"] is True and status["account_id"] == SIM_ACCOUNT_ID and status["is_paper"] is True
    assert client.get("/api/v1/auth/me", headers=VIEW).json()["user_id"] == SIM_UID


# ── deny list: one test per route family, with the side effect checked ────────
DENIED_ROUTES = [
    ("kill switch engage", "POST", "/api/v1/kite/trading/kill-switch", {"enabled": True, "reason": "x"}),
    ("kill switch lift", "POST", "/api/v1/kite/trading/kill-switch", {"enabled": False, "reason": "xyz"}),
    ("emergency halt", "POST", "/api/v1/kite/engine/emergency-halt", None),
    ("safe-mode resume", "POST", "/api/v1/ops/safe-mode/resume", {}),
    ("operator safe-mode", "POST", "/api/v1/operator/safe-mode", {}),
    ("operator backup", "POST", "/api/v1/operator/backup", {}),
    ("family stop switch", "POST", "/api/v1/snapback/family/stop-new-trades", {}),
    ("family resume switch", "POST", "/api/v1/snapback/family/resume-new-trades", {}),
    ("account create", "POST", "/api/v1/kite/accounts", {"label": "x", "api_key": "k", "api_secret": "s"}),
    ("account update", "PUT", f"/api/v1/kite/accounts/{SIM_ACCOUNT_ID}", {"label": "x"}),
    ("account delete", "DELETE", f"/api/v1/kite/accounts/{SIM_ACCOUNT_ID}", None),
    ("account activate", "POST", f"/api/v1/kite/accounts/{SIM_ACCOUNT_ID}/activate", None),
    ("account test", "POST", f"/api/v1/kite/accounts/{SIM_ACCOUNT_ID}/test", None),
    ("login url", "GET", "/api/v1/kite/login-url", None),
    ("session", "POST", "/api/v1/kite/session", {"request_token": "x"}),
    ("session refresh", "POST", "/api/v1/kite/session/refresh", {}),
    ("logout", "POST", "/api/v1/kite/logout", None),
    ("holdings auth", "POST", "/api/v1/kite/holdings/authorise", {}),
    ("live daily-loss set", "PUT", "/api/v1/kite/risk/daily-loss", {"hard_halt_inr": -1000, "soft_warn_inr": -500}),
    ("live daily-loss clear", "DELETE", "/api/v1/kite/risk/daily-loss", None),
    ("retry queue", "DELETE", "/api/v1/kite/risk/retries", None),
    ("alert targets list", "GET", "/api/v1/kite/telegram", None),
    ("alert targets add", "POST", "/api/v1/kite/telegram", {}),
    ("user session", "POST", "/api/v1/auth/logout", {}),
    ("data provider credentials", "POST", "/api/v1/truedata/credentials", {}),
    ("data lake root", "POST", "/api/v1/datalake/root", {}),
    ("retired order path", "POST", "/api/v1/opening-volume-leaders/execute", {}),
]


@pytest.mark.parametrize("name,method,path,body", DENIED_ROUTES, ids=[r[0] for r in DENIED_ROUTES])
def test_denied_route_family_is_refused_in_the_simulation_view(client, tenants, name, method, path, body):
    kwargs = {} if body is None else {"json": body}
    response = client.request(method, path, headers=VIEW, **kwargs)
    assert response.status_code == 403 and response.json()["detail"] == DENIED


@pytest.mark.parametrize("name,method,path,body",
                         [r for r in DENIED_ROUTES if r[0] in ("kill switch engage", "emergency halt")],
                         ids=["kill switch engage", "emergency halt"])
def test_a_denied_halt_changes_no_global_live_state(client, tenants, monkeypatch, name, method, path, body):
    install_fake_sim_client(monkeypatch)        # so an undenied emergency halt WOULD reach the kill switch
    assert live_safety.kill_switch_state()["enabled"] is False
    client.request(method, path, headers=VIEW, **({} if body is None else {"json": body}))
    assert live_safety.kill_switch_state()["enabled"] is False


def test_the_same_kill_switch_call_is_not_blocked_by_the_guard_in_the_live_view(client, tenants):
    response = client.post("/api/v1/kite/trading/kill-switch", json={"enabled": True, "reason": "drill"})
    assert response.json().get("detail") != DENIED
    assert live_safety.kill_switch_state()["enabled"] is True         # live behaviour is unchanged
    live_safety.set_kill_switch(False)


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/v1/kite/trading/kill-switch"), ("GET", "/api/v1/kite/risk/daily-loss"),
    ("GET", "/api/v1/ops/safe-mode"), ("GET", "/api/v1/kite/accounts"), ("GET", "/api/v1/kite/status"),
    ("GET", "/api/v1/kite/engine/config"), ("GET", "/api/v1/kite/orders"), ("GET", "/api/v1/kite/positions"),
    ("GET", "/api/v1/ops/live-status"),
])
def test_reads_of_global_state_stay_available_in_the_simulation_view(client, tenants, method, path):
    response = client.request(method, path, headers=VIEW)
    body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    assert not (isinstance(body, dict) and body.get("detail") == DENIED)


# ── every non-denied mutating route under /api/v1/kite resolves to the sim tenant ──
def _kite_routes():
    out = []
    for module in (kite, kite_engine, navigator, kite_telegram):
        for route in module.router.routes:
            if isinstance(route, APIRoute):
                for verb in sorted(route.methods - {"HEAD", "OPTIONS"}):
                    out.append((verb, "/api/v1" + route.path, route))
    return out


def _depends_on_current_user(dep) -> bool:
    return dep.call is auth.get_current_user or any(_depends_on_current_user(d) for d in dep.dependencies)


#: Routes under /api/v1/kite that carry no user identity at all. Each is a broker -> Sterling
#: callback; none can name the virtual tenant (the postback resolves a REAL kite_user_id).
UNAUTHENTICATED_BY_DESIGN = {("POST", "/api/v1/kite/postback")}


def test_the_route_walk_sees_every_route_under_kite():
    spec = main.app.openapi()["paths"]
    published = {(verb.upper(), path) for path, ops in spec.items() if path.startswith("/api/v1/kite")
                 for verb in ops if verb in ("get", "post", "put", "patch", "delete")}
    assert {(v, p) for v, p, _ in _kite_routes()} == published, \
        "a router under /api/v1/kite is missing from the walk: add it to _kite_routes()"


def test_every_mutating_kite_route_is_scoped_by_the_view_or_refused_in_it():
    unscoped = []
    mutating = [(v, p, r) for v, p, r in _kite_routes() if v in ("POST", "PUT", "PATCH", "DELETE")]
    assert len(mutating) > 40
    for verb, path, route in mutating:
        if auth.simulation_denied(verb, path):
            continue
        if not _depends_on_current_user(route.dependant) and (verb, path) not in UNAUTHENTICATED_BY_DESIGN:
            unscoped.append((verb, path))
    assert unscoped == [], f"mutating routes that ignore the tenant (and so the view): {unscoped}"


async def test_every_non_denied_mutating_kite_route_gets_the_sim_tenant_and_denied_ones_get_403():
    seen = {"sim": 0, "denied": 0}
    for verb, path, route in _kite_routes():
        if verb == "GET" or (verb, path) in UNAUTHENTICATED_BY_DESIGN:
            continue
        concrete = path.replace("{", "x").replace("}", "")
        req = _request(verb, concrete, headers=VIEW)
        if auth.simulation_denied(verb, concrete):
            with pytest.raises(auth.HTTPException) as err:
                await auth.get_current_user(req)
            assert err.value.status_code == 403 and err.value.detail == DENIED, (verb, path)
            seen["denied"] += 1
        else:
            ctx = await auth.get_current_user(req)
            assert ctx.user_id == SIM_UID and ctx.view == "simulation", (verb, path)
            seen["sim"] += 1
    assert seen["sim"] > 20 and seen["denied"] > 10


def test_the_broker_postback_can_never_resolve_a_sim_account(tenants):
    assert kite_accounts.find_by_kite_user_id(SIM_ACCOUNT_ID) is None


# ── spot checks through the real handlers: state lands on the sim tenant ──────
def test_engine_config_written_in_the_simulation_view_lands_on_the_sim_tenant_only(client, tenants):
    before = engine_state.get_config(OWNER).risk_pct
    response = client.patch("/api/v1/kite/engine/config", headers=VIEW, json={"risk_pct": 0.37})
    assert response.status_code == 200 and response.json()["risk_pct"] == 0.37
    assert engine_state.get_config(SIM_UID).risk_pct == 0.37
    assert engine_state.get_config(OWNER).risk_pct == before


def test_gamma_and_snapback_config_written_in_the_simulation_view_land_on_the_sim_tenant_only(client, tenants):
    from app.services import gamma_move, snapback

    g_before = gamma_move.get_config(OWNER).execution_lots
    assert client.put("/api/v1/config/gamma-move", headers=VIEW, json={"execution_lots": g_before + 2}).status_code == 200
    assert gamma_move.get_config(SIM_UID).execution_lots == g_before + 2
    assert gamma_move.get_config(OWNER).execution_lots == g_before

    s_before = snapback.get_config(OWNER).max_open_positions
    response = client.put("/api/v1/config/snapback", headers=VIEW, json={"max_open_positions": s_before + 1})
    assert response.status_code == 200, response.text
    assert snapback.get_config(SIM_UID).max_open_positions == s_before + 1
    assert snapback.get_config(OWNER).max_open_positions == s_before


def test_an_order_route_in_the_simulation_view_only_ever_asks_for_the_sim_account(client, tenants, monkeypatch):
    live, _sim = tenants
    install_fake_sim_client(monkeypatch)
    asked = []
    real = kite_accounts.get_active
    monkeypatch.setattr(kite_accounts, "get_active", lambda uid: asked.append(uid) or real(uid))
    acquired = []
    real_acquire = kite_accounts.acquire_client

    async def spy(acct):
        acquired.append(acct.id)
        return await real_acquire(acct)

    monkeypatch.setattr(kite_accounts, "acquire_client", spy)
    client.post("/api/v1/kite/engine/emergency-square-off", headers=VIEW)
    client.get("/api/v1/kite/positions", headers=VIEW)
    assert asked and set(asked) == {SIM_UID}
    assert live.id not in acquired and set(acquired) <= {SIM_ACCOUNT_ID}
