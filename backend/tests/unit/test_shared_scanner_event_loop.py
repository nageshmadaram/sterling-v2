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
