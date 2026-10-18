"""Unauthenticated probes must not block the event loop or be re-run per hit."""
from __future__ import annotations

import asyncio
import inspect
import threading

from fastapi.testclient import TestClient

from app.api.v1.endpoints import snapback_ops
from app.services import snapback_preflight


class _Result:
    passed = True

    def as_dict(self):
        return {"checks": []}


def test_probes_are_sync_handlers_so_they_run_in_the_threadpool():
    assert not inspect.iscoroutinefunction(snapback_ops.health_ready)
    assert not inspect.iscoroutinefunction(snapback_ops.prospective_health)


def test_readiness_is_cached_and_single_flight(monkeypatch):
    calls = {"n": 0}
    threads = set()

    def fake_preflight(**_):
        calls["n"] += 1
        threads.add(threading.get_ident())
        return _Result()

    monkeypatch.setattr(snapback_preflight, "run_preflight", fake_preflight)
    monkeypatch.setitem(snapback_ops._ready_cache, "body", None)
    from main import create_app

    client = TestClient(create_app())
    for _ in range(5):
        assert client.get("/api/v1/health/ready").status_code == 200
    assert calls["n"] == 1
    assert threading.get_ident() not in threads  # ran off the caller's thread


def test_readiness_cache_expires_and_failure_is_503(monkeypatch):
    class _Fail(_Result):
        passed = False

    monkeypatch.setattr(snapback_preflight, "run_preflight", lambda **_: _Fail())
    monkeypatch.setitem(snapback_ops._ready_cache, "body", None)
    monkeypatch.setattr(snapback_ops, "_READY_TTL_S", 0.0)
    from main import create_app

    client = TestClient(create_app())
    assert client.get("/api/v1/health/ready").status_code == 503
    assert client.get("/api/v1/health/ready").json()["ready"] is False


def test_event_loop_stays_responsive_while_readiness_runs(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def slow_preflight(**_):
        started.set()
        release.wait(timeout=2)
        finished.set()
        return _Result()

    monkeypatch.setattr(snapback_preflight, "run_preflight", slow_preflight)
    monkeypatch.setitem(snapback_ops._ready_cache, "body", None)

    async def scenario():
        import httpx
        from main import create_app

        transport = httpx.ASGITransport(app=create_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            async def heartbeat():
                for _ in range(200):
                    if started.is_set():
                        break
                    await asyncio.sleep(0.01)
                assert started.is_set(), "readiness handler never started"
                assert not finished.is_set(), "readiness blocked the event loop"
                release.set()

            await asyncio.gather(c.get("/api/v1/health/ready"), heartbeat())
            assert finished.is_set()

    asyncio.run(scenario())


def test_heavy_read_and_backup_lane_handlers_run_in_the_threadpool():
    """lanes_dashboard (~300 ms), system health (~260 ms) and the evidence backup copy
    were `async def` with no await: they ran on, and froze, the event loop that also
    serves order handling and the Kite tick reader."""
    from app.api.v1.endpoints import lanes

    for name in (
        "lanes_dashboard", "system_health_status", "lane_evidence", "positions",
        "operator_doctor", "operator_backup", "lane_status", "lane_promotion",
    ):
        assert not inspect.iscoroutinefunction(getattr(lanes, name)), name


def test_operations_snapshot_uses_worker_thread_for_all_storage_reads(monkeypatch):
    from fastapi import FastAPI
    from app.api.v1.endpoints.family_operations import router
    from app.services import family_operations_v2
    from app.services.ema_trend import snapshot
    loop_threads=set()
    storage_threads=[]
    def payload():
        storage_threads.append(threading.get_ident())
        return {"generated_at":"2026-10-18T00:00:00Z"}
    def archived_view(uid):
        storage_threads.append(threading.get_ident())
        return {"status":"DISABLED"}
    monkeypatch.setattr(family_operations_v2,'family_operations_v2',payload)
    monkeypatch.setattr(snapshot,'operations',archived_view)
    app=FastAPI()
    @app.middleware('http')
    async def capture_loop(request,call_next):
        loop_threads.add(threading.get_ident())
        return await call_next(request)
    app.include_router(router,prefix='/api/v1')
    with TestClient(app) as client:
        response=client.get('/api/v1/operations/family')
    assert response.status_code==200
    assert len(storage_threads)==2
    assert not loop_threads.intersection(storage_threads)
    assert response.headers['cache-control']=='no-store'
