from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.services.exchanges.kite import accounts
from app.services.kite_engine import service


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, 0, 1, "false", "missing"])
@pytest.mark.parametrize("side", ["BUY", "SELL"])
async def test_manual_order_requires_exact_boolean_mode(monkeypatch, mode, side):
    client = SimpleNamespace(_account_id="account", place_order=AsyncMock())
    if mode != "missing":
        client._is_paper = mode
    gate = Mock(side_effect=AssertionError("Unknown mode must fail before admission"))
    monkeypatch.setattr(accounts, "get_active", lambda uid: SimpleNamespace(id="account"))
    monkeypatch.setattr(accounts, "acquire_client", AsyncMock(return_value=client))
    monkeypatch.setattr(service.live_safety, "assert_safe_to_trade", gate)

    result = await service.place_manual_order("u", "OPT", side, 50)

    assert result == {"status": "blocked", "reason": "execution_mode_unverified"}
    gate.assert_not_called()
    client.place_order.assert_not_awaited()
