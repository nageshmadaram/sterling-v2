"""The Simulation tenant's scans must never queue real trading calls behind them."""
import asyncio
import time

import pytest

from app.services.exchanges.kite import client as kc


@pytest.mark.asyncio
async def test_a_background_caller_steps_aside_while_work_is_reserved_but_foreground_does_not():
    lim = kc.ReservationRateLimiter(rate=10.0, burst=1)
    for _ in range(8):                       # 0.8 s of reserved work ahead
        lim.reserve()
    assert lim.backlog() > kc.BACKGROUND_YIELD_BACKLOG_S

    t0 = time.monotonic()
    await lim.acquire()                      # foreground queues FIFO: waits its slot only
    fg = time.monotonic() - t0

    token = kc.BACKGROUND_CALLS.set(True)
    try:
        t1 = time.monotonic()
        await lim.acquire()                  # background yields until the backlog drains, then reserves
        bg = time.monotonic() - t1
    finally:
        kc.BACKGROUND_CALLS.reset(token)
    assert fg >= 0.6 and bg >= 0.0           # the foreground caller was not delayed by the background one
    assert lim.backlog() < 0.5


@pytest.mark.asyncio
async def test_background_yielding_is_bounded_so_it_cannot_starve():
    lim = kc.ReservationRateLimiter(rate=0.2, burst=1)     # 5 s per slot: backlog stays high
    for _ in range(4):
        lim.reserve()
    token = kc.BACKGROUND_CALLS.set(True)
    t0 = time.monotonic()
    try:
        await asyncio.wait_for(lim.acquire(), timeout=kc.BACKGROUND_MAX_YIELD_S + 1.0 + 25.0)
    except asyncio.TimeoutError:
        pass
    finally:
        kc.BACKGROUND_CALLS.reset(token)
    assert time.monotonic() - t0 >= kc.BACKGROUND_MAX_YIELD_S - 0.2   # yielded up to the cap


@pytest.mark.asyncio
async def test_the_sim_scan_lane_marks_its_passes_as_background():
    from app.services import sim_scan_lane

    seen = []

    async def probe():
        seen.append(kc.BACKGROUND_CALLS.get())

    assert sim_scan_lane.spawn("probe", "sim:x", probe) is True
    for _ in range(50):
        if seen:
            break
        await asyncio.sleep(0.01)
    assert seen == [True]
    assert kc.BACKGROUND_CALLS.get() is False        # the spawning (live) context is untouched
