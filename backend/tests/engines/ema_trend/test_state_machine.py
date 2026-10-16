"""Campaign quantities follow fills, not quote predicates or acknowledgements."""
from decimal import Decimal

import pytest

from app.engines.ema_trend.config import PROFILES
from app.engines.ema_trend.contracts import Action, Campaign
from app.engines.ema_trend.state_machine import apply_fill, decide, finalize_entry, make_action, propose_stop
from .helpers import bar, campaign, config, daily_bar, feature, pivot


def opened(qty=100, **kwargs):
    c = campaign(qty, **kwargs)
    finalize_entry(c)
    return c


def reduce(c, component="WEAK_10", qty=25, index=1, price="108"):
    action = make_action(c, "REDUCE", "SELL", qty, index, daily_bar(index).available_ms,
                         Decimal(price), {component: qty})
    apply_fill(c, action, f"reduce-{component}-{index}", qty, price)
    return action


def decide_swing(c, f, previous=None, **kwargs):
    return decide(c, f, previous, PROFILES["swing"], config(), **kwargs)


@pytest.mark.parametrize("quantity,quarter", [(100, 25), (103, 25), (7, 1)])
def test_initial_anchors_use_actual_filled_quantity_and_original_stop(quantity, quarter):
    c = opened(quantity)
    assert (c.q0, c.quarter, c.p0, c.r_price) == (quantity, quarter, Decimal(100), Decimal(5))
    assert c.allowance10 == c.allowance21 == quarter
    assert c.state == "OPEN"


def test_terminal_tiny_partial_fill_requires_closure_not_rounded_quarters():
    c = opened(3)
    assert c.state == "CLOSING" and c.reason == "ENTRY_TOO_SMALL"
    action = decide_swing(c, feature(1))
    assert action.side == "SELL" and action.quantity == 3


def test_four_r_is_once_and_initial_anchors_survive_fill_stop_raise_and_restart():
    c = opened()
    action = decide_swing(c, feature(1, 120))
    assert action.components == {"TAKE_4R": 25}
    assert c.qty == 100  # submitting an intent cannot sell shares
    action.status = "ACKNOWLEDGED"
    assert c.qty == 100
    apply_fill(c, action, "four-r", 25, 120)
    c.stop = Decimal(99)
    c = Campaign.model_validate_json(c.model_dump_json())
    assert (c.q0, c.p0, c.initial_stop, c.r_price) == (100, Decimal(100), Decimal(95), Decimal(5))
    assert decide_swing(c, feature(2, 121), feature(1, 120)) is None
    assert c.qty == 75 and c.took_4r


def test_weakness_two_closes_once_equality_resets_and_no_unrestored_rearm():
    c = opened()
    assert decide_swing(c, feature(1, 109), feature(0, 111)) is None
    action = decide_swing(c, feature(2, 108), feature(1, 109))
    assert action.components == {"WEAK_10": 25}
    apply_fill(c, action, "weak10", 25, 108)
    assert decide_swing(c, feature(3, 107), feature(2, 108), allow_add=False) is None
    assert decide_swing(c, feature(4, 110), feature(3, 107), allow_add=False) is None
    assert c.below10 == 0
    assert decide_swing(c, feature(5, 109), feature(4, 110), allow_add=False) is None
    assert decide_swing(c, feature(6, 108), feature(5, 109), allow_add=False) is None
    assert c.qty == 75


def test_partial_multi_component_sells_allocate_only_filled_capacity_and_dedup():
    c = opened()
    assert decide_swing(c, feature(1, 100), feature(0, 111)) is None
    action = decide_swing(c, feature(2, 99), feature(1, 100))
    assert action.quantity == 50
    assert list(action.components) == ["WEAK_21", "WEAK_10"]
    apply_fill(c, action, "part1", 10, 99)
    assert (c.qty, c.allowance21, c.allowance10) == (90, 15, 25)
    assert sum(v.qty for v in c.vacancies) == 10
    before = c.model_dump_json()
    assert not apply_fill(c, action, "part1", 10, 99)
    assert c.model_dump_json() == before
    apply_fill(c, action, "part2", 20, 99)
    assert (c.qty, c.allowance21, c.allowance10) == (70, 0, 20)
    assert sum(v.qty for v in c.vacancies if v.component == "WEAK_21") == 25
    assert sum(v.qty for v in c.vacancies if v.component == "WEAK_10") == 5
    apply_fill(c, action, "part3", 20, 99)
    assert c.qty == 50 and c.pending is None
    assert c.allowance10 == c.allowance21 == 0


