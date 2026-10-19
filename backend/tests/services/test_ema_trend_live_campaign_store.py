"""Hermetic durable EMA live ledger/journal integration; NO broker network writes."""
from dataclasses import replace
from decimal import Decimal

import pytest

from app.engines.ema_trend.live_contracts import LiveCampaign, BrokerIntent
from app.services import db
from app.services.ema_trend import live_campaign_store as store
from app.services.ema_trend.live_order_facts import (
    LiveOrderIdentity, broker_order_fact,
)
from app.services.kite_engine import fill_ledger, order_journal


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db,"_DB_PATH",str(tmp_path/"ema-live-store.db"))
    monkeypatch.setattr(db,"_available",False)
    assert db.init()


def prepare():
    model = LiveCampaign(
        id="ema-1", owner_uid="alice", account_id="KITE-LIVE-TEST",
        exchange="NSE",symbol="INFY",mode="swing",product="CNC",
        strategy_version="1",config_hash="frozen",signal_id="signal",
        stop=Decimal("95"), entries=[BrokerIntent(
            intent_key=order_journal.make_key(
                uid="alice",account_id="KITE-LIVE-TEST",
                strategy_id="ema_trend",generation_id="frozen",
                signal_id="signal",exchange="NSE",symbol="INFY",side="BUY"),
            side="BUY",requested_qty=4)],
    )
    store.create_prepared(model)
    journal = order_journal.reserve(
        uid=model.owner_uid,account_id=model.account_id,strategy_id="ema_trend",
        generation_id=model.config_hash,signal_id=model.signal_id,
        exchange=model.exchange,symbol=model.symbol,side="BUY",quantity=4,
        payload={"product":"CNC"},
    )
    assert journal.intent_key == model.entries[0].intent_key
    assert order_journal.claim_submission(journal.intent_key)
    order_journal.acknowledge(journal.intent_key,"ORDER-123")
    bound = store.bind_acknowledged_order(
        model.id,journal.intent_key,expected_revision=0)
    assert bound.revision == 1 and bound.phase == "ENTRY_PENDING"
    return bound, journal.intent_key


def broker_fact(status="OPEN",filled=0, *,order_id="ORDER-123",
                account_id="KITE-LIVE-TEST"):
    identity=LiveOrderIdentity(
        uid="alice",account_id=account_id,order_id=order_id,
        exchange="NSE",symbol="INFY",product="CNC",side="BUY",quantity=4)
    row={
        "order_id":order_id,"exchange":"NSE","tradingsymbol":"INFY",
        "product":"CNC","transaction_type":"BUY","quantity":4,
        "status":status,"filled_quantity":filled,
        "average_price":"100" if filled else "0",
    }
    return broker_order_fact(identity,[row],
        broker_account_id=account_id,broker_is_paper=False)


def observe(journal_key,status,filled):
    return order_journal.observe_order(journal_key,
        status=status,order_id="ORDER-123",
        filled_quantity=filled,average_price=100 if filled else 0)


def sign_fill(filled):
    return fill_ledger.record(
        account_id="KITE-LIVE-TEST",uid="alice",symbol="INFY",exchange="NSE",
        side="BUY",order_id="ORDER-123",
        cumulative_quantity=filled,cumulative_value=100*filled,
        source="entry",lot_size=1)


def test_prepared_is_durable_and_uniquely_owned():
    prepare()
    saved=store.get("ema-1")
    assert saved.account_id=="KITE-LIVE-TEST" and saved.revision==1
    other=LiveCampaign(
        id="ema-other",owner_uid="alice",account_id="KITE-LIVE-TEST",
        exchange="NSE",symbol="INFY",mode="swing",product="CNC",
        strategy_version="1",config_hash="frozen",signal_id="other",
        stop=Decimal("95"))
    with pytest.raises(store.LiveStoreConflict,match="DUPLICATED"):
        store.create_prepared(other)


def test_order_ack_never_claims_a_confirmed_fill():
    campaign,key=prepare()
    observe(key,"OPEN",0)
    result=store.project_verified_order(
        campaign.id,key,broker_fact("OPEN",0),expected_revision=campaign.revision)
    assert result.campaign.confirmed_bought==0
    assert result.campaign.phase=="RECOVERY_REQUIRED"
    assert result.next_proof=="BROKER_INVENTORY_RECONCILIATION"
    assert not order_journal.get(key).projection_pending


