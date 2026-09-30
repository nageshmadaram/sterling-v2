"""Shared Kite ticks must not be removed or downgraded by a different consumer."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.exchanges.kite import constants as K, ticker_manager as tm
from app.services.exchanges.kite.ticker import KiteTicker


@pytest.mark.asyncio
async def test_protection_claim_survives_ui_unsubscribe(monkeypatch):
    ticker = SimpleNamespace(
        subscribe=AsyncMock(),
        unsubscribe=AsyncMock(),
        status=lambda: {"subscribed": [777]},
    )
    monkeypatch.setattr(tm, "_owners", {})
    monkeypatch.setattr(tm, "_tickers", {"u": ticker})
    monkeypatch.setattr(tm, "ensure", AsyncMock(return_value=ticker))

    await tm.subscribe("u", [777], mode=K.MODE_FULL)
    await tm.subscribe(
        "u", [777], mode=K.MODE_LTP,
        owner=tm.protection_owner("NIFTY26OCT25000CE"),
    )
    assert tm.owners_of("u", 777) == {
        "*", "kite-protection:NIFTY26OCT25000CE",
    }

    result = await tm.unsubscribe("u", [777])
    assert result["kept_for_owners"]["777"] == ["kite-protection:NIFTY26OCT25000CE"]
    ticker.unsubscribe.assert_not_awaited()

    await tm.release(
        "u", [777], owner=tm.protection_owner("NIFTY26OCT25000CE"),
    )
    ticker.unsubscribe.assert_awaited_once_with([777])


@pytest.mark.asyncio
async def test_later_ltp_request_cannot_downgrade_full_ticker_mode():
    ticker = KiteTicker(
        api_key="test-key", access_token="test-token",
        on_ticks=lambda *_args: None,
    )
    await ticker.subscribe([777], mode=K.MODE_FULL)
    await ticker.subscribe([777, 888], mode=K.MODE_LTP)

    assert ticker._subscribed[777] == K.MODE_FULL
    assert ticker._subscribed[888] == K.MODE_LTP

    await ticker.subscribe([888], mode=K.MODE_QUOTE)
    assert ticker._subscribed[888] == K.MODE_QUOTE
    await ticker.subscribe([888], mode=K.MODE_LTP)
    assert ticker._subscribed[888] == K.MODE_QUOTE


@pytest.mark.asyncio
async def test_invalid_ticker_mode_does_not_mutate_subscriptions():
    ticker = KiteTicker(
        api_key="test-key", access_token="test-token",
        on_ticks=lambda *_args: None,
    )
    with pytest.raises(ValueError, match="invalid Kite subscription mode"):
        await ticker.subscribe([777], mode="unknown")
    assert ticker._subscribed == {}


def test_protection_callers_have_named_subscription_and_release():
    app = Path(__file__).resolve().parents[1] / "app"
    protection = (app / "services/kite_engine/protection.py").read_text()
    lifecycle = (app / "services/kite_engine/execution_lifecycle.py").read_text()
    monitor = (app / "services/kite_engine/monitor.py").read_text()
    service = (app / "services/kite_engine/service.py").read_text()

    assert "owner=ticker_manager.protection_owner(symbol)" in protection
    assert "owner=ticker_manager.protection_owner(p.symbol)" in lifecycle
    assert monitor.count("owner=ticker_manager.protection_owner(p.symbol)") >= 2
    assert "owner=ticker_manager.protection_owner(p.symbol)" in service