def test_separately_deserialized_partial_action_updates_durable_pending_watermark():
    c = opened()
    action = make_action(c, "REDUCE", "SELL", 50, 1, daily_bar(1).available_ms,
                         Decimal(100), {"WEAK_21": 25, "WEAK_10": 25})
    outbox_copy = Action.model_validate_json(action.model_dump_json())
    assert outbox_copy is not c.pending
    apply_fill(c, outbox_copy, "deserialized-part-one", 10, 100)
    assert c.pending.filled_quantity == 10 and c.pending.status == "PARTIAL"
    recovered = Campaign.model_validate_json(c.model_dump_json())
    apply_fill(recovered, recovered.pending, "deserialized-part-two", 20, 100)
    assert recovered.pending.filled_quantity == 30
    assert recovered.allowance21 == 0 and recovered.allowance10 == 20


def test_higher_low_stop_proposal_requires_ack_and_never_widens_or_crosses_price():
    c = opened()
    higher = pivot("LOW", 105, 3, 5)
    higher.previous_price = 100
    f = feature(5, 112, pivots=[higher])
    assert propose_stop(c, f) == Decimal("104.50")
    assert c.stop == Decimal(95), "A proposed stop has no acknowledged coverage yet"
    c.stop = Decimal("104.50")  # paper/broker adapter acknowledged replacement
    weaker = pivot("LOW", 104, 4, 6)
    weaker.previous_price = 103
    assert propose_stop(c, feature(6, 112, pivots=[weaker])) is None
    crossed = pivot("LOW", 115, 5, 7)
    crossed.previous_price = 105
    assert propose_stop(c, feature(7, 114, pivots=[crossed])) is None
    assert c.stop == Decimal("104.50")


def test_partial_add_rearms_only_actual_filled_shares():
    c = opened()
    reduce(c, index=1)
    reclaim = decide_swing(c, feature(2, 111, open_=110), feature(1, 108))
    assert reclaim.kind == "RECLAIM_10" and reclaim.quantity == 25
    apply_fill(c, reclaim, "add-only-five", 5, 111)
    assert c.qty == 80 and c.allowance10 == 5
    # The canonical adapter has confirmed the remaining buy cancellation.
    reclaim.status = "CANCELLED"
    c.pending = None
    c.state = "OPEN"
    assert decide_swing(c, feature(3, 109), feature(2, 111)) is None
    next_weak = decide_swing(c, feature(4, 108), feature(3, 109))
    assert next_weak.components == {"WEAK_10": 5}


def test_simultaneous_reclaims_add_one_quarter_per_bar_and_preserve_second_edge():
    c = opened()
    action = make_action(c, "REDUCE", "SELL", 50, 1, daily_bar(1).available_ms,
                         Decimal(100), {"WEAK_21": 25, "WEAK_10": 25})
    apply_fill(c, action, "both-weak", 50, 100)
    first = decide_swing(c, feature(2, 111, open_=110), feature(1, 100))
    assert first.kind == "RECLAIM_10" and first.quantity == 25
    apply_fill(c, first, "reclaim-ten", 25, 111)
    assert c.qty == 75
    assert decide_swing(c, feature(2, 111, open_=110), feature(1, 100)) is None
    second = decide_swing(c, feature(3, 112, open_=111), feature(2, 111))
    assert second.kind == "RECLAIM_21" and second.quantity == 25
    apply_fill(c, second, "reclaim-twentyone", 25, 112)
    assert c.qty == c.q0 and sum(v.qty for v in c.vacancies) == 0


def test_unused_captured_reclaim_expires_before_third_later_bar():
    c = opened()
    reduce(c, index=1)
    assert decide_swing(c, feature(2, 111, open_=110), feature(1, 108), allow_add=False) is None
    assert any(x.kind == "RECLAIM_10" for x in c.candidates)
    for index in (3, 4):
        assert decide_swing(c, feature(index, 111, open_=110), feature(index-1, 111), allow_add=False) is None
    assert decide_swing(c, feature(5, 111, open_=110), feature(4, 111)) is None
    assert not c.candidates


def test_unrelated_reduction_does_not_rearm_consumed_partial_reclaim_catalyst():
    c = opened()
    reduce(c, "WEAK_10", index=1)
    reclaim = decide_swing(c, feature(2, 111, open_=110), feature(1, 108))
    apply_fill(c, reclaim, "partial-reclaim-ten", 5, 111)
    reclaim.status = "CANCELLED"
    c.pending = None
    c.state = "OPEN"
    # A different weakness component sells later. That is not a new EMA10
    # reduction episode and cannot reset the already consumed EMA10 catalyst.
    reduce(c, "WEAK_21", index=3)
    action = decide_swing(c, feature(4, 112, open_=111), feature(3, 108))
    assert action is None or action.kind != "RECLAIM_10"


