import asyncio
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock
import pytest
from app.services.kite_engine import service
from app.engines.sterling_kite_engine.schemas import EngineConfigModel
from app.services.exchanges.kite.client import BACKGROUND_CALLS


@pytest.fixture
def preview(monkeypatch):
    monkeypatch.setattr(service, '_preview_turn', {})
    monkeypatch.setattr(service.state, 'log', Mock())
    monkeypatch.setattr(service.scanner, 'snapshot', lambda uid: SimpleNamespace(rows=[]))
    async def run():
        await service._preview_next_mode(object(), 'test-owner', EngineConfigModel(horizon_mode='swing'),
            spot_universe=[], nfo=[], bfo=[], deriv_universe=[], confluence_universe=[])
    return run


@pytest.mark.asyncio
@pytest.mark.parametrize('inherited', [False, True])
async def test_preview_yields_without_changing_selected_mode_or_parent_scope(monkeypatch, preview, inherited):
    seen = []
    async def scan(**kwargs):
        seen.append((BACKGROUND_CALLS.get(), kwargs))
    monkeypatch.setattr(service.scanner, 'scan', scan)
    token = BACKGROUND_CALLS.set(inherited)
    try:
        await preview()
        assert BACKGROUND_CALLS.get() is inherited
    finally:
        BACKGROUND_CALLS.reset(token)
    assert seen[0][0] is True
    assert seen[0][1]['cfg'].horizon_mode != 'swing'
    assert seen[0][1]['place_cb'] is None
    assert '|view:' in seen[0][1]['uid']


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [False, True])
async def test_failed_preview_restores_foreground_context(monkeypatch, preview, cancel):
    seen = []
    async def scan(**kwargs):
        seen.append(BACKGROUND_CALLS.get())
        if cancel:
            raise asyncio.CancelledError
        raise RuntimeError('preview unavailable')
    monkeypatch.setattr(service.scanner, 'scan', scan)
    token = BACKGROUND_CALLS.set(False)
    try:
        if cancel:
            with pytest.raises(asyncio.CancelledError):
                await preview()
        else:
            await preview()
        assert BACKGROUND_CALLS.get() is False
        assert seen == [True]
    finally:
        BACKGROUND_CALLS.reset(token)


@pytest.mark.asyncio
async def test_preview_does_not_lower_concurrent_live_task_priority(monkeypatch, preview):
    entered, release = asyncio.Event(), asyncio.Event()
    observed = []
    async def scan(**kwargs):
        observed.append(BACKGROUND_CALLS.get())
        entered.set()
        await release.wait()
    monkeypatch.setattr(service.scanner, 'scan', scan)
    token = BACKGROUND_CALLS.set(False)
    try:
        task = asyncio.create_task(preview())
        await entered.wait()
        assert BACKGROUND_CALLS.get() is False
        release.set()
        await task
        assert observed == [True]
        assert BACKGROUND_CALLS.get() is False
    finally:
        BACKGROUND_CALLS.reset(token)


@pytest.mark.asyncio
async def test_preview_uses_existing_bounded_yield_while_foreground_does_not(monkeypatch, preview):
    from app.services.exchanges.kite import client as kite
    limiter = kite.ReservationRateLimiter(rate=2.8, burst=2)
    original_budget = (limiter._interval, limiter._burst_window)
    monkeypatch.setattr(limiter, 'backlog', Mock(side_effect=[1.0, 0.0]))
    reserve = Mock(return_value=0.0)
    monkeypatch.setattr(limiter, 'reserve', reserve)
    sleep = AsyncMock()
    monkeypatch.setattr(kite.asyncio, 'sleep', sleep)
    async def scan(**kwargs):
        await limiter.acquire()
    monkeypatch.setattr(service.scanner, 'scan', scan)
    token = BACKGROUND_CALLS.set(False)
    try:
        await preview()
        sleep.assert_awaited_once_with(kite.BACKGROUND_YIELD_STEP_S)
        await limiter.acquire()
        sleep.assert_awaited_once()
        assert reserve.call_count == 2
        assert original_budget == (limiter._interval, limiter._burst_window)
    finally:
        BACKGROUND_CALLS.reset(token)
