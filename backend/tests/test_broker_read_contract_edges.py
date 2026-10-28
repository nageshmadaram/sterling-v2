"""Phase 2B read-contract edge cases; no real broker session or trading."""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.exchanges.normalized_read import (
    BrokerReadFailure, NormalizedKiteReader, normalize_kite_quotes,
)
from tests.test_broker_normalized_read import SyntheticKite


NOW = datetime(2026, 10, 29, 4, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("quote,code", [
    ({"last_price": 0}, "INVALID_BROKER_NUMBER"),
    ({"last_price": "-1"}, "INVALID_BROKER_NUMBER"),
    ({"last_price": "Infinity"}, "INVALID_BROKER_NUMBER"),
    ({"last_price": "not-a-price"}, "INVALID_BROKER_NUMBER"),
    ({"last_price": 100, "depth": {"buy": [{"price": 99, "quantity": -1}]}},
     "INVALID_BROKER_DEPTH"),
    ({"last_price": 100, "depth": {"buy": [{"price": 0, "quantity": 3}]}},
     "INVALID_BROKER_DEPTH"),
    ({"last_price": 100, "depth": {"buy": [{"price": 99, "quantity": 3, "orders": "one"}]}},
     "INVALID_BROKER_DEPTH"),
    ({"last_price": 100, "depth": {"sell": "not-a-list"}},
     "INVALID_BROKER_PAYLOAD"),
    ({"last_price": 100, "timestamp": "garbage"}, "INVALID_BROKER_TIMESTAMP"),
])
def test_provider_quote_rejects_corrupt_data(quote, code):
    with pytest.raises(BrokerReadFailure) as err:
        normalize_kite_quotes(
            {"NFO:NIFTY26NOVFUT": quote},
            ["NFO:NIFTY26NOVFUT"], fetched_at_utc=NOW, stale_after_ms=5000,
        )
    assert err.value.code == code


def test_exchange_and_partial_quote_never_silent_success():
    data = {
        "NFO:NIFTY26NOVFUT": {"last_price": "25000.05", "timestamp": "2026-10-29 09:30:00"},
        "NSE:INFY": {"last_price": "1500"},
    }
    rows = normalize_kite_quotes(
        data, ["NFO:NIFTY26NOVFUT", "NSE:INFY"], fetched_at_utc=NOW, stale_after_ms=5000,
    )
    assert rows[0].exchange == "NFO"
    assert rows[0].last_price == Decimal("25000.05")
    assert rows[1].exchange == "NSE"
    assert rows[1].stale and rows[1].quality == "unknown"
    with pytest.raises(BrokerReadFailure):
        normalize_kite_quotes(data, ["NFO:NIFTY26NOVFUT", "BSE:UNKNOWN"],
                              fetched_at_utc=NOW, stale_after_ms=5000)
    for malformed_symbol in ("NSE:", ":INFY", "NSE", ""):
        with pytest.raises(ValueError):
            normalize_kite_quotes(data, [malformed_symbol], fetched_at_utc=NOW,
                                  stale_after_ms=5000)


@pytest.mark.asyncio
async def test_order_status_known_pending_and_unsupported_are_not_filled():
    provider = SyntheticKite()
    async def orders():
        data = await SyntheticKite.get_orders(provider)
        data[0]["status"] = "TRIGGER PENDING"
        return data
    provider.get_orders = orders
    reader = NormalizedKiteReader(provider, paper=False)
    assert (await reader.orders())[0].status == "pending"
    async def unexpected():
        data = await SyntheticKite.get_orders(provider)
        data[0]["status"] = "PROVIDER_UNRECOGNIZED"
        return data
    provider.get_orders = unexpected
    assert (await reader.orders())[0].status == "unknown"
    async def complete():
        data = await SyntheticKite.get_orders(provider)
        data[0]["status"] = "COMPLETE"
        return data
    provider.get_orders = complete
    assert (await reader.orders())[0].status == "filled"


@pytest.mark.asyncio
async def test_no_position_or_order_is_inferred_from_partial_payload():
    class Missing(SyntheticKite):
        async def get_positions_raw(self):
            return {"day": []}
        async def get_orders(self):
            return [{"order_id": "O1", "status": "COMPLETE", "quantity": 50}]
    reader = NormalizedKiteReader(Missing(), paper=False)
    with pytest.raises(BrokerReadFailure) as positions:
        await reader.positions()
    assert positions.value.code == "INVALID_BROKER_PAYLOAD"
    with pytest.raises(BrokerReadFailure) as orders:
        await reader.orders()
    assert orders.value.code == "MISSING_REQUIRED_FIELD"


@pytest.mark.asyncio
async def test_unsupported_option_right_fails_closed():
    class Unsupported(SyntheticKite):
        async def get_option_chain(self, instrument):
            rows = await super().get_option_chain(instrument)
            rows[0]["option_type"] = "BINARY"
            return rows
    reader = NormalizedKiteReader(Unsupported(), paper=False)
    with pytest.raises(BrokerReadFailure) as err:
        await reader.option_chain(SimpleNamespace(underlying="NIFTY"))
    assert err.value.code == "UNSUPPORTED_OPTION_RIGHT"


@pytest.mark.asyncio
async def test_denied_broker_access_sanitized_across_read_surface():
    class Denied(SyntheticKite):
        async def get_profile(self):
            raise PermissionError("access_token=synthetic-not-to-expose")
        async def get_margins(self, segment=None):
            raise PermissionError("access_token=synthetic-not-to-expose")
        async def get_orders(self):
            raise PermissionError("access_token=synthetic-not-to-expose")
        async def get_quote(self, instruments):
            raise PermissionError("access_token=synthetic-not-to-expose")
    reader = NormalizedKiteReader(Denied(), paper=False)
    for coro, expected in (
        (reader.account(), "BROKER_ACCOUNT_UNAVAILABLE"),
        (reader.funds(), "BROKER_FUNDS_UNAVAILABLE"),
        (reader.orders(), "BROKER_ORDERS_UNAVAILABLE"),
        (reader.quotes(["NSE:INFY"]), "BROKER_QUOTES_UNAVAILABLE"),
    ):
        with pytest.raises(BrokerReadFailure) as exc:
            await coro
        assert exc.value.code == expected
        assert "synthetic-not-to-expose" not in str(exc.value)


@pytest.mark.asyncio
async def test_provider_schema_mismatch_is_normalized_not_leaked():
    class Mismatched(SyntheticKite):
        async def get_holdings(self):
            return {"holdings": []}
        async def get_trades(self):
            return [{"fill_id": "T1", "symbol": "NFO:ABC"}]
        async def get_candles(self, instrument, resolution, limit=200):
            return [{"high": 10}]
    reader = NormalizedKiteReader(Mismatched(), paper=False)
    for coro, code in (
        (reader.holdings(), "INVALID_BROKER_PAYLOAD"),
        (reader.fills(), "MISSING_REQUIRED_FIELD"),
        (reader.candles(SimpleNamespace(underlying="NIFTY"), "5m"), "BROKER_HISTORY_UNAVAILABLE"),
    ):
        with pytest.raises(BrokerReadFailure) as exc:
            await coro
        assert exc.value.code == code