def test_higher_low_candidate_cannot_survive_a_bearish_crossover():
    c = opened()
    reduce(c, "TAKE_4R", index=1, price="120")
    c.bullish_epoch = 0
    c.pivot_low = pivot("LOW", 90, 0, 2)
    higher = pivot("LOW", 98, 3, 5)
    assert decide_swing(c, feature(5, 112, open_=111, pivots=[higher]),
                        feature(4, 111), allow_add=False) is None
    assert any(x.kind == "HIGHER_LOW" for x in c.candidates)
    # The completed bearish crossover invalidates the structural add setup.
    after_cross = feature(6, 111, open_=110, ema10=112, ema21=113)
    assert decide_swing(c, after_cross, feature(5, 112)) is None
    assert not any(x.kind == "HIGHER_LOW" for x in c.candidates)


def test_other_add_consumes_vacancy_before_stale_matching_reclaim():
    c = opened()
    reduce(c, index=1)
    action = make_action(c, "HIGHER_LOW", "BUY", 25, 2, daily_bar(2).available_ms, Decimal(111))
    apply_fill(c, action, "structural-refill", 25, 111)
    assert sum(v.qty for v in c.vacancies) == 0
    assert decide_swing(c, feature(3, 112, open_=111), feature(2, 108)) is None
    assert c.qty == c.q0


def test_no_averaging_down_admission_and_fixed_risk_capacity():
    c = opened()
    reduce(c, index=1)
    low_reclaim = feature(2, 99.5, open_=99, ema10=99, ema21=98)
    assert decide_swing(c, low_reclaim, feature(1, 98.5, ema10=99, ema21=98)) is None
    assert c.qty == 75
    limited = opened(budget="500")
    reduce(limited, index=1)
    action = decide_swing(limited, feature(2, 111, open_=110), feature(1, 108))
    # 75*5 existing risk + n*(111-95) <= 500; floor(125/16)=7.
    assert action.quantity == 7


def test_same_bar_delivery_and_restart_cannot_repeat_decision():
    c = opened()
    f = feature(1, 120)
    action = decide_swing(c, f)
    saved = Campaign.model_validate_json(c.model_dump_json())
    assert decide_swing(saved, f) is None
    assert saved.pending.id == action.id


def test_missing_daily_interval_breaks_two_close_count():
    c = opened()
    assert decide_swing(c, feature(1, 109), feature(0, 111)) is None
    # One entire expected exchange session is absent.
    assert decide_swing(c, feature(3, 108), feature(1, 109)) is None
    assert c.below10 == 1


def test_structural_lower_high_closes_all_inventory_including_restored_shares():
    c = opened(103)
    c.bearish_epoch = 2
    c.pivot_high = pivot("HIGH", 130, 0, 2)
    lower = pivot("HIGH", 125, 4, 6)
    f = feature(6, 115, ema10=110, ema21=111, pivots=[lower])
    action = decide_swing(c, f, feature(5, 115, ema10=110, ema21=111))
    assert action.kind == "STRUCTURAL_EXIT"
    assert action.quantity == 103


def test_stop_first_and_adverse_gap_never_wait_for_ema_confirmation():
    c = opened()
    f = feature(1, 123)
    f.bar = daily_bar(1, 123, open_=90, low=89, high=125)
    action = decide_swing(c, f)
    assert action.kind == "STOP" and action.quantity == 100
    assert action.limit == Decimal(90)


@pytest.mark.parametrize("mode", ["intraday", "scalping", "ultra_scalping"])
def test_session_cutoff_closes_fast_profiles_without_waiting_for_weakness(mode):
    c = opened()
    c.mode, c.product = mode, "MIS"
    f = feature(1, 111)
    # BASE starts at 10:00 IST. The completed 315th minute is 15:15.
    f.bar = bar(314, 111)
    action = decide(c, f, None, PROFILES[mode], config())
    assert action.kind == "SESSION_EXIT" and action.quantity == 100


