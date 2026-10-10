"""Process wiring: ``main`` boots the owner's virtual tenant, runs the Simulation loop beside live
trading, stops it first on shutdown, and a failure in any of it can never stop the live runtime."""
from __future__ import annotations

import asyncio

import pytest
from starlette.testclient import TestClient
from tenancy_support import OWNER, SIM_ACCOUNT_ID, SIM_UID, wipe_tenants

import main
from app.services import db, live_runtime
from app.services.exchanges.kite import accounts as kite_accounts
from app.services.simulation import identity, sim_runtime, tenant


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    wipe_tenants()
    monkeypatch.delenv("STERLING_OWNER_UID", raising=False)
    yield
    wipe_tenants()
    asyncio.run(sim_runtime.stop_sim_runtime())


def _routes():
    return {(verb.upper(), path) for path, ops in main.app.openapi()["paths"].items() for verb in ops}


def test_the_sim_router_is_mounted_and_the_replay_router_is_still_separate():
    routes = _routes()
    assert {("GET", "/api/v1/sim/status"), ("PUT", "/api/v1/sim/strategy/{strategy}/mode"),
            ("POST", "/api/v1/sim/reset"), ("PUT", "/api/v1/sim/capital")} <= routes
    assert ("GET", "/api/v1/simulation/status") in routes and ("POST", "/api/v1/simulation/start") in routes
    assert ("GET", "/api/v1/journal/trades") in routes


def test_startup_creates_the_tenant_and_runs_the_sim_loop_beside_live():
    assert kite_accounts.sim_account_for(OWNER) is None
    with TestClient(main.app) as client:
        account = kite_accounts.sim_account_for(OWNER)
        assert account is not None and account.id == SIM_ACCOUNT_ID and account.user_id == SIM_UID
        control = db.get_execution_control(uid=SIM_UID, account_id=SIM_ACCOUNT_ID)
        assert (control["operator_state"], control["recovery_state"]) == ("RUNNING", "CLEAN")
        assert sim_runtime.is_running() is True
        assert client.get("/api/v1/sim/status").json()["runtime"]["running"] is True
    assert sim_runtime.is_running() is False                                # stopped with the app


def test_the_owner_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("STERLING_OWNER_UID", "boss")
    with TestClient(main.app):
        assert kite_accounts.sim_account_for("boss").user_id == "sim:boss"
        assert kite_accounts.sim_account_for(OWNER) is None


def test_a_bad_owner_setting_boots_without_a_tenant_and_live_still_starts(monkeypatch):
    monkeypatch.setenv("STERLING_OWNER_UID", "sim:default")                 # a sim uid can never be the owner
    started = []
    real_start = live_runtime.start_live_runtime
    monkeypatch.setattr(live_runtime, "start_live_runtime", lambda *a, **k: started.append(1) or real_start(*a, **k))
    with TestClient(main.app) as client:
        assert started == [1]
        assert kite_accounts.sim_account_for(OWNER) is None
        assert client.get("/health").status_code in (200, 503)


def test_a_failing_tenant_boot_never_stops_live_startup(monkeypatch):
    def boom(owner):
        raise RuntimeError("ledger unreadable")

    monkeypatch.setattr(tenant, "boot_tenant", boom)
    started = []
    real_start = live_runtime.start_live_runtime
    monkeypatch.setattr(live_runtime, "start_live_runtime", lambda *a, **k: started.append(1) or real_start(*a, **k))
    with TestClient(main.app) as client:
        assert started == [1]
        assert client.get("/api/v1/journal/summary").status_code in (200, 400, 403)   # the app serves


def test_a_failing_sim_runtime_never_stops_live_startup_or_shutdown(monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("cannot start")

    monkeypatch.setattr(sim_runtime, "start_sim_runtime", boom)
    stopped = []
    real_stop = live_runtime.stop_live_runtime

    async def spy_stop(handles):
        stopped.append(1)
        return await real_stop(handles)

    monkeypatch.setattr(live_runtime, "stop_live_runtime", spy_stop)
    with TestClient(main.app) as client:
        assert sim_runtime.is_running() is False
        assert client.get("/api/v1/sim/status").status_code == 200           # the API still answers
    assert stopped == [1]


def test_a_failing_sim_runtime_stop_never_stops_live_shutdown(monkeypatch):
    async def boom():
        raise RuntimeError("stuck")

    monkeypatch.setattr(sim_runtime, "stop_sim_runtime", boom)
    stopped = []
    real_stop = live_runtime.stop_live_runtime

    async def spy_stop(handles):
        stopped.append(1)
        return await real_stop(handles)

    monkeypatch.setattr(live_runtime, "stop_live_runtime", spy_stop)
    with TestClient(main.app):
        pass
    assert stopped == [1]
    monkeypatch.undo()                                                       # let the fixture stop the real loop


def test_the_configured_owner_never_resolves_to_a_sim_identity(monkeypatch):
    assert tenant.configured_owner_uid() == "default"
    monkeypatch.setenv("STERLING_OWNER_UID", " boss ")
    assert tenant.configured_owner_uid() == "boss"
    monkeypatch.setenv("STERLING_OWNER_UID", "sim:boss")
    with pytest.raises(ValueError):
        tenant.configured_owner_uid()
    assert identity.sim_uid("boss") == "sim:boss"
