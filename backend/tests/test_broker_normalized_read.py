"""Non-executing Kite normalization contract tests; synthetic provider only."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.exchanges.normalized_read import (
    BrokerReadFailure, NormalizedKiteReader, create_normalized_kite_reader,
    normalize_kite_quotes,
)

UTC = timezone.utc


class SyntheticKite:
    def __init__(self):
        self.calls = []
        self.connected = True
        self.quote_payload = {}

    async def get_profile(self):
        self.calls.append("profile")
        return {"user_id": "TEST123", "user_name": "Synthetic"}

    async def get_margins(self, segment=None):
        self.calls.append("margins")
        return {"equity": {"available": {"live_balance": 2000},
                           "utilised": {"debits": "500.12"}, "net": 1499.88}}

    async def get_positions_raw(self):
        self.calls.append("positions")
        return {"net": [{"exchange": "NFO", "tradingsymbol": "NIFTY26NOVFUT",
                         "quantity": -50, "average_price": "25100",
                         "last_price": 25090, "pnl": "500", "product": "NRML"}], "day": []}

    async def get_holdings(self):
        self.calls.append("holdings")
        return [{"exchange": "NSE", "tradingsymbol": "INFY",
                 "quantity": 10, "average_price": 1000}]

    async def get_orders(self):
        self.calls.append("orders")
        return [{"order_id": "O1", "exchange": "NFO", "tradingsymbol": "NIFTY26NOVFUT",
                 "status": "OPEN", "transaction_type": "BUY",
                 "quantity": 50, "filled_quantity": 0,
                 "order_timestamp": "2026-10-29 09:30:00"}]

    async def get_trades(self):
        self.calls.append("trades")
        return [{"trade_id": "T1", "order_id": "O1",
                 "tradingsymbol": "NIFTY26NOVFUT", "quantity": 5,
                 "average_price": "25002.55", "fill_timestamp": "2026-10-29 09:30:01"}]

    async def get_quote(self, instruments):
        self.calls.append("quotes")
        return self.quote_payload

    async def get_candles(self, instrument, resolution, limit=200):
        self.calls.append("candles")
        return [{"timestamp_ms": 1720000000000, "open": 100,
                 "high": 110, "low": 90, "close": 105, "volume": 10000}]

    async def get_option_chain(self, instrument):
        self.calls.append("chain")
        return [{"underlying": "NIFTY", "strike": 25000,
                 "expiry_date": "2026-11-05", "option_type": "call",
                 "bid": 100, "ask": 102, "mark_iv": 0.15,
                 "open_interest": 1000, "delta": 0.55,
                 "gamma": 0, "greeks_enriched": False}]

    async def test_connection(self):
        self.calls.append("health")
        return self.connected

    async def place_order(self, *a, **k):
        raise AssertionError("a read-only test invoked a broker write")


@pytest.mark.asyncio
async def test_observation_facade_never_exposes_order_writes():
    provider = SyntheticKite()
    reader = NormalizedKiteReader(provider, paper=False)
    for action in ("place_order", "cancel_order", "modify_order",
                   "flatten", "emergency_squareoff", "submit_order"):
        assert not hasattr(reader, action)
    account = await reader.account()
    assert account.account_id == "TEST123"
    funds = await reader.funds()
    assert funds[0].available_inr == 2000
    assert str(funds[0].used_inr) == "500.12"
    assert (await reader.positions())[0].net_quantity == -50
    assert (await reader.holdings())[0].quantity == 10
    assert (await reader.orders())[0].status == "open"
    assert (await reader.fills())[0].order_id == "O1"
    assert provider.calls == ["profile", "margins", "positions", "holdings", "orders", "trades"]


@pytest.mark.asyncio
async def test_health_paper_not_authenticated_and_live_status():
    provider = SyntheticKite()
    assert (await NormalizedKiteReader(provider, paper=True).health()).status == "simulation"
    assert "health" not in provider.calls
    live = NormalizedKiteReader(provider, paper=False)
    assert (await live.health()).authenticated
    provider.connected = False
    assert not (await live.health()).authenticated
    assert (await live.health()).status == "unavailable"


@pytest.mark.asyncio
async def test_quote_freshness_and_depth_and_unknown_time():
    provider = SyntheticKite()
    fixed = datetime(2026, 10, 29, 4, 0, tzinfo=UTC)  # IST 09:30
    provider.quote_payload = {
        "NSE:NIFTY 50": {
            "last_price": "25000.25", "timestamp": "2026-10-29 09:30:00",
            "depth": {"buy": [{"price": 25000, "quantity": 10, "orders": 2}],
                      "sell": [{"price": 25001, "quantity": 5, "orders": 1}]}
        },
        "NSE:INFY": {"last_price": 1450},
    }
    reader = NormalizedKiteReader(provider, paper=False)
    q = await reader.quotes(["NSE:NIFTY 50", "NSE:INFY"], fetched_at_utc=fixed)
    assert q[0].quality == "fresh" and not q[0].stale
    assert q[0].depth.bids[0].quantity == 10
    assert q[0].observed_at_utc == fixed
    assert q[1].quality == "unknown" and q[1].stale
    old = await reader.quotes(["NSE:NIFTY 50"], fetched_at_utc=fixed + timedelta(seconds=10))
    assert old[0].quality == "stale"
    depths = await reader.depth(["NSE:NIFTY 50"])
    assert len(depths) == 1


def test_quote_missing_or_invalid_is_never_assumed_fresh():
    fixed = datetime(2026, 10, 29, tzinfo=UTC)
    with pytest.raises(BrokerReadFailure):
        normalize_kite_quotes({}, ["NSE:NIFTY 50"], fetched_at_utc=fixed, stale_after_ms=5000)
    with pytest.raises(BrokerReadFailure):
        normalize_kite_quotes({"NSE:NIFTY 50": {"last_price": "NaN"}},
                              ["NSE:NIFTY 50"], fetched_at_utc=fixed, stale_after_ms=5000)
    with pytest.raises(ValueError):
        normalize_kite_quotes({}, ["NSE:NIFTY 50"], fetched_at_utc=fixed, stale_after_ms=0)


@pytest.mark.asyncio
async def test_history_and_chain_source_evidence():
    reader = NormalizedKiteReader(SyntheticKite(), paper=False)
    instrument = SimpleNamespace(underlying="NIFTY")
    candles = await reader.candles(instrument, "5m", limit=20)
    assert candles[0].source == "existing_market_adapter"
    options = await reader.option_chain(instrument)
    assert options[0].right == "CE"
    assert str(options[0].iv) == '0.15'
    assert options[0].gamma is None  # no evidence to treat default zero as measured Greek


@pytest.mark.asyncio
async def test_quotes_dont_echo_provider_secrets():
    class Unsafe(SyntheticKite):
        async def get_quote(self, instruments):
            raise RuntimeError("access_token=SECRET_EXAMPLE")
    reader = NormalizedKiteReader(Unsafe(), paper=False)
    with pytest.raises(BrokerReadFailure) as exc:
        await reader.quotes(["NSE:NIFTY 50"])
    assert str(exc.value) == "BROKER_QUOTES_UNAVAILABLE"
    assert "SECRET_EXAMPLE" not in str(exc.value)


def test_factory_reuses_existing_kite_registry(monkeypatch):
    from app.services.exchanges import adapter_factory
    provider = SyntheticKite()
    monkeypatch.setattr(adapter_factory, "create_account_adapter", lambda cfg: provider)
    cfg = SimpleNamespace(name="zerodha", is_paper=True)
    reader = create_normalized_kite_reader(cfg)
    assert isinstance(reader, NormalizedKiteReader)
    with pytest.raises(ValueError):
        create_normalized_kite_reader(SimpleNamespace(name="unknown", is_paper=True))