def test_partial_fill_requires_signed_ledger_before_campaign_commit():
    campaign,key=prepare()
    observe(key,"OPEN",2)
    with pytest.raises(store.LiveStoreConflict,match="SIGNED_ORDER_FILL_LEDGER"):
        store.project_verified_order(
            campaign.id,key,broker_fact("OPEN",2),expected_revision=1)
    assert store.get("ema-1").revision==1
    assert order_journal.get(key).projection_pending
    sign_fill(2)
    result=store.project_verified_order(
        campaign.id,key,broker_fact("OPEN",2),expected_revision=1)
    assert result.campaign.confirmed_bought==2
    assert result.campaign.held_qty==2
    assert result.uncovered_qty==2
    assert result.campaign.phase=="PROTECTION_REQUIRED"
    assert store.get("ema-1").confirmed_bought==2
    assert not order_journal.get(key).projection_pending


def test_second_fill_after_restart_projects_only_new_quantity():
    campaign,key=prepare()
    observe(key,"OPEN",2)
    sign_fill(2)
    partial=store.project_verified_order(
        campaign.id,key,broker_fact("OPEN",2),expected_revision=1)
    observe(key,"COMPLETE",4)
    sign_fill(4)
    complete=store.project_verified_order(
        campaign.id,key,broker_fact("COMPLETE",4),
        expected_revision=partial.campaign.revision)
    assert complete.campaign.confirmed_bought==4
    assert complete.campaign.held_qty==4
    assert complete.uncovered_qty==4
    assert complete.campaign.entries[0].status=="COMPLETE"
    assert not order_journal.get(key).projection_pending
    with pytest.raises(store.LiveStoreConflict):
        store.project_verified_order(
            campaign.id,key,broker_fact("COMPLETE",4),
            expected_revision=complete.campaign.revision)
    assert store.get("ema-1").confirmed_bought==4


def test_stale_revision_or_wrong_broker_order_never_acknowledges_projection():
    campaign,key=prepare()
    observe(key,"OPEN",2)
    sign_fill(2)
    with pytest.raises(store.LiveStoreConflict,match="STALE_REVISION"):
        store.project_verified_order(
            campaign.id,key,broker_fact("OPEN",2),expected_revision=0)
    wrong=broker_fact("OPEN",2,order_id="ORDER-WRONG")
    with pytest.raises(store.LiveStoreConflict,match="IDENTITY_CONFLICT"):
        store.project_verified_order(
            campaign.id,key,wrong,expected_revision=1)
    assert order_journal.get(key).projection_pending
    assert store.get("ema-1").confirmed_bought==0


def test_wrong_account_or_missing_canonical_observation_fails_closed():
    campaign,key=prepare()
    with pytest.raises(store.LiveStoreConflict,match="NOT_PROJECTABLE"):
        store.project_verified_order(
            campaign.id,key,broker_fact("OPEN",0),expected_revision=1)
    with pytest.raises(store.LiveStoreConflict,match="IDENTITY_CONFLICT"):
        store.project_verified_order(
            campaign.id,key,broker_fact("OPEN",0,account_id="OTHER"),expected_revision=1)


@pytest.mark.parametrize("identity_changes", [
    {"uid": "mallory"},
    {"account_id": "KITE-OTHER"},
    {"exchange": "BSE"},
    {"symbol": "TCS"},
    {"product": "MIS"},
])
def test_foreign_broker_facts_refused_before_or_after_observation(identity_changes):
    # The canonical intent belongs to alice and one exact NSE/CNC broker
    # contract. Changing even one fact identity field must not project it,
    # regardless of whether the journal has recorded a fill.
    campaign, key = prepare()
    foreign = replace(broker_fact("OPEN", 0),
                      identity=replace(broker_fact("OPEN", 0).identity, **identity_changes))
    with pytest.raises(store.LiveStoreConflict, match="IDENTITY_CONFLICT"):
        store.project_verified_order(campaign.id, key, foreign, expected_revision=1)
    observe(key, "OPEN", 0)
    with pytest.raises(store.LiveStoreConflict, match="IDENTITY_CONFLICT"):
        store.project_verified_order(campaign.id, key, foreign, expected_revision=1)
    assert store.get(campaign.id).revision == 1
    assert store.get(campaign.id).confirmed_bought == 0
    assert order_journal.get(key).projection_pending


def test_canonical_conflict_blocks_strategy_projection():
    campaign,key=prepare()
    observe(key,"OPEN",2)
    sign_fill(2)
    order_journal.observe_order(
        key,status="OPEN",order_id="ORDER-123",
        filled_quantity=2,average_price=101)  # price restatement is quarantined
    assert order_journal.get(key).reconciliation_required
    with pytest.raises(store.LiveStoreConflict,match="JOURNAL_RECONCILIATION_PENDING"):
        store.project_verified_order(
            campaign.id,key,broker_fact("OPEN",2),expected_revision=1)


