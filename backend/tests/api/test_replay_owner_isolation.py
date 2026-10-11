"""Replay cannot disclose or change another operator's session or entry authority."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from app.api.v1.endpoints import config, simulation as routes, snapback_plans
from app.core.auth import get_current_user
from app.services import simulation
from app.services.simulation import SimConfig, SimState, SimulationRunner


@pytest.fixture
def env(monkeypatch):
    runner = SimulationRunner()
    runner._uid = "owner"
    runner._state = SimState.PAUSED
    runner._config = SimConfig(date="2026-10-05")
    runner._session_complete = True
    monkeypatch.setattr(simulation, "simulation_runner", runner)
    monkeypatch.setattr(routes, "simulation_runner", runner)
    monkeypatch.setattr(routes, "_START_LOCK", asyncio.Lock())
    user = SimpleNamespace(user_id="other")
    app = FastAPI()
    app.include_router(routes.router)
    app.include_router(config.router)
    app.include_router(snapback_plans.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return app, runner, user


@pytest.mark.asyncio
async def test_foreign_status_is_empty_but_owner_sees_session(env):
    app, runner, user = env
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = (await client.get("/simulation/status")).json()
        assert result["state"] == SimState.IDLE.value
        assert result["config"] is None
        assert result["stats"]["events"] == []
        user.user_id = "owner"
        result = (await client.get("/simulation/status")).json()
        assert result["state"] == SimState.PAUSED.value
        assert result["config"]["date"] == "2026-10-05"


@pytest.mark.asyncio
@pytest.mark.parametrize("path,body", [
    ("stop", {}), ("clear", {}), ("pause", {}), ("resume", {}),
    ("speed", {"speed": 25}), ("seek", {"action": "jump_end"}),
    ("start?force=true", {"date": "2026-10-06"}),
])
async def test_foreign_controls_cannot_mutate_or_force_takeover(env, path, body):
    app, runner, _ = env
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = await client.post("/simulation/" + path, json=body)
        assert result.status_code == 409
    assert runner._uid == "owner"
    assert runner._state == SimState.PAUSED
    assert runner._config.date == "2026-10-05"


@pytest.mark.asyncio
async def test_foreign_stream_is_refused(env):
    app, _, _ = env
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/simulation/stream")).status_code == 409


@pytest.mark.asyncio
async def test_stream_stops_before_emitting_after_owner_changes(env):
    _, runner, user = env
    user.user_id = "owner"
    request = SimpleNamespace(is_disconnected=AsyncMock(return_value=False))
    response = await routes.stream_sim(request, user)
    first = await anext(response.body_iterator)
    assert "event: state" in first
    runner._uid = "other"
    runner._publish_state()
    with pytest.raises(StopAsyncIteration):
        await anext(response.body_iterator)
    assert runner._subscribers == []


@pytest.mark.asyncio
async def test_start_is_serialized_and_foreign_force_cannot_win(env, monkeypatch):
    _, runner, user = env
    runner._state = SimState.IDLE
    runner._session_complete = False
    runner._uid = None
    entered, release = asyncio.Event(), asyncio.Event()
    async def slow_start(cfg):
        entered.set()
        await release.wait()
        runner._state = SimState.LOADING
        runner._config = cfg
        return runner.status
    monkeypatch.setattr(runner, "start", slow_start)
    first = asyncio.create_task(routes.start_sim(SimConfig(date="2026-10-05"), False,
                                                SimpleNamespace(user_id="owner")))
    await asyncio.wait_for(entered.wait(), 1)
    second = asyncio.create_task(routes.start_sim(SimConfig(date="2026-10-06"), True, user))
    release.set()
    await first
    with pytest.raises(HTTPException) as exc:
        await second
    assert exc.value.status_code == 409
    assert runner._uid == "owner"
    assert runner._config.date == "2026-10-05"


@pytest.mark.asyncio
@pytest.mark.parametrize("path,body", [
    ("/config/gamma-move/scan", {}),
    ("/config/gamma-move/arm", {"signal_id": "s1"}),
    ("/config/gamma-move/adopt", {"symbol": "NIFTY26OCT25000CE", "quantity": 25, "entry_price": 100}),
    ("/snapback/plans", {"signal_id": "s1"}),
    ("/snapback/plans/p1/arm", {"expected_revision": 1, "idempotency_key": "once"}),
])
async def test_replay_owner_cannot_request_live_entry(env, path, body):
    app, _, user = env
    user.user_id = "owner"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = await client.post(path, json=body)
        assert result.status_code == 409
        assert "replay" in result.json()["detail"].lower()


@pytest.mark.asyncio
async def test_foreign_replay_does_not_block_live_gamma_scan(env, monkeypatch):
    from app.services import gamma_move_runner
    scan = AsyncMock(return_value={"source": "live"})
    monkeypatch.setattr(gamma_move_runner, "scan_once", scan)
    app, _, _ = env
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = await client.post("/config/gamma-move/scan")
        assert result.status_code == 200
        assert result.json()["source"] == "live"
    scan.assert_awaited_once_with("other")


def test_foreign_replay_does_not_block_snapback_plan_authority(env):
    snapback_plans._refuse_replay_entry(SimpleNamespace(user_id="other"))


@pytest.mark.asyncio
async def test_owner_can_resume_stop_clear_and_release_slot(env, monkeypatch):
    app, runner, user = env
    user.user_id = "owner"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/simulation/resume")).status_code == 200
        assert runner._state == SimState.RUNNING
        assert (await client.post("/simulation/stop")).status_code == 200
        assert runner._state == SimState.IDLE
        assert (await client.post("/simulation/clear")).status_code == 200
        assert not runner.has_session_view
        user.user_id = "other"
        async def start(cfg):
            runner._config = cfg
            runner._state = SimState.LOADING
            return runner.status
        monkeypatch.setattr(runner, "start", start)
        assert (await client.post("/simulation/start", json={"date": "2026-10-06"})).status_code == 200
        assert runner._uid == "other"


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["intraday", "adaptive-edge"])
async def test_foreign_archived_snapshot_uses_its_live_service(env, monkeypatch, surface):
    from app.services import adaptive_edge, intraday
    app, _, _ = env
    if surface == "intraday":
        monkeypatch.setattr(intraday, "snapshot", lambda uid: {"uid": uid, "source": "live"})
    else:
        monkeypatch.setattr(adaptive_edge, "snapshot", AsyncMock(return_value={"uid": "other", "source": "live"}))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = await client.get(f"/config/{surface}/snapshot")
        assert result.status_code == 200
        assert result.json() == {"uid": "other", "source": "live"}
