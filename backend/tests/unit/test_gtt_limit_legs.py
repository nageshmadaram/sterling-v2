"""Kite GTT accepts priced LIMIT orders only: every protective leg carries a price."""
import pytest

from app.services.kite_engine import protective_stop as ps


def _req(**kw):
    base = dict(tradingsymbol="NIFTY25OCT24500CE", exchange="NFO", qty=75,
                trigger_premium=100.0, last_price=120.0, direction="long", product="NRML")
    base.update(kw)
    return ps.build_gtt_request(**base)


def test_single_stop_leg_is_a_marketable_limit_below_the_trigger(monkeypatch):
    monkeypatch.delenv(ps.GTT_LIMIT_BUFFER_ENV, raising=False)
    req = _req()
    (leg,) = req["orders"]
    assert leg["order_type"] == "LIMIT"
    assert leg["transaction_type"] == "SELL"
    assert leg["price"] == pytest.approx(98.0)
    assert req["trigger_values"] == [100.0]


def test_oco_legs_each_price_off_their_own_trigger(monkeypatch):
    monkeypatch.delenv(ps.GTT_LIMIT_BUFFER_ENV, raising=False)
    req = _req(target_premium=150.0)
    assert req["trigger_type"] == "two-leg"
    assert req["trigger_values"] == [100.0, 150.0]
    assert [o["price"] for o in req["orders"]] == pytest.approx([98.0, 147.0])
    assert all(o["order_type"] == "LIMIT" for o in req["orders"])


def test_short_close_leg_buys_through_the_trigger(monkeypatch):
    monkeypatch.delenv(ps.GTT_LIMIT_BUFFER_ENV, raising=False)
    req = _req(direction="short", trigger_premium=100.0, last_price=80.0)
    (leg,) = req["orders"]
    assert leg["transaction_type"] == "BUY" and leg["price"] == pytest.approx(102.0)


def test_buffer_is_configurable_and_bounded(monkeypatch):
    monkeypatch.setenv(ps.GTT_LIMIT_BUFFER_ENV, "400")      # clamped to 25%
    (leg,) = _req(trigger_premium=100.0)["orders"]
    assert leg["price"] == pytest.approx(75.0)
    monkeypatch.setenv(ps.GTT_LIMIT_BUFFER_ENV, "garbage")  # falls back to the default
    (leg,) = _req(trigger_premium=100.0)["orders"]
    assert leg["price"] == pytest.approx(98.0)


def test_a_cheap_premium_never_prices_below_one_tick(monkeypatch):
    monkeypatch.delenv(ps.GTT_LIMIT_BUFFER_ENV, raising=False)
    (leg,) = _req(trigger_premium=0.05, last_price=0.10)["orders"]
    assert leg["price"] >= 0.05
