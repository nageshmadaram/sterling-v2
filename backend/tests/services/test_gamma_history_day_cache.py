from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.services import gamma_move_scanner as scanner

IST = timezone(timedelta(hours=5, minutes=30))


@pytest.mark.asyncio
async def test_completed_day_history_survives_a_fresh_reader(tmp_path, monkeypatch):
    monkeypatch.setenv('STERLING_GAMMA_CACHE_DIR', str(tmp_path))
    rows = [['2026-10-22T09:15:00+0530', 10, 12, 9, 11, 100]]
    first = SimpleNamespace(_account_id='owner-a', get_historical=AsyncMock(return_value=rows))
    second = SimpleNamespace(_account_id='owner-a', get_historical=AsyncMock(side_effect=AssertionError('duplicate history request')))
    pacer = SimpleNamespace(wait=AsyncMock())
    args = (123, '5minute', '2026-10-22 09:15:00', '2026-10-22 15:30:00', False)
    assert await scanner._historical(first, pacer, *args) == rows
    assert await scanner._historical(second, pacer, *args) == rows
    second.get_historical.assert_not_awaited()
    # Another account cannot consume the owner's cached observations.
    other = SimpleNamespace(_account_id='owner-b', get_historical=AsyncMock(return_value=rows))
    await scanner._historical(other, pacer, *args)
    other.get_historical.assert_awaited_once()


@pytest.mark.asyncio
async def test_cache_failure_never_substitutes_stale_data(tmp_path, monkeypatch):
    monkeypatch.setenv('STERLING_GAMMA_CACHE_DIR', str(tmp_path))
    from app.services import gamma_move_history_cache as cache
    now = [datetime(2026, 10, 23, 11, 30, tzinfo=IST)]
    monkeypatch.setattr(cache, '_now', lambda: now[0])
    first_rows = [['2026-10-23T11:25:00+0530', 10, 12, 9, 11, 100]]
    client = SimpleNamespace(_account_id='owner', get_historical=AsyncMock(return_value=first_rows))
    pacer = SimpleNamespace(wait=AsyncMock())
    args = (123, '5minute', '2026-10-23 09:15:00', '2026-10-23 15:30:00', False)
    assert await scanner._historical(client, pacer, *args) == first_rows
    now[0] += timedelta(minutes=5)
    client.get_historical.side_effect = RuntimeError('broker unavailable')
    with pytest.raises(RuntimeError, match='broker unavailable'):
        await scanner._historical(client, pacer, *args)


@pytest.mark.asyncio
async def test_corrupt_coverage_is_rebuilt_instead_of_trusted(tmp_path, monkeypatch):
    import json
    from app.services import gamma_move_history_cache as cache
    monkeypatch.setenv('STERLING_GAMMA_CACHE_DIR', str(tmp_path))
    rows = [['2026-10-22T09:15:00+0530', 10, 12, 9, 11, 100]]
    client = SimpleNamespace(_account_id='owner', get_historical=AsyncMock(return_value=rows))
    pacer = SimpleNamespace(wait=AsyncMock())
    args = (123, '5minute', '2026-10-22 09:15:00', '2026-10-22 15:30:00', False)
    await scanner._historical(client, pacer, *args)
    path = next(tmp_path.rglob('*.json'))
    value = json.loads(path.read_text())
    value['through'] = float('inf')
    value['rows'][0][4] = 9999
    path.write_text(json.dumps(value))
    client.get_historical.reset_mock()
    assert await scanner._historical(client, pacer, *args) == rows
    client.get_historical.assert_awaited_once()


@pytest.mark.asyncio
async def test_incremental_history_rereads_tail_and_preserves_earlier_bars(tmp_path, monkeypatch):
    from app.services import gamma_move_history_cache as cache
    monkeypatch.setenv('STERLING_GAMMA_CACHE_DIR', str(tmp_path))
    now = [datetime(2026, 10, 23, 11, 30, tzinfo=IST)]
    monkeypatch.setattr(cache, '_now', lambda: now[0])
    old = [['2026-10-23T11:20:00+0530', 10, 12, 9, 11, 100],
           ['2026-10-23T11:25:00+0530', 10, 12, 9, 11, 100]]
    revised = [['2026-10-23T11:25:00+0530', 10, 13, 9, 12, 200],
               ['2026-10-23T11:30:00+0530', 12, 14, 11, 13, 100]]
    client = SimpleNamespace(_account_id='owner', get_historical=AsyncMock(side_effect=[old, revised]))
    args = (123, '5minute', '2026-10-23 09:15:00', '2026-10-23 15:30:00', False)
    pacer = SimpleNamespace(wait=AsyncMock())
    await scanner._historical(client, pacer, *args)
    now[0] += timedelta(minutes=5)
    assert await scanner._historical(client, pacer, *args) == [old[0], *revised]
    assert client.get_historical.await_args.args[2] == '2026-10-23 11:25:00'


@pytest.mark.asyncio
async def test_live_zero_oi_strike_is_filtered_before_history_priming(monkeypatch):
    from datetime import date
    cfg = scanner.GammaMoveConfig()
    expiry = date(2026, 11, 26)
    instrument = {'segment': 'NFO-OPT', 'name': 'TEST', 'expiry': str(expiry),
                  'instrument_type': 'CE', 'tradingsymbol': 'TEST26NOV100CE',
                  'instrument_token': 222, 'strike': 100, 'lot_size': 1}
    monkeypatch.setattr(scanner, 'nfo_dump', AsyncMock(return_value=[instrument]))
    monkeypatch.setattr(scanner, '_approaching', lambda level, config: (False, True))
    monkeypatch.setattr(scanner, 'lists_for', lambda *args: [SimpleNamespace(side='CE', expiry_date=expiry, list_name='L1')])
    monkeypatch.setattr(scanner, '_quote_batched', AsyncMock(return_value={
        'NFO:TEST26NOV100CE': {'oi': 0, 'volume': 0, 'last_price': 10}}))
    client = SimpleNamespace(get_historical=AsyncMock(side_effect=AssertionError('untraded strike history')))
    day = date(2026, 10, 23)
    watches = await scanner._watchlist_stage('zero-oi-test', cfg, client,
        {'TEST': SimpleNamespace(resistance=SimpleNamespace(bottom=100))}, {'TEST': 100}, {'TEST': 1}, {'TEST': (1, [])}, day)
    assert watches == []
    assert await scanner._prime_runtime('zero-oi-test', cfg, client, watches, day, {}, {}) == []
    client.get_historical.assert_not_awaited()


def test_incomplete_cache_envelope_is_a_miss(tmp_path):
    import json
    from app.services import gamma_move_history_cache as cache
    path = tmp_path / 'incomplete.json'
    path.write_text(json.dumps({'version': 2, 'rows': [], 'data_hash': 'incomplete'}))
    assert cache._read(path) is None
