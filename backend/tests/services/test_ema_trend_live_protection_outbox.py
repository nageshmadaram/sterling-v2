"""Hermetic EMA stop outbox fences, crash recovery and idempotency.

No broker is constructed and no order/GTT write operation is available.
"""
from decimal import Decimal

import pytest

from app.engines.ema_trend.live_contracts import BrokerIntent, BrokerStop, LiveCampaign
from app.services import db
from app.services.ema_trend import live_campaign_store as store
from app.services.ema_trend import live_protection_outbox as outbox
from app.services.kite_engine import fill_ledger, order_journal


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db,"_DB_PATH",str(tmp_path/"ema-outbox.db"))
    monkeypatch.setattr(db,"_available",False)
    assert db.init()


def filled_campaign():
    identity=dict(uid="alice",account_id="KITE-LIVE-TEST",strategy_id="ema_trend",
                  generation_id="frozen",signal_id="signal",exchange="NSE",
                  symbol="INFY",side="BUY")
    key=order_journal.make_key(**identity)
    prepared=LiveCampaign(
        id="ema-1",owner_uid="alice",account_id="KITE-LIVE-TEST",
        exchange="NSE",symbol="INFY",mode="swing",product="CNC",
        strategy_version="1",config_hash="frozen",signal_id="signal",
        stop=Decimal("95"),entries=[BrokerIntent(
            intent_key=key,side="BUY",requested_qty=4)],
    )
    store.create_prepared(prepared)
    order_journal.reserve(**identity,quantity=4,payload={"product":"CNC"})
    assert order_journal.claim_submission(key)
    order_journal.acknowledge(key,"ORDER-1")
    bound=store.bind_acknowledged_order("ema-1",key,expected_revision=0)
    assert bound.revision==1
    order_journal.observe_order(key,status="COMPLETE",order_id="ORDER-1",
                                filled_quantity=4,average_price=100)
    fill_ledger.record(account_id="KITE-LIVE-TEST",uid="alice",
        exchange="NSE",symbol="INFY",side="BUY",order_id="ORDER-1",
        cumulative_quantity=4,cumulative_value=400,source="entry",lot_size=1)
    from app.services.ema_trend.live_order_facts import LiveOrderIdentity,broker_order_fact
    ident=LiveOrderIdentity(
        uid="alice",account_id="KITE-LIVE-TEST",order_id="ORDER-1",
        exchange="NSE",symbol="INFY",product="CNC",side="BUY",quantity=4)
    fact=broker_order_fact(ident,[{
        "order_id":"ORDER-1","exchange":"NSE","tradingsymbol":"INFY",
        "product":"CNC","transaction_type":"BUY","quantity":4,
        "status":"COMPLETE","filled_quantity":4,"average_price":100,
    }],broker_account_id="KITE-LIVE-TEST",broker_is_paper=False)
    result=store.project_verified_order(
        "ema-1",key,fact,expected_revision=1)
    assert result.campaign.phase=="PROTECTION_REQUIRED"
    return result.campaign


def protect_for_test(campaign):
    stop=BrokerStop(trigger_id="901",exchange="NSE",symbol="INFY",product="CNC",
                    qty=campaign.held_qty,trigger=Decimal("95"),
                    verified_active=True)
    protected=LiveCampaign.model_validate({
        **campaign.model_dump(mode="python"),
        "phase":"PROTECTED","protection":stop.model_dump(mode="python"),
        "revision":campaign.revision+1,
    })
    with order_journal._transaction() as conn:
        store._schema(conn)
        store._save(conn,protected)
    return protected


def test_placement_fenced_and_idempotent_with_durable_key():
    c=filled_campaign()
    command=outbox.reserve_stop_command(c.id,"stop-1",kind="PLACE",
                                        desired_trigger="95",expected_revision=c.revision)
    assert command.status=="RESERVED"
    assert command.held_qty==4
    assert outbox.get("stop-1")==command
    again=outbox.reserve_stop_command(c.id,"stop-1",kind="PLACE",
                                      desired_trigger="95",expected_revision=c.revision)
    assert again==command
    with pytest.raises(outbox.StopCommandConflict,match="PREVIOUS_CHANGE_UNRESOLVED"):
        outbox.reserve_stop_command(c.id,"stop-2",kind="PLACE",
                                    desired_trigger="96",expected_revision=c.revision)
    with pytest.raises(outbox.StopCommandConflict,match="IDEMPOTENCY_CONFLICT"):
        outbox.reserve_stop_command(c.id,"stop-1",kind="PLACE",
                                    desired_trigger="97",expected_revision=c.revision)


