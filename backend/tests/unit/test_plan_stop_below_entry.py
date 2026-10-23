"""A bought option's plan is protectable only with a stop below its entry (live 22 Oct 2026:
HDFCBANK 680 PE showed entry 2.10 with stop 2.18, which sells the moment it fills)."""
from app.services.kite_engine.protection import LegPlan


def plan(entry, stop):
    return LegPlan(symbol="NIFTY26OCT24000CE", exchange="NFO", token=1, lot_size=75,
                   entry_premium=entry, stop_premium=stop, target_premium=0.0, strike=24000.0,
                   expiry="2026-10-27", underlying="NIFTY", direction="long", signal_direction="long",
                   source="spot", entry_spot=24000.0, entry_delta=0.5)


def test_stop_below_entry_is_protectable():
    assert plan(8.55, 3.10).protectable


def test_stop_at_or_above_entry_is_not():
    assert not plan(2.10, 2.18).protectable
    assert not plan(5.00, 5.00).protectable


def test_missing_stop_is_not():
    assert not plan(9.00, 0).protectable
