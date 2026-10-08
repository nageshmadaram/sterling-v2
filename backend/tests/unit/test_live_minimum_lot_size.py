"""The lot cap measures an order against the broker's lot size, never the caller's.

A sizing bug that sends 150 with payload lot_size=150 used to read as one lot and slip
under a one-lot cap. The broker instrument dump is authoritative; the payload is used
only when the broker offers no lookup at all, and a disagreement refuses.
"""
from types import SimpleNamespace

import pytest

from app.services import db, live_minimum_gate as gate

SYMBOL = "NIFTY26OCT25000CE"


def _request(quantity, payload=None):
    return SimpleNamespace(
        uid="u1", account_id="acc1", strategy_id="sterling-kite", generation_id="g",
        signal_id="s", exchange="NFO", symbol=SYMBOL, side="BUY", quantity=quantity,
        price=100.0, payload=payload or {})


class _Lookup:
    def __init__(self, size=75, error=None):
        self.size, self.error = size, error

    async def instrument_lot_sizes(self, keys):
        if self.error:
            raise self.error
        return {k: self.size for k in keys} if self.size else {}


@pytest.mark.asyncio
async def test_the_broker_lot_size_wins_over_the_callers_payload():
    assert await gate._lot_size(_request(150, {"lot_size": 150}), [], _Lookup(75)) is None
    assert await gate._lot_size(_request(75, {"lot_size": 1}), [], _Lookup(75)) is None
    assert await gate._lot_size(_request(75, {"lot_size": 75}), [], _Lookup(75)) == 75
    assert await gate._lot_size(_request(150), [], _Lookup(75)) == 75


@pytest.mark.asyncio
async def test_a_broker_lookup_that_fails_or_knows_nothing_does_not_fall_back_to_the_payload():
    assert await gate._lot_size(
        _request(150, {"lot_size": 150}), [], _Lookup(error=RuntimeError("dump not loaded"))) is None
    assert await gate._lot_size(_request(150, {"lot_size": 150}), [], _Lookup(size=0)) is None


@pytest.mark.asyncio
async def test_the_payload_is_used_only_when_the_broker_offers_no_lookup():
    assert await gate._lot_size(_request(75, {"lot_size": 75}), [], object()) == 75
    assert await gate._lot_size(_request(75), [], object()) is None


@pytest.mark.asyncio
async def test_the_ledger_lot_size_must_agree_with_the_payload():
    held = [{"symbol": SYMBOL, "lot_size": 75}]
    assert await gate._lot_size(_request(75, {"lot_size": 75}), held, object()) == 75
    assert await gate._lot_size(_request(150, {"lot_size": 150}), held, object()) is None
    assert await gate._lot_size(_request(150), held, _Lookup(50)) is None


@pytest.mark.asyncio
async def test_two_real_lots_are_refused_under_a_one_lot_cap_even_with_a_matching_payload(
        tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_DB_PATH", str(tmp_path / "lot.db"))
    monkeypatch.setattr(db, "_available", True)
    db.init()
    monkeypatch.setenv("STERLING_LIVE_MINIMUM_MAX_LOTS", "1")

    verdict = await gate.evaluate_entry(
        request=_request(150, {"lot_size": 150}), order_value=15000.0,
        available_capital=500000.0, broker_client=_Lookup(75))

    assert verdict.allowed is False
    assert "MINIMUM_QUANTITY_UNKNOWN" in verdict.message()