def test_mark_unknown_survives_restart_and_blocks_retries():
    c=filled_campaign()
    outbox.reserve_stop_command(c.id,"stop-1",kind="PLACE",
                                desired_trigger=95,expected_revision=c.revision)
    first=outbox.mark_outcome_unknown("stop-1")
    assert first.status=="OUTCOME_UNKNOWN"
    assert outbox.mark_outcome_unknown("stop-1")==first
    assert outbox.get("stop-1")==first
    with pytest.raises(outbox.StopCommandConflict,match="PREVIOUS_CHANGE_UNRESOLVED"):
        outbox.reserve_stop_command(c.id,"stop-retry",kind="PLACE",
                                    desired_trigger=95,expected_revision=c.revision)
    assert store.get(c.id).phase=="PROTECTION_REQUIRED"


def test_tightening_requires_verified_stop_and_cannot_weaken():
    c=protect_for_test(filled_campaign())
    with pytest.raises(outbox.StopCommandConflict,match="STRICTLY_TIGHTEN"):
        outbox.reserve_stop_command(c.id,"bad",kind="TIGHTEN",
                                    desired_trigger=95,expected_revision=c.revision)
    with pytest.raises(outbox.StopCommandConflict,match="STRICTLY_TIGHTEN"):
        outbox.reserve_stop_command(c.id,"bad",kind="TIGHTEN",
                                    desired_trigger=94,expected_revision=c.revision)
    command=outbox.reserve_stop_command(c.id,"trail-1",kind="TIGHTEN",
                                        desired_trigger=96,expected_revision=c.revision)
    assert command.prior_gtt_id=="901"
    assert command.held_qty==4
    assert command.desired_trigger==Decimal("96")
    assert store.get(c.id).protection.trigger==Decimal("95")


def test_placement_refuses_insufficient_or_unknown_inventory():
    c=filled_campaign()
    with order_journal._transaction() as conn:
        conn.execute(
            "UPDATE kite_inventory SET net_quantity=3 WHERE account_id=? AND uid=? AND symbol=?",
            ("KITE-LIVE-TEST","alice","INFY"))
    with pytest.raises(outbox.StopCommandConflict,match="SIGNED_INVENTORY_UNVERIFIED"):
        outbox.reserve_stop_command(c.id,"stop-1",kind="PLACE",
                                    desired_trigger=95,expected_revision=c.revision)


def test_order_projection_pending_refuses_stop_mutation():
    c=filled_campaign()
    with order_journal._transaction() as conn:
        conn.execute(
            "UPDATE kite_order_intents SET projection_pending=1 WHERE uid='alice'")
    with pytest.raises(outbox.StopCommandConflict,match="CANONICAL_ORDER_RECOVERY_PENDING"):
        outbox.reserve_stop_command(c.id,"stop-1",kind="PLACE",
                                    desired_trigger=95,expected_revision=c.revision)


def test_orphan_gtt_and_uncertain_stop_refuse_new_mutation():
    c=filled_campaign()
    modified=LiveCampaign.model_validate({
        **c.model_dump(mode="python"),
        "phase":"RECOVERY_REQUIRED","orphan_stop_ids":["901"],
        "protection_submission_unknown":True,"revision":c.revision+1,
    })
    with order_journal._transaction() as conn:
        store._schema(conn)
        store._save(conn,modified)
    with pytest.raises(outbox.StopCommandConflict,match="CAMPAIGN_UNSAFE"):
        outbox.reserve_stop_command(c.id,"stop-1",kind="PLACE",
                                    desired_trigger=96,expected_revision=modified.revision)


