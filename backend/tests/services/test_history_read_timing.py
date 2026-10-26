import asyncio
import pytest
from app.core.history_read_timing import history_scope, phase, event


def test_no_scope_and_exception_cleanup():
    with phase('http_request'):
        pass
    metrics = {}
    with pytest.raises(ValueError):
        with history_scope(metrics, 'option'):
            with phase('cache_read'):
                event('cache_hit')
                raise ValueError('private data')
    event('cache_miss')
    assert metrics['option']['cache_read']['count'] == 1
    assert metrics['option']['cache_read']['seconds'] >= 0
    assert metrics['option']['cache_hit'] == 1
    assert 'cache_miss' not in metrics['option']
    assert 'private' not in str(metrics)


@pytest.mark.asyncio
async def test_concurrent_scopes_and_cancellation_are_isolated():
    left, right = {}, {}
    async def read(metrics, role):
        with history_scope(metrics, role):
            with phase('global_queue'):
                await asyncio.sleep(0)
                event('http_attempt')
    await asyncio.gather(read(left, 'option'), read(right, 'underlying'))
    assert set(left) == {'option'}
    assert set(right) == {'underlying'}
    assert left['option']['http_attempt'] == 1
    async def cancel():
        with history_scope(left, 'option'):
            with phase('http_request'):
                raise asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await cancel()
    assert left['option']['http_request']['count'] == 1
    event('after_cancel')
    assert 'after_cancel' not in left['option']


@pytest.mark.asyncio
async def test_cache_hit_bypasses_pacing_and_broker(tmp_path, monkeypatch):
    from datetime import datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app.services import gamma_move_history_cache as cache, gamma_move_scanner as scanner
    monkeypatch.setenv('STERLING_GAMMA_CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(cache, '_now', lambda: datetime(2026, 10, 23, 16, tzinfo=cache.IST))
    rows = [['2026-10-22T09:15:00+0530', 10, 12, 9, 11, 100]]
    client = SimpleNamespace(_account_id='private-owner', get_historical=AsyncMock(return_value=rows))
    pacer = SimpleNamespace(wait=AsyncMock())
    metrics = {}
    args = (client, pacer, 123, '5minute', '2026-10-22', '2026-10-22', False)
    with history_scope(metrics, 'option'):
        assert await scanner._historical(*args) == rows
        assert await scanner._historical(*args) == rows
    detail = metrics['option']
    assert detail['cache_miss'] == detail['cache_hit'] == detail['fetch_attempt'] == 1
    assert detail['cache_read']['count'] == 2
    assert detail['cache_write']['count'] == detail['local_pacer']['count'] == 1
    client.get_historical.assert_awaited_once()
    assert 'private-owner' not in str(metrics)


@pytest.mark.asyncio
async def test_real_client_queue_http_decode_and_failures(monkeypatch):
    from unittest.mock import AsyncMock
    import httpx
    from app.services.exchanges.kite import client as module
    # No sockets: exercise the actual historical client request boundary.
    client = module.KiteClient(api_key='test', access_token='test')
    monkeypatch.setattr(module._GLOBAL_KITE_HISTORICAL_LIMITER, 'acquire', AsyncMock())
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={'status': 'success', 'data': {'candles': []}}))
    async with httpx.AsyncClient(transport=transport, base_url='https://api.kite.trade') as http:
        monkeypatch.setattr(client, '_get_client', AsyncMock(return_value=http))
        metrics = {}
        with history_scope(metrics, 'underlying'):
            await client.get_historical(123, '5minute', '2026-10-22', '2026-10-22')
        detail = metrics['underlying']
        assert detail['http_attempt'] == 1
        for key in ['global_queue', 'client_acquisition', 'http_request', 'response_decode']:
            assert detail[key]['count'] == 1
        assert 'test' not in str(metrics)
