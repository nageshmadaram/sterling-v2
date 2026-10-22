"""HTTP route dispatch must use the maintained Navigator chart implementation."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from app.api.v1.endpoints import navigator
from app.engines.navigator.quality import CandleValidationError
from app.services.exchanges.kite import accounts


def test_chart_has_one_unambiguous_route():
    routes=[r for r in navigator.router.routes if r.path.endswith('/chart/{underlying}')]
    assert len(routes)==1
    assert routes[0].endpoint is navigator.get_chart_overlay


@pytest.mark.asyncio
async def test_chart_candle_errors_are_validation_responses(monkeypatch):
    monkeypatch.setattr(navigator,'_default_underlyings',lambda uid:['NIFTY 50'])
    monkeypatch.setattr(navigator.config_store,'get',lambda *a,**kw:SimpleNamespace())
    monkeypatch.setattr(accounts,'get_active',lambda uid:SimpleNamespace(id='fake',user_id=uid))
    monkeypatch.setattr(accounts,'acquire_client',AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(navigator,'_resolve_chart_token',AsyncMock(return_value=123))
    build=AsyncMock(side_effect=CandleValidationError('malformed candles'))
    monkeypatch.setattr(navigator.chart_series,'build_chart_series',build)
    route=next(r for r in navigator.router.routes if r.path.endswith('/chart/{underlying}'))
    with pytest.raises(HTTPException) as result:
        await route.endpoint('NIFTY 50',bars=1,user=SimpleNamespace(user_id='qa'))
    assert result.value.status_code==422
    assert 'NAVIGATOR_CANDLES_INVALID' in result.value.detail
    assert build.await_args.kwargs['bars']==60
