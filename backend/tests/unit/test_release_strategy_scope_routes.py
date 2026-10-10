"""Release-scope contract: only SuperTrend, Gamma-Unwind and Snapback may originate."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import config


USER = SimpleNamespace(user_id="operator")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("call", "name"),
    [
        (lambda: config.adaptive_edge_scan(USER), "adaptive_edge"),
        (lambda: config.adaptive_edge_arm({"signal_id": "ae-1"}, USER), "adaptive_edge"),
        (lambda: config.intraday_scan(USER), "intraday"),
        (lambda: config.intraday_arm({"signal_id": "intra-1"}, USER), "intraday"),
    ],
)
async def test_retired_strategy_origination_routes_are_gone(call, name):
    with pytest.raises(HTTPException) as exc:
        await call()

    assert exc.value.status_code == 410
    assert name in str(exc.value.detail)
    assert "Settings → Archived" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_retired_intraday_exit_remains_available(monkeypatch):
    seen = {}

    async def exit_one(uid, symbol):
        seen.update(uid=uid, symbol=symbol)
        return {"ok": True}

    import app.services.intraday_runner as runner

    monkeypatch.setattr(runner, "exit_one", exit_one)

    result = await config.intraday_exit({"symbol": "NIFTY26OCT25000CE"}, USER)

    assert result == {"ok": True}
    assert seen == {"uid": "operator", "symbol": "NIFTY26OCT25000CE"}


@pytest.mark.asyncio
async def test_retired_adaptive_edge_square_off_remains_available(monkeypatch):
    seen = {}

    async def square_off_all(uid):
        seen["uid"] = uid
        return {"closed": ["OPT"], "failed": []}

    import app.services.adaptive_edge_runner as runner

    monkeypatch.setattr(runner, "square_off_all", square_off_all)

    result = await config.adaptive_edge_square_off(USER)

    assert result == {"closed": ["OPT"], "failed": []}
    assert seen == {"uid": "operator"}
