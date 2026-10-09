"""The price source: real reads only, short TTL, fail closed."""
import asyncio

import pytest

from app.services.exchanges.kite.errors import KiteInputError, KiteTokenError
from app.services.simulation import quotes as q
from tests.unit.simulation.sim_fakes import FUT, OPT, OPT_PE, FakeReal


def _source(real=None, **kw):
    real = real or FakeReal()
    t = [100.0]
    src = q.QuoteSource(lambda: real, owner="default", clock=lambda: t[0], use_websocket=False, **kw)
    return src, real, t


async def test_a_book_carries_the_real_touch_and_ltp():
    src, real, _ = _source()
    real.set_quote(OPT, 101.0, bid=100.9, ask=101.2, qty=450)
    book = await src.book("NFO", "NIFTY2611325000CE")
    assert (book.ltp, book.bid, book.ask, book.bid_qty, book.ask_qty) == (101.0, 100.9, 101.2, 450, 450)
    assert book.source == "rest" and book.key == OPT and book.token == 1001


async def test_a_burst_of_reads_asks_the_broker_once_and_batches_the_keys():
    src, real, t = _source()
    await src.books([OPT, OPT_PE, FUT])
    assert [c[0] for c in real.calls] == ["get_quote"] and len(real.calls[0][1]) == 3
    await src.books([OPT])
    await src.book("NFO", "NIFTY2611325000PE")
    assert len(real.calls) == 1                       # inside the TTL
    t[0] += 1.5
    await src.books([OPT])
    assert len(real.calls) == 2                       # TTL expired: a fresh read
    assert real.calls[1][1] == [OPT]


async def test_concurrent_callers_share_one_read():
    src, real, _ = _source()
    await asyncio.gather(*[src.books([OPT]) for _ in range(5)])
    assert len(real.calls) == 1


async def test_strict_refuses_an_unpriced_key_and_lenient_skips_it():
    src, real, _ = _source()
    with pytest.raises(q.MarketDataUnavailable) as info:
        await src.books([OPT, "NFO:NIFTY2611325000XX"])
    assert "NIFTY2611325000XX" in str(info.value)
    got = await src.books([OPT, "NFO:NIFTY2611325000XX"], strict=False)
    assert list(got) == [OPT]


async def test_a_failed_real_read_is_market_data_unavailable_and_is_a_refusal_not_an_invention():
    src, real, _ = _source()
    real.fail = KiteTokenError("Kite session expired", error_type="TokenException")
    with pytest.raises(q.MarketDataUnavailable) as info:
        await src.book("NFO", "NIFTY2611325000CE")
    assert isinstance(info.value.__cause__, KiteTokenError)
    assert isinstance(info.value, PermissionError)          # the canonical path reads this as "not sent"
    assert await src.books([OPT], strict=False) == {}


async def test_a_quote_with_no_price_at_all_is_never_turned_into_one():
    src, real, _ = _source()
    real.set_quote(OPT, 0.0, bid=0.0, ask=0.0)
    with pytest.raises(q.MarketDataUnavailable):
        await src.book("NFO", "NIFTY2611325000CE")


async def test_an_empty_side_of_the_book_is_reported_as_zero_not_filled_in():
    src, real, _ = _source()
    real.set_quote(OPT, 50.0, bid=49.9, ask=0.0)
    book = await src.book("NFO", "NIFTY2611325000CE")
    assert (book.bid, book.ask, book.ltp) == (49.9, 0.0, 50.0)


async def test_a_stale_quote_is_dropped_when_an_age_limit_is_set():
    real = FakeReal()
    real.set_quote(OPT, 100.0, age_s=600)
    src, _, _ = _source(real, max_quote_age_s=120.0)
    with pytest.raises(q.MarketDataUnavailable):
        await src.book("NFO", "NIFTY2611325000CE")
    real.set_quote(OPT, 100.0, age_s=5)
    src.invalidate()
    assert (await src.book("NFO", "NIFTY2611325000CE")).ltp == 100.0


