"""No-network fake Kite tests for EMA live exit admission (never sends orders)."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.engines.ema_trend.live_contracts import BrokerIntent, LiveCampaign
from app.services.ema_trend.live_exit_preflight import inspect_exit
from app.services.exchanges.kite.client import KiteClient


def campaign(**changes):
    data=dict(
        id="ema-fake-live-1",owner_uid="alice",account_id="KITE-FAKE-1",
        exchange="NSE",symbol="INFY",mode="swing",product="CNC",
        strategy_version="1",config_hash="frozen",signal_id="signal",
        phase="PROTECTION_REQUIRED",stop=Decimal("95"),
        entries=[BrokerIntent(
            intent_key="entry",side="BUY",requested_qty=4,order_id="OID1",
            confirmed_qty=4,cumulative_value=Decimal("400"),status="COMPLETE")],
        confirmed_bought=4,
    )
    data.update(changes)
    return LiveCampaign(**data)


def client(*, qty=4, orders=None, gtts=None, holdings=None):
    kite=KiteClient(api_key="fake",api_secret="fake",access_token="fake",is_paper=False)
    kite._account_id="KITE-FAKE-1"
    kite.get_positions_raw=AsyncMock(return_value={
        "net":[{"exchange":"NSE","tradingsymbol":"INFY","product":"CNC","quantity":qty}]
        if qty is not None else [],
    })
    kite.get_holdings=AsyncMock(return_value=holdings if holdings is not None else [])
    kite.get_orders=AsyncMock(return_value=orders if orders is not None else [])
    kite.get_gtts=AsyncMock(return_value=gtts if gtts is not None else [])
    kite.place_order=AsyncMock(side_effect=AssertionError("NO ORDER WRITE"))
    kite.cancel_order=AsyncMock(side_effect=AssertionError("NO CANCEL WRITE"))
    kite.modify_order=AsyncMock(side_effect=AssertionError("NO MODIFY WRITE"))
    kite.place_gtt=AsyncMock(side_effect=AssertionError("NO GTT WRITE"))
    kite.modify_gtt=AsyncMock(side_effect=AssertionError("NO GTT MODIFY"))
    kite.delete_gtt=AsyncMock(side_effect=AssertionError("NO GTT DELETE"))
    return kite


def no_mutations(kite):
    for name in ("place_order","cancel_order","modify_order",
                 "place_gtt","modify_gtt","delete_gtt"):
        getattr(kite,name).assert_not_called()


@pytest.mark.asyncio
async def test_fake_kite_matching_inventory_still_never_authorizes_a_live_order():
    kite=client()
    result=await inspect_exit(campaign(),kite)
    assert result.status=="READ_ONLY_VERIFIED"
    assert result.broker_quantity==4
    assert result.read_only and not result.live_order_authorized
    no_mutations(kite)


@pytest.mark.asyncio
@pytest.mark.parametrize("orders",[
    [{"exchange":"NSE","tradingsymbol":"INFY","status":"OPEN",
      "transaction_type":"SELL"}],
    [{"exchange":"NSE","tradingsymbol":"INFY","status":"OPEN",
      "transaction_type":"BUY"}],
    [{"exchange":"NSE","tradingsymbol":"INFY","status":"TRIGGER PENDING",
      "transaction_type":"SELL"}],
])
async def test_fake_kite_rejects_resting_child_exit_and_new_buy(orders):
    kite=client(orders=orders)
    result=await inspect_exit(campaign(),kite)
    assert result.status=="BLOCKED"
    assert "UNRESOLVED_BROKER_ORDER_FOR_SYMBOL" in result.blockers
    no_mutations(kite)


@pytest.mark.asyncio
async def test_fake_kite_active_or_triggered_gtt_blocks_competing_sell():
    for status in ("active","triggered"):
        kite=client(gtts=[{"id":901,"status":status,"condition":{
            "exchange":"NSE","tradingsymbol":"INFY"}}])
        result=await inspect_exit(campaign(),kite)
        assert "ACTIVE_OR_FIRED_GTT_REQUIRES_CHILD_RECONCILIATION" in result.blockers
        no_mutations(kite)


@pytest.mark.asyncio
async def test_broker_book_disagreement_never_fabricates_held_shares():
    kite=client(qty=3)
    result=await inspect_exit(campaign(),kite)
    assert result.status=="BLOCKED"
    assert "BROKER_OWNERSHIP_QUANTITY_DISAGREES_WITH_CANONICAL_LEDGER" in result.blockers
    no_mutations(kite)


@pytest.mark.asyncio
async def test_broker_disconnect_or_invalid_inventory_fails_closed():
    kite=client()
    kite.get_positions_raw=AsyncMock(side_effect=ConnectionError("offline"))
    result=await inspect_exit(campaign(),kite)
    assert result.blockers==("BROKER_EXIT_FACTS_UNAVAILABLE",)
    no_mutations(kite)
    kite=client(qty=True)
    result=await inspect_exit(campaign(),kite)
    assert result.blockers==("INVALID_BROKER_QUANTITY",)
    no_mutations(kite)


@pytest.mark.asyncio
async def test_missing_or_wrong_client_account_rejected_without_broker_reads():
    kite=client()
    kite._account_id="OTHER"
    result=await inspect_exit(campaign(),kite)
    assert result.blockers==("LIVE_CLIENT_ACCOUNT_BINDING_NOT_PROVEN",)
    kite.get_positions_raw.assert_not_called()
    no_mutations(kite)
    unknown=SimpleNamespace(_is_paper=False,_account_id="KITE-FAKE-1")
    result=await inspect_exit(campaign(),unknown)
    assert result.status=="BLOCKED"


@pytest.mark.asyncio
async def test_orphan_gtt_survives_replay_and_vetoes_another_sell():
    kite=client()
    record=campaign(phase="RECOVERY_REQUIRED",
                    protection_submission_unknown=True,
                    orphan_stop_ids=["901"])
    result=await inspect_exit(record,kite)
    assert result.status=="BLOCKED"
    assert "ORPHAN_OR_UNKNOWN_BROKER_STOP" in result.blockers
    kite.get_positions_raw.assert_not_called()
    no_mutations(kite)


@pytest.mark.asyncio
async def test_live_pending_exit_or_partially_filled_buy_prevents_oversell():
    exit_order=BrokerIntent(intent_key="exit",side="SELL",
                            requested_qty=4,order_id="EXIT1",status="ACKNOWLEDGED")
    kite=client()
    result=await inspect_exit(campaign(exits=[exit_order],phase="EXIT_PENDING"),kite)
    assert "EXIT_ALREADY_PENDING_OR_UNKNOWN" in result.blockers
    partial_buy=BrokerIntent(intent_key="entry",side="BUY",
                             requested_qty=5,order_id="OID1",
                             confirmed_qty=4,cumulative_value=Decimal("400"),
                             status="PARTIAL")
    result=await inspect_exit(campaign(entries=[partial_buy]),kite)
    assert "ENTRY_MAY_FILL_AFTER_EXIT_DECISION" in result.blockers
    no_mutations(kite)


@pytest.mark.asyncio
async def test_holdings_overlap_or_unsettled_quantity_is_not_safely_reducible():
    row={"exchange":"NSE","tradingsymbol":"INFY","quantity":4,
         "t1_quantity":0,"collateral_quantity":0}
    kite=client(qty=4,holdings=[row])
    result=await inspect_exit(campaign(),kite)
    assert result.blockers==("BROKER_POSITION_HOLDING_OVERLAP_UNRESOLVED",)
    kite=client(qty=None,holdings=[{**row,"t1_quantity":1}])
    result=await inspect_exit(campaign(),kite)
    assert result.blockers==("UNSETTLED_OR_PLEDGED_HOLDINGS_NOT_AUTHORIZED",)
    no_mutations(kite)
