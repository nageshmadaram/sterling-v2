"""A shared scanner's durable commit must not stall protection or liveness."""
import asyncio
import threading

import pytest
from app.services.ema_trend.scanner import _off_loop


@pytest.mark.asyncio
async def test_storage_work_leaves_the_event_loop_responsive():
    started=threading.Event()
    release=threading.Event()
    loop_thread=threading.get_ident()
    def commit():
        assert threading.get_ident()!=loop_thread
        started.set()
        assert release.wait(3)
        return 'committed'
    task=asyncio.create_task(_off_loop(commit))
    try:
        assert await asyncio.to_thread(started.wait,2)
        await asyncio.sleep(.01)
        assert not task.done()
        release.set()
        assert await task=='committed'
    finally:
        release.set()
        await asyncio.gather(task,return_exceptions=True)


@pytest.mark.asyncio
async def test_cancel_waits_for_the_inflight_transaction_before_reporting_done():
    started=threading.Event()
    release=threading.Event()
    def commit():
        started.set()
        assert release.wait(3)
    task=asyncio.create_task(_off_loop(commit))
    try:
        assert await asyncio.to_thread(started.wait,2)
        task.cancel()
        await asyncio.sleep(.02)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        await asyncio.gather(task,return_exceptions=True)


@pytest.mark.asyncio
async def test_supertrend_scan_cache_write_runs_outside_the_loop(monkeypatch):
    from app.services.kite_engine import scanner as module,state
    from app.engines.sterling_kite_engine.config import SterlingKiteEngineConfig
    loop_thread=threading.get_ident()
    writes=[]
    monkeypatch.setattr(state,'load_signal_cache',lambda uid:None)
    def persist(*args):
        assert threading.get_ident()!=loop_thread
        writes.append(args)
    monkeypatch.setattr(state,'save_signal_cache',persist)
    await module.KiteEngineScanner().scan(uid='cache-thread-probe',client=object(),
        universe=[],nfo_rows=[],bfo_rows=[],cfg=SterlingKiteEngineConfig())
    assert writes and writes[-1][0]=='cache-thread-probe'


@pytest.mark.asyncio
async def test_background_health_write_runs_outside_the_loop(monkeypatch):
    from unittest.mock import AsyncMock
    from app.services.ema_trend import runner
    loop_thread=threading.get_ident()
    writes=[]
    monkeypatch.setattr(runner.repo,'owners',lambda:['probe'])
    monkeypatch.setattr(runner.repo,'list_documents',lambda *a,**kw:[])
    monkeypatch.setattr(runner,'run_user',AsyncMock(return_value={'status':'RUNNING','errors':[]}))
    def persist(*args):
        assert threading.get_ident()!=loop_thread
        writes.append(args)
    monkeypatch.setattr(runner.health,'record',persist)
    async def stop_after_cycle(*args):
        raise asyncio.CancelledError()
    monkeypatch.setattr(runner.asyncio,'sleep',stop_after_cycle)
    with pytest.raises(asyncio.CancelledError):
        await runner.run_loop()
    assert len(writes)==1
