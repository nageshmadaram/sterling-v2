"""Option-only mark-to-market computations; no Kite, future hedge, or fill claims."""
import math
import random

import pytest

from app.engines.snapback.equity_mark import option_leg_mark_equity as mark


def test_true_open_loss_includes_exit_costs():
    assert mark(
        100000., entry_price=120, observed_mark_price=119, quantity=50,
        estimated_roundtrip_cost_points=1, fixed_roundtrip_cost_inr=40,
    ) == 99860


def test_adverse_intrabar_low_exceeds_realized_stop_loss():
    observed_low_equity = mark(
        100000, entry_price=119, observed_mark_price=100, quantity=50,
        estimated_roundtrip_cost_points=1, fixed_roundtrip_cost_inr=40)
    estimated_stop_close_equity = mark(
        100000, entry_price=119, observed_mark_price=115, quantity=50,
        estimated_roundtrip_cost_points=1, fixed_roundtrip_cost_inr=40)
    assert observed_low_equity < estimated_stop_close_equity
    assert 100000 - observed_low_equity == 1040
    assert 100000 - estimated_stop_close_equity == 290


def test_mark_profit_when_price_recovers_after_costs():
    assert mark(
        100000, entry_price=100, observed_mark_price=110, quantity=50,
        estimated_roundtrip_cost_points=1, fixed_roundtrip_cost_inr=40,
    ) == 100410


@pytest.mark.parametrize("kwargs", [
    dict(quantity=0), dict(quantity=-1), dict(quantity=True),
    dict(quantity=50.5), dict(entry_price=0), dict(entry_price=-1),
    dict(observed_mark_price=0), dict(observed_mark_price=math.nan),
    dict(estimated_roundtrip_cost_points=-1),
    dict(fixed_roundtrip_cost_inr=-1),
    dict(realized_equity_inr=math.inf),
])
def test_invalid_inputs_are_never_used_as_profit_or_risk(kwargs):
    values = dict(
        realized_equity_inr=100000, entry_price=100,
        observed_mark_price=101, quantity=50,
        estimated_roundtrip_cost_points=1, fixed_roundtrip_cost_inr=40)
    values.update(kwargs)
    with pytest.raises(ValueError, match="invalid option mark"):
        mark(**values)


def test_500_seeded_marks_match_hand_economics_and_cost_monotonicity():
    rng = random.Random(27102026)
    for _ in range(500):
        capital = rng.uniform(10000, 500000)
        entry = rng.uniform(.05, 1000)
        price = rng.uniform(.05, 1000)
        quantity = rng.randrange(1, 1000)
        cost = rng.uniform(0, 10)
        fixed = rng.uniform(0, 80)
        a = mark(
            capital, entry_price=entry, observed_mark_price=price,
            quantity=quantity, estimated_roundtrip_cost_points=cost,
            fixed_roundtrip_cost_inr=fixed)
        b = mark(
            capital, entry_price=entry, observed_mark_price=price,
            quantity=quantity, estimated_roundtrip_cost_points=cost + .5,
            fixed_roundtrip_cost_inr=fixed)
        assert a == pytest.approx(capital + (price-entry-cost)*quantity-fixed)
        assert a-b == pytest.approx(.5*quantity)
