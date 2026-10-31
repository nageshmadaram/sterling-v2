"""Quote freshness must follow the active clock after a process restarts.

This is offline; no real session, transport, or broker order is constructed.
"""
from app.services.simulation import quotes


def test_default_quote_wall_clock_is_read_at_call_time(monkeypatch):
    now = 1791780360.0
    # Simulate Freezegun changing the time provider after QuoteSource was
    # imported. A captured function default would keep reading the old clock.
    monkeypatch.setattr(quotes.time, "time", lambda: now)
    source = quotes.QuoteSource(lambda: None, owner="synthetic-owner",
                                use_websocket=False)
    assert source._wall() == now
    stale = quotes.Book(
        exchange="NFO", symbol="SYNTHETIC26NOVFUT",
        ltp=100.0, bid=99.9, ask=100.1, bid_qty=1, ask_qty=1,
        fetched_at=now, source="rest", quote_ts=now - 65,
    )
    fresh = quotes.Book(
        exchange="NFO", symbol="SYNTHETIC26NOVFUT",
        ltp=100.0, bid=99.9, ask=100.1, bid_qty=1, ask_qty=1,
        fetched_at=now, source="rest", quote_ts=now - 2,
    )
    assert source._stale(stale, source._wall())
    assert not source._stale(fresh, source._wall())


def test_explicit_quote_clock_is_preserved(monkeypatch):
    monkeypatch.setattr(quotes.time, "time", lambda: 300)
    source = quotes.QuoteSource(lambda: None, owner="synthetic-owner",
                                wall_clock=lambda: 200, use_websocket=False)
    assert source._wall() == 200
