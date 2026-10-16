from decimal import Decimal

import pytest

from app.engines.ema_trend.sizing import size_plan, used_campaign_risk
from app.engines.ema_trend.state_machine import finalize_entry
from .helpers import campaign, config


def test_risk_size_is_floor_of_stop_distance_and_never_rounds_up():
    cfg = config(allocation_inr=Decimal(20_000))
    result = size_plan(cfg, 100, 95, 5, 20_000)
    assert result["quantity"] == 10  # budget 50 / five rupees risk
    assert result["budget"] == Decimal(50)
    assert result["capital_required"] == Decimal(1000)


def test_cash_and_remaining_parent_risk_can_each_deny_minimum_quarters():
    cfg = config(allocation_inr=Decimal(20_000))
    assert size_plan(cfg, 100, 95, 5, 350)["quantity"] == 0
    # Strategy allowance is 150; 145 already reserved leaves five rupees.
    assert size_plan(cfg, 100, 95, 5, 20_000, used_risk=145)["quantity"] == 0
    assert size_plan(config(allocation_inr=0), 100, 95, 5, 20_000)["quantity"] == 0


def test_transaction_costs_can_make_a_geometrically_valid_trade_unaffordable():
    cfg = config(allocation_inr=Decimal(20_000), brokerage_per_order=10)
    assert size_plan(cfg, 100, 95, 5, 20_000)["quantity"] == 0


@pytest.mark.parametrize("entry,stop,atr,cash", [(95, 95, 5, 1000), (94, 95, 5, 1000),
                                                (100, -1, 5, 1000), (100, 95, 0, 1000),
                                                (100, 95, 5, -1)])
def test_invalid_economic_input_never_authorizes_size(entry, stop, atr, cash):
    assert size_plan(config(), entry, stop, atr, cash)["quantity"] == 0


def test_nonfinite_sizing_input_raises_instead_of_leaking_through_minimums():
    with pytest.raises(ValueError):
        size_plan(config(), float("nan"), 95, 5, 1000)


def test_realized_profits_never_credit_prior_realized_loss_debit():
    c = campaign()
    finalize_entry(c)
    c.realized_net = Decimal(1000)
    c.realized_loss_debit = Decimal(20)
    assert used_campaign_risk(c, config()) == Decimal(520)
    c.stop = Decimal(105)
    assert used_campaign_risk(c, config()) == Decimal(20)


def test_open_entry_fees_are_stop_risk_until_allocated_to_realized_lots():
    c = campaign()
    c.lots[0].fee_per_share = Decimal(1)
    finalize_entry(c)
    # Losing five rupees in price and one in paid entry costs is six rupees
    # per remaining share. The zero future fee config isolates that cost.
    assert used_campaign_risk(c, config()) == Decimal(600)