def test_paper_and_unknown_broker_modes_never_bind_in_strict_broker_fact_parser():
    identity=LiveOrderIdentity(uid="alice",account_id="KITE-LIVE-TEST",
        order_id="ORDER-123",exchange="NSE",symbol="INFY",product="CNC",
        side="BUY",quantity=4)
    history=[{"order_id":"ORDER-123","exchange":"NSE","tradingsymbol":"INFY",
              "product":"CNC","transaction_type":"BUY","quantity":4,
              "status":"OPEN","filled_quantity":0,"average_price":0}]
    from app.services.ema_trend.live_order_facts import LiveOrderEvidenceError
    for mode in (True,None):
        with pytest.raises(LiveOrderEvidenceError):
            broker_order_fact(identity,history,broker_account_id="KITE-LIVE-TEST",
                              broker_is_paper=mode)


@pytest.mark.asyncio
async def test_read_only_kite_gtt_confirmation_requires_existing_filled_cnc_inventory():
    from unittest.mock import AsyncMock
    from app.services.exchanges.kite.client import KiteClient

    campaign,key=prepare()
    observe(key,"COMPLETE",4)
    sign_fill(4)
    projection=store.project_verified_order(
        campaign.id,key,broker_fact("COMPLETE",4),expected_revision=1)
    assert projection.campaign.phase=="PROTECTION_REQUIRED"
    client=KiteClient(api_key="fake",api_secret="fake",access_token="fake",
                      is_paper=False)
    client._account_id="KITE-LIVE-TEST"
    client.get_gtt=AsyncMock(return_value={
        "id":901,"status":"active",
        "condition":{"exchange":"NSE","tradingsymbol":"INFY",
                     "trigger_values":[95]},
        "orders":[{"exchange":"NSE","tradingsymbol":"INFY","transaction_type":"SELL","product":"CNC",
                   "quantity":4,"order_type":"LIMIT","price":94.5}],
    })
    client.get_orders=AsyncMock(return_value=[])
    client.place_order=AsyncMock(side_effect=AssertionError("LIVE ORDER WRITE"))
    client.place_gtt=AsyncMock(side_effect=AssertionError("LIVE GTT WRITE"))
    client.delete_gtt=AsyncMock(side_effect=AssertionError("LIVE GTT DELETE"))
    verified=await store.confirm_existing_cnc_stop(
        campaign.id,"901",expected_revision=projection.campaign.revision,
        broker_client=client)
    assert verified.phase=="PROTECTED"
    assert verified.protection.verified_active
    assert verified.protection.qty==4
    assert store.get(campaign.id).phase=="PROTECTED"
    client.get_gtt.assert_awaited_once()
    client.get_orders.assert_awaited_once()
    client.place_order.assert_not_called()
    client.place_gtt.assert_not_called()
    client.delete_gtt.assert_not_called()


@pytest.mark.asyncio
async def test_stale_gtt_and_open_external_sell_never_prove_protection():
    from unittest.mock import AsyncMock
    from app.services.exchanges.kite.client import KiteClient

    campaign,key=prepare()
    observe(key,"COMPLETE",4)
    sign_fill(4)
    projection=store.project_verified_order(
        campaign.id,key,broker_fact("COMPLETE",4),expected_revision=1)
    client=KiteClient(api_key="fake",api_secret="fake",access_token="fake",
                      is_paper=False)
    client._account_id="KITE-LIVE-TEST"
    record={
        "id":901,"status":"triggered",
        "condition":{"exchange":"NSE","tradingsymbol":"INFY",
                     "trigger_values":[95]},
        "orders":[{"exchange":"NSE","tradingsymbol":"INFY","transaction_type":"SELL","product":"CNC",
                   "quantity":4,"order_type":"LIMIT","price":94.5}],
    }
    client.get_gtt=AsyncMock(return_value=record)
    client.get_orders=AsyncMock(return_value=[])
    from app.services.ema_trend.live_lifecycle import LiveLifecycleEvidenceError
    with pytest.raises(LiveLifecycleEvidenceError,match="NOT_ACTIVE"):
        await store.confirm_existing_cnc_stop(
            campaign.id,"901",expected_revision=projection.campaign.revision,
            broker_client=client)
    assert store.get("ema-1").phase=="PROTECTION_REQUIRED"
    record["status"]="active"
    client.get_orders=AsyncMock(return_value=[{
        "exchange":"NSE","tradingsymbol":"INFY",
        "transaction_type":"SELL","status":"OPEN",
    }])
    with pytest.raises(store.LiveStoreConflict,match="DUPLICATE_EXIT"):
        await store.confirm_existing_cnc_stop(
            campaign.id,"901",expected_revision=projection.campaign.revision,
            broker_client=client)
    assert store.get("ema-1").phase=="PROTECTION_REQUIRED"
