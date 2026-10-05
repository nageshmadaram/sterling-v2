"""Kite GTT accepts LIMIT orders only: every protective leg must carry a price."""
import pytest

from app.services.kite_engine import protective_stop as ps


def _req(**kw):
    base = dict(tradingsymbol="NIFTY25OCT24500CE", exchange="NFO", qty=75,
                trigger_premium=100.0, last_price=120.0, direction="long", product="MIS")
    base.update(kw)
    return ps.build_gtt_request(**base)


def test_single_stop_leg_is_a_marketable_limit_below_the_trigger(monkeypatch):
    monkeypatch.delenv(ps.GTT_LIMIT_BUFFER_ENV, raising=False)
    req = _req()
    (leg,) = req["orders"]
    assert leg["order_type"] == "LIMIT"
    assert leg["transaction_type"] == "SELL"
    assert leg["price"] == 95.0
    assert req["trigger_values"] == [100.0]


def test_oco_legs_each_price_off_their_own_trigger(monkeypatch):
    monkeypatch.delenv(ps.GTT_LIMIT_BUFFER_ENV, raising=False)
    req = _req(target_premium=150.0)
    assert req["trigger_type"] == "two-leg"
    assert req["trigger_values"] == [100.0, 150.0]
    assert [o["price"] for o in req["orders"]] == [95.0, 142.5]
    assert all(o["order_type"] == "LIMIT" for o in req["orders"])


def test_short_close_leg_buys_through_the_trigger(monkeypatch):
    monkeypatch.delenv(ps.GTT_LIMIT_BUFFER_ENV, raising=False)
    req = _req(direction="short", trigger_premium=100.0, last_price=80.0)
    (leg,) = req["orders"]
    assert leg["transaction_type"] == "BUY" and leg["price"] == 105.0


def test_prices_sit_on_the_tick_and_buffer_is_bounded(monkeypatch):
    monkeypatch.setenv(ps.GTT_LIMIT_BUFFER_ENV, "400")      # clamped to 25%
    (leg,) = _req(trigger_premium=33.35)["orders"]
    assert leg["price"] == pytest.approx(round(round(33.35 * 0.75 / 0.05) * 0.05, 2))
    monkeypatch.setenv(ps.GTT_LIMIT_BUFFER_ENV, "garbage")  # falls back to default
    (leg,) = _req(trigger_premium=100.0)["orders"]
    assert leg["price"] == 95.0


def test_a_cheap_premium_never_prices_below_one_tick(monkeypatch):
    monkeypatch.delenv(ps.GTT_LIMIT_BUFFER_ENV, raising=False)
    (leg,) = _req(trigger_premium=0.05, last_price=0.10)["orders"]
    assert leg["price"] >= 0.05


def test_a_leg_without_a_trigger_is_refused_not_sent_as_market():
    with pytest.raises(ValueError):
        ps._exit_order("X", "NFO", 1, "long", "MIS", 0.0)