@pytest.mark.parametrize("bad",["", " ", " leading", "a"*161])
def test_bad_idempotency_key_never_reserves(bad):
    c=filled_campaign()
    with pytest.raises(outbox.StopCommandConflict,match="IDEMPOTENCY_KEY_INVALID"):
        outbox.reserve_stop_command(c.id,bad,kind="PLACE",
                                    desired_trigger=95,expected_revision=c.revision)


def test_stale_campaign_revision_never_reserves_stop():
    c=filled_campaign()
    with pytest.raises(outbox.StopCommandConflict,match="STALE_REVISION"):
        outbox.reserve_stop_command(c.id,"stop-1",kind="PLACE",
                                    desired_trigger=95,expected_revision=c.revision-1)


def test_failed_transaction_does_not_leave_command_reserved(monkeypatch):
    c=filled_campaign()
    original=outbox._row
    def interrupt(*args,**kwargs):
        raise RuntimeError("CRASH_AFTER_INSERT_BEFORE_COMMIT")
    monkeypatch.setattr(outbox,"_row",interrupt)
    with pytest.raises(RuntimeError,match="CRASH_AFTER_INSERT"):
        outbox.reserve_stop_command(c.id,"stop-1",kind="PLACE",
                                    desired_trigger=95,expected_revision=c.revision)
    monkeypatch.setattr(outbox,"_row",original)
    with pytest.raises(outbox.StopCommandConflict,match="COMMAND_NOT_FOUND"):
        outbox.get("stop-1")
    recovered=outbox.reserve_stop_command(c.id,"stop-1",kind="PLACE",
                                          desired_trigger=95,expected_revision=c.revision)
    assert recovered.status=="RESERVED"


def test_reserving_stop_never_changes_protection_or_sends_order():
    c=filled_campaign()
    outbox.reserve_stop_command(c.id,"stop-1",kind="PLACE",
                                desired_trigger=96,expected_revision=c.revision)
    after=store.get(c.id)
    assert after.phase=="PROTECTION_REQUIRED"
    assert after.protection is None
    assert after.stop==Decimal("95")


@pytest.mark.asyncio
async def test_pending_or_unknown_stop_prevents_false_active_gtt_certification():
    from unittest.mock import AsyncMock
    from app.services.exchanges.kite.client import KiteClient

    c=filled_campaign()
    outbox.reserve_stop_command(c.id,"stop-1",kind="PLACE",
                                desired_trigger=95,expected_revision=c.revision)
    kite=KiteClient(api_key="fake",api_secret="fake",access_token="fake",
                    is_paper=False)
    kite._account_id="KITE-LIVE-TEST"
    kite.get_gtt=AsyncMock(return_value={
        "id":901,"status":"active",
        "condition":{"exchange":"NSE","tradingsymbol":"INFY",
                     "trigger_values":[95]},
        "orders":[{"transaction_type":"SELL","product":"CNC",
                   "quantity":4,"order_type":"LIMIT","price":94.5}],
    })
    kite.get_orders=AsyncMock(return_value=[])
    kite.place_order=AsyncMock(side_effect=AssertionError("NO LIVE ORDER"))
    kite.place_gtt=AsyncMock(side_effect=AssertionError("NO LIVE GTT"))
    kite.modify_gtt=AsyncMock(side_effect=AssertionError("NO LIVE GTT MODIFY"))
    kite.delete_gtt=AsyncMock(side_effect=AssertionError("NO LIVE GTT DELETE"))
    for status in ("RESERVED","OUTCOME_UNKNOWN"):
        if status=="OUTCOME_UNKNOWN":
            outbox.mark_outcome_unknown("stop-1")
        with pytest.raises(store.LiveStoreConflict,match="STOP_CHANGE_OUTCOME_UNRESOLVED"):
            await store.confirm_existing_cnc_stop(
                c.id,"901",expected_revision=c.revision,broker_client=kite)
        assert store.get(c.id).protection is None
        assert store.get(c.id).phase=="PROTECTION_REQUIRED"
    kite.place_order.assert_not_called()
    kite.place_gtt.assert_not_called()
    kite.modify_gtt.assert_not_called()
    kite.delete_gtt.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_gtt_readback_is_advisory_and_never_releases_fence():
    from unittest.mock import AsyncMock
    from app.services.exchanges.kite.client import KiteClient

    c=filled_campaign()
    outbox.reserve_stop_command(c.id,"stop-1",kind="PLACE",
                                desired_trigger=95,expected_revision=c.revision)
    outbox.mark_outcome_unknown("stop-1")
    kite=KiteClient(api_key="fake",api_secret="fake",access_token="fake",is_paper=False)
    kite._account_id=c.account_id
    kite.get_gtts=AsyncMock(return_value=[{
        "id":901,"status":"active","condition":{
            "exchange":"NSE","tradingsymbol":"INFY"},
    }])
    kite.get_orders=AsyncMock(return_value=[])
    kite.place_gtt=AsyncMock(side_effect=AssertionError("NO WRITE"))
    kite.modify_gtt=AsyncMock(side_effect=AssertionError("NO MODIFY"))
    kite.delete_gtt=AsyncMock(side_effect=AssertionError("NO DELETE"))
    report=await outbox.readback_stop_command("stop-1",broker_client=kite)
    assert report.blocker=="POSSIBLE_EXISTING_GTT_REQUIRES_OPERATOR_RECONCILIATION"
    assert report.candidate_gtt_ids==("901",)
    assert not report.retry_authorized and not report.broker_writes
    assert outbox.get("stop-1").status=="OUTCOME_UNKNOWN"
    with pytest.raises(outbox.StopCommandConflict,match="PREVIOUS_CHANGE_UNRESOLVED"):
        outbox.reserve_stop_command(c.id,"new-key",kind="PLACE",
                                    desired_trigger=95,expected_revision=c.revision)
    kite.place_gtt.assert_not_called()
    kite.modify_gtt.assert_not_called()
    kite.delete_gtt.assert_not_called()


