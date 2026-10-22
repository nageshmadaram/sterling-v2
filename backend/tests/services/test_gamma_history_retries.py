from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.services import gamma_move_scanner as scanner
from app.services.exchanges.kite.errors import KiteTokenError

@pytest.mark.asyncio
async def test_transient_history_timeout_retries_same_read(monkeypatch):
    sleep=AsyncMock();monkeypatch.setattr(scanner.asyncio,'sleep',sleep)
    client=SimpleNamespace(get_historical=AsyncMock(side_effect=[TimeoutError(),{'candles':[[1,2,3]]}]))
    pacer=SimpleNamespace(wait=AsyncMock())
    rows=await scanner._historical(client,pacer,123,'5minute','from','to',False)
    assert rows==[[1,2,3]] and client.get_historical.await_count==2
    assert pacer.wait.await_count==2 and sleep.await_count==1
    assert client.get_historical.await_args_list[0]==client.get_historical.await_args_list[1]

@pytest.mark.asyncio
async def test_history_retry_exhaustion_does_not_fabricate_rows(monkeypatch):
    monkeypatch.setattr(scanner.asyncio,'sleep',AsyncMock())
    client=SimpleNamespace(get_historical=AsyncMock(side_effect=TimeoutError()))
    with pytest.raises(TimeoutError) as error:
        await scanner._historical(client,SimpleNamespace(wait=AsyncMock()),123,'5minute','from','to',False)
    assert client.get_historical.await_count==3
    assert any('attempts=3' in note for note in error.value.__notes__)

@pytest.mark.asyncio
async def test_expired_session_is_not_retried(monkeypatch):
    sleep=AsyncMock();monkeypatch.setattr(scanner.asyncio,'sleep',sleep)
    client=SimpleNamespace(get_historical=AsyncMock(side_effect=KiteTokenError('expired')))
    with pytest.raises(KiteTokenError):
        await scanner._historical(client,SimpleNamespace(wait=AsyncMock()),123,'5minute','from','to',False)
    assert client.get_historical.await_count==1
    sleep.assert_not_awaited()
