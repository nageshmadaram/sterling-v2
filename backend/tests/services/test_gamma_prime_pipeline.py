"""Offline recovery I/O regression: bounded overlap, ordered engine replay."""
import asyncio
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import gamma_move_scanner as scanner
from tests.services.test_gamma_move_scanner import (
    BASE, watch, closed_option_bars, underlying_bars,
)


@pytest.fixture
def setup_prime(monkeypatch):
    uid = 'pipeline-test'
    scanner.clear_runtime(uid)
    monkeypatch.setattr(scanner, '_runtime_inputs', AsyncMock(return_value={}))
    monkeypatch.setattr(scanner, '_to_candles', lambda rows, **kw: rows)
    monkeypatch.setattr(scanner, '_completed', lambda rows, *args: rows)
    built = []

    def build(w, cfg, bars, underlying, spot, signals, inputs, day):
        built.append(w.instrument.instrument_id)
        return SimpleNamespace(contract=w, oi_peak_day=day)

    monkeypatch.setattr(scanner, '_build_runtime', build)
    watches = [replace(watch(), instrument=replace(watch().instrument,
               instrument_id=str(222 + i), tradingsymbol=f'OPT{i}'),
               list_name=f'L{i}') for i in range(5)]
    yield uid, watches, built
    scanner.clear_runtime(uid)


@pytest.mark.asyncio
async def test_option_reads_overlap_but_are_bounded_and_replay_in_selection_order(monkeypatch, setup_prime):
    uid, watches, built = setup_prime
    started = asyncio.Event()
    active = peak = 0
    underlying_calls = []
    pacers = []

    async def history(client, pacer, token, interval, frm, to, oi):
        nonlocal active, peak
        pacers.append(pacer)
        if not oi:
            underlying_calls.append(token)
            return underlying_bars()
        active += 1
        peak = max(peak, active)
        if active == 2:
            started.set()
        try:
            # A serial implementation cannot release the first selected read.
            await asyncio.wait_for(started.wait(), 0.2)
            if token == 222:
                await asyncio.sleep(0)
            return closed_option_bars()
        finally:
            active -= 1

    monkeypatch.setattr(scanner, '_historical', history)
    result = await scanner._prime_runtime(uid, scanner.GammaMoveConfig(), object(), watches, BASE.date(), {}, {})
    assert result == []
    assert peak == 2
    assert built == [w.instrument.instrument_id for w in watches]
    assert underlying_calls == [111]
    assert len({id(p) for p in pacers}) == 1
    assert pacers[0]._gap == pytest.approx(1 / 2.6)
    assert list(scanner._runtime[uid]) == [222, 223, 224, 225, 226]


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['option', 'underlying', 'short'])
async def test_partial_history_retains_error_and_warmup_semantics(monkeypatch, setup_prime, failure):
    uid, watches, built = setup_prime
    calls = []

    async def history(client, pacer, token, interval, frm, to, oi):
        calls.append((token, oi))
        if failure == 'option' and token == 222:
            raise RuntimeError('offline option failure')
        if failure == 'underlying' and not oi:
            raise RuntimeError('offline underlying failure')
        if failure == 'short' and token == 222:
            return []
        return closed_option_bars() if oi else underlying_bars()

    monkeypatch.setattr(scanner, '_historical', history)
    errors = await scanner._prime_runtime(uid, scanner.GammaMoveConfig(), object(), watches[:2], BASE.date(), {}, {})
    assert calls.count((111, False)) == 1
    if failure == 'underlying':
        assert len(errors) == 1 and 'underlying 5-minute history unavailable' in errors[0]
        assert built == []
    elif failure == 'option':
        assert len(errors) == 1 and 'option 5-minute history unavailable' in errors[0]
        assert built == ['223']
    else:
        assert errors == [] and built == ['223']


@pytest.mark.asyncio
async def test_short_warmup_never_fetches_underlying(monkeypatch, setup_prime):
    uid, watches, built = setup_prime
    history = AsyncMock(return_value=[])
    monkeypatch.setattr(scanner, '_historical', history)
    assert await scanner._prime_runtime(uid, scanner.GammaMoveConfig(), object(), watches[:2], BASE.date(), {}, {}) == []
    assert built == []
    assert all(call.args[-1] is True for call in history.await_args_list)


@pytest.mark.asyncio
async def test_overlapping_broker_reads_still_obey_existing_start_rate(setup_prime):
    uid, watches, built = setup_prime
    starts = []
    active = peak = 0

    async def get_historical(token, interval, frm, to, continuous, oi):
        nonlocal active, peak
        starts.append(time.monotonic())
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.5)
            return closed_option_bars() if oi else underlying_bars()
        finally:
            active -= 1

    # No account/cache identity: exercise _historical_uncached and real Pacer.
    client = SimpleNamespace(get_historical=get_historical)
    assert await scanner._prime_runtime(uid, scanner.GammaMoveConfig(), client, watches[:2], BASE.date(), {}, {}) == []
    assert len(starts) == 3  # Two selected options and their shared underlying.
    assert min(b - a for a, b in zip(starts, starts[1:])) >= 1 / 2.6 - 0.01
    assert peak == 2
    assert built == ['222', '223']


@pytest.mark.asyncio
async def test_cancellation_drains_pending_reads_and_never_publishes_partial_runtime(monkeypatch, setup_prime):
    uid, watches, built = setup_prime
    started = asyncio.Event()
    count = 0
    cancelled = []

    async def history(client, pacer, token, *args):
        nonlocal count
        count += 1
        if count == 2:
            started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(token)
            raise

    monkeypatch.setattr(scanner, '_historical', history)
    task = asyncio.create_task(scanner._prime_runtime(uid, scanner.GammaMoveConfig(), object(), watches, BASE.date(), {}, {}))
    try:
        await asyncio.wait_for(started.wait(), 0.2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert sorted(cancelled) == [222, 223]
    assert built == []
    assert not scanner._runtime.get(uid)