@pytest.mark.asyncio
async def test_missing_gtt_or_broker_disconnection_does_not_authorize_retry():
    from unittest.mock import AsyncMock
    from app.services.exchanges.kite.client import KiteClient

    c=filled_campaign()
    outbox.reserve_stop_command(c.id,"stop-1",kind="PLACE",
                                desired_trigger=95,expected_revision=c.revision)
    outbox.mark_outcome_unknown("stop-1")
    kite=KiteClient(api_key="fake",api_secret="fake",access_token="fake",is_paper=False)
    kite._account_id=c.account_id
    kite.get_gtts=AsyncMock(return_value=[])
    kite.get_orders=AsyncMock(return_value=[])
    report=await outbox.readback_stop_command("stop-1",broker_client=kite)
    assert report.blocker=="ABSENCE_OF_GTT_DOES_NOT_PROVE_NO_PRIOR_SUBMISSION"
    assert not report.retry_authorized
    kite.get_gtts=AsyncMock(side_effect=TimeoutError("Kite disconnected"))
    report=await outbox.readback_stop_command("stop-1",broker_client=kite)
    assert report.blocker=="BROKER_BOOK_READ_UNAVAILABLE"
    assert not report.retry_authorized
    assert outbox.get("stop-1").status=="OUTCOME_UNKNOWN"


@pytest.mark.asyncio
async def test_open_gtt_child_or_unknown_identity_never_auto_resolves():
    from unittest.mock import AsyncMock
    from app.services.exchanges.kite.client import KiteClient

    c=filled_campaign()
    outbox.reserve_stop_command(c.id,"stop-1",kind="PLACE",
                                desired_trigger=95,expected_revision=c.revision)
    kite=KiteClient(api_key="fake",api_secret="fake",access_token="fake",is_paper=False)
    kite._account_id=c.account_id
    kite.get_gtts=AsyncMock(return_value=[])
    kite.get_orders=AsyncMock(return_value=[{
        "exchange":"NSE","tradingsymbol":"INFY",
        "transaction_type":"SELL","status":"OPEN",
    }])
    report=await outbox.readback_stop_command("stop-1",broker_client=kite)
    assert report.blocker=="BROKER_CHILD_OR_MANUAL_ORDER_UNRESOLVED"
    kite._account_id="OTHER"
    report=await outbox.readback_stop_command("stop-1",broker_client=kite)
    assert report.blocker=="BROKER_CLIENT_IDENTITY_UNVERIFIED"
    assert not report.retry_authorized
    assert outbox.get("stop-1").status=="RESERVED"
