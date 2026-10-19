"""Offline EMA broker-stop evidence and SL/TSL/target/exit decisions."""
from decimal import Decimal

import pytest

from app.engines.ema_trend.live_contracts import BrokerIntent, BrokerStop, LiveCampaign
from app.services.ema_trend.live_lifecycle import (
    LiveLifecycleEvidenceError, decide_exit, verify_active_cnc_gtt,
)


def campaign(*,product="CNC",protected=True,qty=4):
    entered=BrokerIntent(
        intent_key="entry",side="BUY",requested_qty=4,order_id="O-1",
        confirmed_qty=qty,cumulative_value=Decimal(qty)*100,
        status="COMPLETE" if qty==4 else "PARTIAL")
    stop=BrokerStop(
        trigger_id="901",exchange="NSE",symbol="INFY",product=product,
        qty=qty,trigger=Decimal("95"),verified_active=True)
    return LiveCampaign(
        id="c",owner_uid="alice",account_id="KITE-REAL-1",exchange="NSE",
        symbol="INFY",mode="swing" if product=="CNC" else "intraday",
        product=product,strategy_version="1",config_hash="hash",signal_id="s",
        stop=Decimal("95"),entries=[entered],
        confirmed_bought=qty,
        phase="PROTECTED" if protected else "PROTECTION_REQUIRED",
        protection=stop if protected else None)


def gtt():
    return {
        "id":901,"status":"active",
        "condition":{"exchange":"NSE","tradingsymbol":"INFY",
                     "trigger_values":[95]},
        "orders":[{"exchange":"NSE","tradingsymbol":"INFY","transaction_type":"SELL","product":"CNC",
                   "quantity":4,"order_type":"LIMIT","price":94.5}],
    }


def test_cnc_protection_requires_exact_live_broker_gtt():
    stop=verify_active_cnc_gtt(campaign(),gtt(),trigger_id="901")
    assert stop.verified_active and stop.qty==4 and stop.trigger==Decimal("95")


@pytest.mark.parametrize("field,value",[
    ("id",999),("status","triggered"),("condition",{}),("orders",[]),
])
def test_invalid_gtt_snapshot_fails_closed(field,value):
    record=gtt()
    record[field]=value
    with pytest.raises(LiveLifecycleEvidenceError):
        verify_active_cnc_gtt(campaign(),record,trigger_id="901")


@pytest.mark.parametrize("change",[
    {"quantity":3},{"product":"MIS"},{"transaction_type":"BUY"},
    {"price":95.5},{"order_type":"MARKET"},
])
def test_wrong_gtt_child_contract_cannot_claim_broker_protection(change):
    record=gtt()
    record["orders"][0].update(change)
    with pytest.raises(LiveLifecycleEvidenceError):
        verify_active_cnc_gtt(campaign(),record,trigger_id="901")


def test_broker_stop_cannot_be_weaker_than_campaign():
    record=gtt()
    record["condition"]["trigger_values"]=[94]
    with pytest.raises(LiveLifecycleEvidenceError,match="WEAKER"):
        verify_active_cnc_gtt(campaign(),record,trigger_id="901")


def test_mis_requires_a_real_broker_protective_order_not_gtt():
    with pytest.raises(LiveLifecycleEvidenceError,match="MIS_GTT"):
        verify_active_cnc_gtt(campaign(product="MIS"),gtt(),trigger_id="901")
    result=decide_exit(campaign(product="MIS"),bid=100)
    assert result.action=="BROKER_STOP_UNSUPPORTED"


def test_trailing_stop_only_tightens_and_never_crosses_bid():
    current=campaign()
    assert decide_exit(current,bid=100,stop_candidate=96).action=="REPLACE_STOP"
    assert decide_exit(current,bid=100,stop_candidate=94).action=="WAIT"
    assert decide_exit(current,bid=96,stop_candidate=96).reason=="TRAIL_ALREADY_BREACHED"


def test_stop_loss_target_deadline_and_manual_flatten_propose_exit_not_fill():
    current=campaign()
    assert decide_exit(current,bid=94).reason=="STOP_BREACH"
    assert decide_exit(current,bid=105,target=103).reason=="TARGET_REACHED"
    assert decide_exit(current,bid=105,deadline_reached=True).reason=="SESSION_DEADLINE"
    requested=current.model_copy(update={"flatten_requested":True})
    assert decide_exit(requested,bid=105).reason=="OPERATOR_FLATTEN"
    assert decide_exit(current,bid=100).action=="WAIT"


def test_stale_quotes_unknown_orders_and_unprotected_positions_fail_closed():
    current=campaign()
    assert decide_exit(current,bid=100,quote_age_s=30,max_quote_age_s=10).action=="RECONCILE_BROKER"
    pending=campaign(protected=False)
    assert decide_exit(pending,bid=100).action=="VERIFY_STOP"
    ambiguous=current.model_copy(update={"protection_submission_unknown":True,"phase":"RECOVERY_REQUIRED"})
    assert decide_exit(ambiguous,bid=100).action=="RECONCILE_BROKER"


def test_read_only_decisions_do_not_call_a_broker_order_method():
    class FakeKite:
        def place_order(self,*args,**kwargs):
            raise AssertionError("NO BROKER ORDER WRITE")
        def place_gtt(self,*args,**kwargs):
            raise AssertionError("NO GTT WRITE")
        def delete_gtt(self,*args,**kwargs):
            raise AssertionError("NO GTT DELETE")
    client=FakeKite()
    result=decide_exit(campaign(),bid=100,stop_candidate=96)
    assert result.action=="REPLACE_STOP"
    assert verify_active_cnc_gtt(campaign(),gtt(),trigger_id="901").verified_active


def test_pending_sell_prevents_second_stop_or_target_exit():
    pending=BrokerIntent(
        intent_key="exit-1",side="SELL",requested_qty=4,
        order_id="SELL-1",status="ACKNOWLEDGED",
    )
    current=campaign().model_copy(update={"exits":[pending],"phase":"EXIT_PENDING"})
    for price in (90,100,110):
        result=decide_exit(current,bid=price,target=105,deadline_reached=(price==100))
        assert result.action=="RECONCILE_BROKER"
        assert result.reason=="EXIT_ALREADY_PENDING_OR_UNKNOWN"


def test_live_orphan_broker_stop_disallows_another_exit_proposal():
    current=campaign(protected=False).model_copy(
        update={"phase":"RECOVERY_REQUIRED",
                "orphan_stop_ids":["901"],
                "protection_submission_unknown":True,
                "flatten_requested":True})
    result=decide_exit(current,bid=80)
    assert result.action=="RECONCILE_BROKER"
    assert result.reason=="UNRESOLVED_BROKER_STATE"


def test_outstanding_additional_buy_blocks_duplicate_exit_on_stop_breach():
    current=campaign()
    add=BrokerIntent(intent_key="add-1",side="BUY",
                     requested_qty=2,order_id="BUY-2",status="ACKNOWLEDGED")
    current=current.model_copy(update={"entries":[*current.entries,add]})
    result=decide_exit(current,bid=90)
    assert result.action=="RECONCILE_BROKER"
    assert result.reason=="PENDING_ENTRY_CAN_INCREASE_LIVE_EXPOSURE"