async def test_providers_may_be_sync_or_async_and_are_called_each_time():
    real = FakeReal()
    seen = []

    async def provider():
        seen.append(1)
        return real
    src = q.QuoteSource(provider, owner="default", use_websocket=False, ttl_s=0)
    await src.book("NFO", "NIFTY2611325000CE")
    await src.book("NFO", "NIFTY2611325000CE")
    assert len(seen) == 2


async def test_a_provider_that_raises_means_no_market_data():
    def provider():
        raise RuntimeError("not logged in to Kite")
    src = q.QuoteSource(provider, owner="default", use_websocket=False)
    with pytest.raises(q.MarketDataUnavailable, match="not logged in"):
        await src.book("NFO", "NIFTY2611325000CE")
    src = q.QuoteSource(lambda: None, owner="default", use_websocket=False)
    with pytest.raises(q.MarketDataUnavailable):
        await src.book("NFO", "NIFTY2611325000CE")


async def test_a_simulated_client_or_another_owners_client_is_never_a_price_source():
    class Sim:
        _is_simulated = True
    with pytest.raises(q.MarketDataUnavailable, match="simulated"):
        await q.resolve_real_client(lambda: Sim(), owner="default")
    with pytest.raises(q.MarketDataUnavailable, match="different owner"):
        await q.resolve_real_client(lambda: FakeReal(owner="mallory"), owner="default")
    assert await q.resolve_real_client(lambda: FakeReal(owner="default"), owner="sim:default")


async def test_a_fresh_websocket_book_is_preferred_and_a_stale_stream_falls_back_to_rest():
    real = FakeReal()
    ticks = {1001: {"instrument_token": 1001, "mode": "full", "last_price": 111.0,
                    "depth": {"buy": [{"price": 110.9, "quantity": 75, "orders": 2}],
                              "sell": [{"price": 111.1, "quantity": 90, "orders": 2}]}}}
    stream = {"live": True}
    src = q.QuoteSource(lambda: real, owner="default", ttl_s=0,
                        tick_reader=lambda owner: ticks if stream["live"] else None)
    await src.instrument("NFO", "NIFTY2611325000CE")                # learns the token
    book = await src.book("NFO", "NIFTY2611325000CE")
    assert (book.source, book.bid, book.ask, book.ltp) == ("ws", 110.9, 111.1, 111.0)
    assert real.calls == []                                         # no REST read at all
    stream["live"] = False
    book = await src.book("NFO", "NIFTY2611325000CE")
    assert book.source == "rest" and book.ltp == 100.0
    ticks[1001]["depth"] = {}
    stream["live"] = True
    assert (await src.book("NFO", "NIFTY2611325000CE")).source == "rest"     # a tick without depth is not a book


async def test_instrument_meta_comes_from_the_real_master_and_is_cached():
    src, real, _ = _source()
    meta = await src.instrument("NFO", "NIFTY2611325000CE")
    assert (meta.token, meta.tick_size, meta.lot_size, meta.instrument_type, meta.strike) == (
        1001, 0.05, 75, "CE", 25000.0)
    await src.instrument("NFO", "NIFTY2611325000CE")
    await src.instrument("NFO", "NIFTY2611325000PE")
    assert real._instruments.loads == 2                             # one load per fresh symbol, none for a repeat
    assert src.peek_instrument("NFO", "NIFTY2611325000CE") == meta
    with pytest.raises(q.InstrumentNotFound) as info:
        await src.instrument("NFO", "NOPE")
    assert isinstance(info.value, KiteInputError) and "Invalid instrument NFO:NOPE" in str(info.value)


async def test_instrument_lookup_falls_back_to_search_when_the_client_has_no_cache():
    inner = FakeReal()

    class SearchOnly:
        async def search_instruments(self, query, exchange="", limit=50):
            return await inner.search_instruments(query, exchange, limit)

    src = q.QuoteSource(lambda: SearchOnly(), owner="default", use_websocket=False)
    assert (await src.instrument("NFO", "NIFTY26OCTFUT")).instrument_type == "FUT"
    with pytest.raises(q.InstrumentNotFound):
        await src.instrument("NFO", "NIFTY2611399999CE")
