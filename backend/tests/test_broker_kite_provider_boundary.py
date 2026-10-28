"""Wire the actual Sterling KiteClient read adapter to synthetic transport replies.

No network, credentials, real broker calls, trading or order-path changes.
"""
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.services.exchanges.kite.client import KiteClient
from app.services.exchanges.normalized_read import (
    BrokerReadFailure, NormalizedKiteReader,
)


@pytest.mark.asyncio
async def test_existing_kite_adapter_normalizes_real_method_shapes(monkeypatch):
    raw = {
        "/user/profile": {"user_id": "TEST123", "user_name": "Fixture"},
        "/user/margins": {
            "equity": {
                "available": {"live_balance": 4000.20},
                "utilised": {"debits": "25.02"},
                "net": 3975.18,
            },
        },
        "/portfolio/positions": {"net": [{
            "exchange": "NFO", "tradingsymbol": "NIFTY26NOVFUT", "product": "NRML",
            "quantity": -50, "average_price": 25200, "last_price": 25190, "pnl": 500,
        }], "day": []},
        "/portfolio/holdings": [{
            "exchange": "NSE", "tradingsymbol": "INFY", "quantity": 1,
            "average_price": 1200, "last_price": 1250,
        }],
        "/orders": [{
            "order_id": "SIM_ORDER_1", "exchange": "NFO",
            "tradingsymbol": "NIFTY26NOVFUT", "status": "COMPLETE",
            "quantity": 50, "filled_quantity": 50, "transaction_type": "SELL",
            "order_timestamp": "2026-10-29 09:30:00",
        }],
        "/trades": [{
            "trade_id": "SIM_FILL_1", "order_id": "SIM_ORDER_1",
            "tradingsymbol": "NIFTY26NOVFUT", "quantity": 50,
            "average_price": 25200, "fill_timestamp": "2026-10-29 09:30:02",
        }],
        "/quote": {
            "NFO:NIFTY26NOVFUT": {
                "last_price": 25190, "timestamp": "2026-10-29 09:30:00",
                "depth": {"buy": [{"price": 25189.95, "quantity": 50, "orders": 2}],
                          "sell": [{"price": 25190.05, "quantity": 30, "orders": 1}]},
            },
        },
    }
    observed = []

    async def fake_get(path, params=None):
        observed.append(path)
        return raw[path]

    client = KiteClient(api_key="synthetic-api-key",
                        access_token="synthetic-access-token", is_paper=True)
    monkeypatch.setattr(client, "_auth_get", fake_get)
    reader = NormalizedKiteReader(client, paper=True)
    assert (await reader.account()).account_id == "TEST123"
    assert (await reader.funds())[0].used_inr == Decimal("25.02")
    assert (await reader.positions())[0].net_quantity == -50
    assert (await reader.holdings())[0].symbol == "INFY"
    assert (await reader.orders())[0].status == "filled"
    assert (await reader.fills())[0].price == 25200
    quotes = await reader.quotes(
        ["NFO:NIFTY26NOVFUT"],
        fetched_at_utc=datetime(2026, 10, 29, 4, 0, tzinfo=timezone.utc),
    )
    assert quotes[0].quality == "fresh"
    assert quotes[0].exchange == "NFO"
    assert quotes[0].depth.asks[0].quantity == 30
    assert observed == [
        "/user/profile", "/user/margins", "/portfolio/positions",
        "/portfolio/holdings", "/orders", "/trades", "/quote",
    ]
    assert not hasattr(reader, "place_order")


@pytest.mark.asyncio
async def test_actual_kite_adapter_broker_denial_becomes_sanitized_observation(monkeypatch):
    client = KiteClient(api_key="synthetic", access_token="synthetic", is_paper=True)
    async def denied(path, params=None):
        raise PermissionError("access_token=SYNTHETIC_NOT_TO_LOG")
    monkeypatch.setattr(client, "_auth_get", denied)
    reader = NormalizedKiteReader(client, paper=False)
    with pytest.raises(BrokerReadFailure) as err:
        await reader.account()
    assert err.value.code == "BROKER_ACCOUNT_UNAVAILABLE"
    assert "SYNTHETIC_NOT_TO_LOG" not in str(err.value)