@pytest.mark.parametrize("mode,hold", [("scalping", 20), ("ultra_scalping", 15)])
def test_fast_mode_holding_clock_is_initial_fill_clock_even_after_add(mode, hold):
    c = opened()
    c.mode, c.product = mode, "MIS"
    reduce(c, index=1)
    add = make_action(c, "RECLAIM_10", "BUY", 25, hold-1, bar(hold-1).available_ms,
                      Decimal(111))
    apply_fill(c, add, "late-add", 25, 111)
    f = feature(hold, 112)
    f.bar = bar(hold, 112)
    previous = feature(hold-1, 111)
    previous.bar = bar(hold-1, 111)
    action = decide(c, f, previous, PROFILES[mode], config())
    assert action.kind == "TIME_EXIT" and action.quantity == c.q0
    assert c.entry_index == 0


def test_stop_with_pending_unknown_buy_still_requests_confirmed_inventory_protection():
    c = opened()
    reduce(c, index=1)
    pending = make_action(c, "RECLAIM_10", "BUY", 25, 2, daily_bar(2).available_ms, Decimal(111))
    pending.status = "UNKNOWN"
    f = feature(3, 94)
    action = decide_swing(c, f, feature(2, 111))
    assert c.state == "CLOSING"
    assert action is not None, "Known held exposure cannot wait for uncertain buy cancellation"
    assert action.side == "SELL" and action.quantity == 75


def test_late_buy_after_emergency_close_remains_closing_and_is_closed_again():
    c = opened()
    reduce(c, index=1)
    buy = make_action(c, "RECLAIM_10", "BUY", 25, 2, daily_bar(2).available_ms, Decimal(111))
    buy.status = "UNKNOWN"
    close = decide_swing(c, feature(3, 94), feature(2, 111))
    apply_fill(c, close, "emergency-close", 75, 94)
    assert c.qty == 0 and c.state == "CLOSING"
    assert c.pending is not None and c.pending.id == buy.id
    apply_fill(c, buy, "late-buy", 25, 111)
    assert c.qty == 25 and c.state == "CLOSING"
    next_close = decide_swing(c, feature(4, 112), feature(3, 94))
    assert next_close.side == "SELL" and next_close.quantity == 25
    apply_fill(c, next_close, "late-inventory-close", 25, 112)
    assert c.qty == 0 and c.state == "CLOSED"


def test_initial_partial_stop_and_late_entry_preserve_original_entry_anchors():
    c = campaign()
    c.lots.clear()
    entry = make_action(c, "ENTRY", "BUY", 100, 0, daily_bar(0).available_ms, Decimal(103))
    apply_fill(c, entry, "entry-first-20", 20, 100)
    stop = decide_swing(c, feature(1, 94))
    apply_fill(c, stop, "stop-first-20", 20, 94)
    assert c.state == "CLOSING"
    apply_fill(c, entry, "entry-late-80", 80, 102)
    finalize_entry(c)
    assert c.q0 == 100 and c.qty == 80
    assert c.p0 == Decimal("101.6") and c.r_price == Decimal("6.6")
    assert c.state == "CLOSING"


def test_fifo_realized_fees_and_remaining_lot_basis_conserve_cash():
    c = campaign()
    c.lots.clear()
    entry = make_action(c, "ENTRY", "BUY", 20, 0, daily_bar(0).available_ms, Decimal(110))
    apply_fill(c, entry, "entry-cheap", 10, 100, fee=10)
    apply_fill(c, entry, "entry-dear", 10, 110, fee=20)
    finalize_entry(c)
    sale = make_action(c, "REDUCE", "SELL", 15, 1, daily_bar(1).available_ms,
                       Decimal(120), {"WEAK_21": 5, "WEAK_10": 5, "TAKE_4R": 5})
    apply_fill(c, sale, "fifo-sale", 15, 120, fee=15)
    assert c.realized_net == Decimal(215)
    assert c.total_fees == Decimal(45)
    assert c.qty == 5 and c.lots[0].price == Decimal(110)
    remaining_basis = sum(l.qty * (l.price + l.fee_per_share) for l in c.lots)
    assert remaining_basis == Decimal(560)
    assert Decimal(1785) - (Decimal(2130) - remaining_basis) == c.realized_net


@pytest.mark.parametrize("case", ["other_campaign", "oversell", "overfill"])
def test_invalid_fill_facts_are_rejected_without_changing_inventory(case):
    c = opened()
    action = make_action(c, "REDUCE", "SELL", 25, 1, daily_bar(1).available_ms, Decimal(108))
    if case == "other_campaign":
        action.campaign_id = "unowned"
        qty = 1
    elif case == "oversell":
        action.quantity = 101
        qty = 101
    else:
        qty = 26
    before = c.qty
    with pytest.raises(ValueError):
        apply_fill(c, action, "invalid", qty, 108)
    assert c.qty == before and "invalid" not in c.seen_fill_ids
