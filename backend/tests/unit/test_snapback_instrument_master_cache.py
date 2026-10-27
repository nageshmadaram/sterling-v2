"""Instrument-master caching must speed the board without reusing foreign accounts."""
from datetime import datetime, timedelta, timezone

import pytest

from app.services import snapback


_IST = timezone(timedelta(hours=5, minutes=30))


class MasterClient:
    def __init__(self):
        self.calls = []

    async def search_instruments(self, query, exchange, limit):
        self.calls.append((exchange, limit))
        return [{"tradingsymbol": f"{exchange}-INSTRUMENT"}]


@pytest.mark.asyncio
async def test_same_account_reuses_all_four_instrument_masters():
    c = MasterClient()
    st = snapback.ScanState()
    first = await snapback._instrument_dumps_for_scan(
        c, st, account_key="KITE-A")
    second = await snapback._instrument_dumps_for_scan(
        c, st, account_key="KITE-A")
    assert first == second
    assert [name for name, _ in c.calls] == ["NFO", "BFO", "NSE", "BSE"]


@pytest.mark.asyncio
async def test_changed_account_forces_fresh_masters():
    c = MasterClient()
    st = snapback.ScanState()
    await snapback._instrument_dumps_for_scan(c, st, account_key="KITE-A")
    await snapback._instrument_dumps_for_scan(c, st, account_key="KITE-B")
    assert len(c.calls) == 8
    assert st.instrument_cache[2] == "KITE-B"


@pytest.mark.asyncio
async def test_unbound_account_cannot_reuse_instrument_masters():
    c = MasterClient()
    st = snapback.ScanState()
    await snapback._instrument_dumps_for_scan(c, st, account_key="")
    await snapback._instrument_dumps_for_scan(c, st, account_key="")
    assert len(c.calls) == 8


@pytest.mark.asyncio
async def test_new_ist_day_invalidates_cache(monkeypatch):
    c = MasterClient()
    st = snapback.ScanState()
    dt = datetime(2026, 10, 27, 15, 25, tzinfo=_IST)
    monkeypatch.setattr(snapback, "_wall_now", lambda: dt)
    await snapback._instrument_dumps_for_scan(c, st, account_key="KITE-A")
    monkeypatch.setattr(snapback, "_wall_now", lambda: dt + timedelta(days=1))
    await snapback._instrument_dumps_for_scan(c, st, account_key="KITE-A")
    assert len(c.calls) == 8


@pytest.mark.asyncio
async def test_old_instrument_snapshot_is_reloaded():
    c = MasterClient()
    st = snapback.ScanState()
    await snapback._instrument_dumps_for_scan(c, st, account_key="KITE-A")
    timestamp, date, account, rows = st.instrument_cache
    st.instrument_cache = (timestamp - 601, date, account, rows)
    await snapback._instrument_dumps_for_scan(c, st, account_key="KITE-A")
    assert len(c.calls) == 8


@pytest.mark.asyncio
async def test_unreadable_instrument_master_fails_closed_and_drops_cache():
    class Unreadable(MasterClient):
        async def search_instruments(self, query, exchange, limit):
            self.calls.append((exchange, limit))
            return None if exchange == "NFO" else []

    c = Unreadable()
    st = snapback.ScanState()
    with pytest.raises(snapback.UnreadableCandles):
        await snapback._instrument_dumps_for_scan(c, st, account_key="KITE-A")
    assert st.instrument_cache is None
