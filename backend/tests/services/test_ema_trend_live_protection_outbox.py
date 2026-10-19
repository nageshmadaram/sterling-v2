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
        "orders":[{"exchange":"NSE","tradingsymbol":"INFY","transaction_type":"SELL","product":"CNC",
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


def test_stop_gtt_draft_uses_exact_owned_cnc_sell_contract_without_writing():
    from app.services.ema_trend.live_stop_draft import draft_stop_command
    c=filled_campaign()
    outbox.reserve_stop_command(c.id,"stop-draft",kind="PLACE",
                                desired_trigger=95,expected_revision=c.revision)
    draft=draft_stop_command("stop-draft",reference_bid=100,tick_size="0.05")
    assert not draft.broker_write_authorized
    assert draft.prior_trigger_id==""
    assert draft.change_kind=="PLACE"
    assert draft.request["exchange"]=="NSE"
    assert draft.request["tradingsymbol"]=="INFY"
    assert draft.request["trigger_values"]==[95]
    leg=draft.request["orders"][0]
    assert leg["transaction_type"]=="SELL"
    assert leg["quantity"]==4
    assert leg["product"]=="CNC"
    assert leg["order_type"]=="LIMIT"
    assert Decimal(str(leg["price"]))<=Decimal("95")
    assert draft.fingerprint==draft_stop_command(
        "stop-draft",reference_bid=100,tick_size="0.05").fingerprint
    assert outbox.get("stop-draft").status=="RESERVED"
    assert store.get(c.id).protection is None


def test_trailing_stop_draft_targets_original_broker_gtt_and_never_weakens():
    from app.services.ema_trend.live_stop_draft import draft_stop_command
    c=protect_for_test(filled_campaign())
    outbox.reserve_stop_command(c.id,"trail-draft",kind="TIGHTEN",
                                desired_trigger=96,expected_revision=c.revision)
    draft=draft_stop_command("trail-draft",reference_bid=100,tick_size="0.05")
    assert draft.change_kind=="TIGHTEN"
    assert draft.prior_trigger_id=="901"
    assert draft.request["trigger_values"]==[96]
    assert draft.request["orders"][0]["quantity"]==4
    assert store.get(c.id).protection.trigger==Decimal("95")
    assert not draft.broker_write_authorized


@pytest.mark.parametrize("bid,tick,expected",[
    (94,"0.05","BREACHED_OR_CROSSED_BID"),
    (95,"0.05","BREACHED_OR_CROSSED_BID"),
    (0,"0.05","REFERENCE_BID_INVALID"),
    (float("nan"),"0.05","REFERENCE_BID_INVALID"),
    (True,"0.05","REFERENCE_BID_INVALID"),
    (100,0,"TICK_INVALID"),
    (100,"nan","TICK_INVALID"),
    (100.03,"0.05","PRICE_OFF_TICK_GRID"),
    (100,"0.03","PRICE_OFF_TICK_GRID"),
])
def test_stop_draft_invalid_quote_or_tick_cannot_generate_executable_request(
        bid,tick,expected):
    from app.services.ema_trend.live_stop_draft import StopDraftBlocked,draft_stop_command
    c=filled_campaign()
    outbox.reserve_stop_command(c.id,"stop-draft",kind="PLACE",
                                desired_trigger=95,expected_revision=c.revision)
    with pytest.raises(StopDraftBlocked,match=expected):
        draft_stop_command("stop-draft",reference_bid=bid,tick_size=tick)
    assert outbox.get("stop-draft").status=="RESERVED"


def test_unknown_stop_outcome_cannot_be_reconverted_to_a_new_gtt_draft():
    from app.services.ema_trend.live_stop_draft import StopDraftBlocked,draft_stop_command
    c=filled_campaign()
    outbox.reserve_stop_command(c.id,"stop-draft",kind="PLACE",
                                desired_trigger=95,expected_revision=c.revision)
    outbox.mark_outcome_unknown("stop-draft")
    with pytest.raises(StopDraftBlocked,match="UNKNOWN_OUTCOME_CANNOT_RETRY"):
        draft_stop_command("stop-draft",reference_bid=100,tick_size="0.05")
    assert outbox.get("stop-draft").status=="OUTCOME_UNKNOWN"


@pytest.mark.parametrize("state",["SUBMITTED","PARTIAL"])
def test_acknowledged_or_partially_filled_canonical_order_blocks_gtt_reservation(state):
    c=filled_campaign()
    with order_journal._transaction() as conn:
        conn.execute(
            "UPDATE kite_order_intents SET state=? WHERE uid='alice' AND strategy_id='ema_trend'",
            (state,))
    with pytest.raises(outbox.StopCommandConflict,
                       match="CANONICAL_ORDER_RECOVERY_PENDING"):
        outbox.reserve_stop_command(c.id,"stop-1",kind="PLACE",
                                    desired_trigger=95,expected_revision=c.revision)


def test_campaign_with_working_buy_can_never_resize_a_stop_blindly():
    c=filled_campaign()
    buy=BrokerIntent(
        intent_key="pending-buy",side="BUY",requested_qty=1,
        order_id="SECOND-1",status="ACKNOWLEDGED")
    changed=LiveCampaign.model_validate({
        **c.model_dump(mode="python"),
        "entries":[*c.model_dump(mode="python")["entries"],buy.model_dump(mode="python")],
        "revision":c.revision+1,
    })
    with order_journal._transaction() as conn:
        store._schema(conn)
        store._save(conn,changed)
    with pytest.raises(outbox.StopCommandConflict,match="CAMPAIGN_HAS_WORKING_ORDER"):
        outbox.reserve_stop_command(changed.id,"stop-1",kind="PLACE",
                                    desired_trigger=95,expected_revision=changed.revision)


def test_gtt_ack_requires_unknown_pre_send_fence_and_preserves_idempotency():
    c=filled_campaign()
    outbox.reserve_stop_command(c.id,"ack-1",kind="PLACE",
                                desired_trigger=95,expected_revision=c.revision)
    with pytest.raises(outbox.StopCommandConflict,match="ACK_REQUIRES_PRE_SEND_FENCE"):
        outbox.record_unverified_broker_ack("ack-1","901")
    assert outbox.get("ack-1").status=="RESERVED"
    outbox.mark_outcome_unknown("ack-1")
    ack=outbox.record_unverified_broker_ack("ack-1","901")
    assert ack.status=="ACK_PENDING_VERIFICATION"
    assert ack.broker_trigger_id=="901"
    assert outbox.record_unverified_broker_ack("ack-1","901")==ack
    assert outbox.mark_outcome_unknown("ack-1")==ack
    with pytest.raises(outbox.StopCommandConflict,match="CONFLICTING_BROKER_ACK"):
        outbox.record_unverified_broker_ack("ack-1","902")
    with pytest.raises(outbox.StopCommandConflict,match="PREVIOUS_CHANGE_UNRESOLVED"):
        outbox.reserve_stop_command(c.id,"ack-2",kind="PLACE",
                                    desired_trigger=95,expected_revision=c.revision)
    assert outbox.get("ack-1").broker_trigger_id=="901"


@pytest.mark.parametrize("bad",["", "0", "000901", "not-a-gtt", "-1", " 901", "９０１", "9"*100])
def test_invalid_or_forged_gtt_ack_id_never_changes_status(bad):
    c=filled_campaign()
    outbox.reserve_stop_command(c.id,"ack-1",kind="PLACE",
                                desired_trigger=95,expected_revision=c.revision)
    outbox.mark_outcome_unknown("ack-1")
    with pytest.raises(outbox.StopCommandConflict,match="BROKER_ACK_ID_INVALID"):
        outbox.record_unverified_broker_ack("ack-1",bad)
    assert outbox.get("ack-1").status=="OUTCOME_UNKNOWN"


def test_tightening_ack_must_refer_to_original_gtt():
    c=protect_for_test(filled_campaign())
    outbox.reserve_stop_command(c.id,"trail-ack",kind="TIGHTEN",
                                desired_trigger=96,expected_revision=c.revision)
    outbox.mark_outcome_unknown("trail-ack")
    with pytest.raises(outbox.StopCommandConflict,match="TRAIL_ACK_GTT_ID_MISMATCH"):
        outbox.record_unverified_broker_ack("trail-ack","999")
    valid=outbox.record_unverified_broker_ack("trail-ack","901")
    assert valid.kind=="TIGHTEN" and valid.status=="ACK_PENDING_VERIFICATION"
    assert valid.prior_gtt_id=="901"


def test_unverified_ack_stays_durable_after_read_reconnect_and_never_unblocks_draft():
    from app.services.ema_trend.live_stop_draft import StopDraftBlocked,draft_stop_command
    c=filled_campaign()
    outbox.reserve_stop_command(c.id,"ack-draft",kind="PLACE",
                                desired_trigger=95,expected_revision=c.revision)
    outbox.mark_outcome_unknown("ack-draft")
    outbox.record_unverified_broker_ack("ack-draft","901")
    recovered=outbox.get("ack-draft")
    assert recovered.broker_trigger_id=="901"
    with pytest.raises(StopDraftBlocked,match="UNKNOWN_OUTCOME_CANNOT_RETRY"):
        draft_stop_command("ack-draft",reference_bid=100,tick_size="0.05")
    assert store.get(c.id).phase=="PROTECTION_REQUIRED"


@pytest.mark.asyncio
async def test_acknowledged_gtt_readback_still_cannot_unlock_broker_retry():
    from unittest.mock import AsyncMock
    from app.services.exchanges.kite.client import KiteClient
    c=filled_campaign()
    outbox.reserve_stop_command(c.id,"ack-readback",kind="PLACE",
                                desired_trigger=95,expected_revision=c.revision)
    outbox.mark_outcome_unknown("ack-readback")
    outbox.record_unverified_broker_ack("ack-readback","901")
    client=KiteClient(api_key="fake",api_secret="fake",access_token="fake",is_paper=False)
    client._account_id=c.account_id
    client.get_gtts=AsyncMock(return_value=[{
        "id":901,"status":"active",
        "condition":{"exchange":"NSE","tradingsymbol":"INFY"},
    }])
    client.get_orders=AsyncMock(return_value=[])
    client.place_gtt=AsyncMock(side_effect=AssertionError("NO GTT WRITE"))
    client.modify_gtt=AsyncMock(side_effect=AssertionError("NO GTT MODIFY"))
    client.delete_gtt=AsyncMock(side_effect=AssertionError("NO GTT DELETE"))
    report=await outbox.readback_stop_command("ack-readback",broker_client=client)
    assert report.blocker=="ACKED_GTT_REQUIRES_EXACT_STOP_COVERAGE_VERIFICATION"
    assert report.candidate_gtt_ids==("901",)
    assert not report.retry_authorized and not report.broker_writes
    assert outbox.get("ack-readback").status=="ACK_PENDING_VERIFICATION"
    client.get_gtts=AsyncMock(return_value=[])
    missing=await outbox.readback_stop_command("ack-readback",broker_client=client)
    assert missing.blocker=="ACKED_GTT_NOT_PRESENT_IN_BROKER_BOOK"
    client.get_gtts=AsyncMock(return_value=[
        {"id":901,"status":"active","condition":{"exchange":"NSE","tradingsymbol":"INFY"}},
        {"id":902,"status":"active","condition":{"exchange":"NSE","tradingsymbol":"INFY"}},
    ])
    duplicates=await outbox.readback_stop_command("ack-readback",broker_client=client)
    assert duplicates.blocker=="MULTIPLE_GTT_IDENTITIES_REQUIRE_RECONCILIATION"
    assert not duplicates.retry_authorized
    assert outbox.get("ack-readback").status=="ACK_PENDING_VERIFICATION"
    client.place_gtt.assert_not_called()
    client.modify_gtt.assert_not_called()
    client.delete_gtt.assert_not_called()


@pytest.mark.asyncio
async def test_unverified_ack_vetoes_existing_stop_certification():
    from unittest.mock import AsyncMock
    from app.services.exchanges.kite.client import KiteClient
    c=filled_campaign()
    outbox.reserve_stop_command(c.id,"ack-cert",kind="PLACE",
                                desired_trigger=95,expected_revision=c.revision)
    outbox.mark_outcome_unknown("ack-cert")
    outbox.record_unverified_broker_ack("ack-cert","901")
    kite=KiteClient(api_key="fake",api_secret="fake",access_token="fake",is_paper=False)
    kite._account_id=c.account_id
    kite.get_gtt=AsyncMock(return_value={
        "id":901,"status":"active",
        "condition":{"exchange":"NSE","tradingsymbol":"INFY",
                     "trigger_values":[95]},
        "orders":[{"exchange":"NSE","tradingsymbol":"INFY","transaction_type":"SELL","product":"CNC",
                   "quantity":4,"order_type":"LIMIT","price":94.5}],
    })
    kite.get_orders=AsyncMock(return_value=[])
    with pytest.raises(store.LiveStoreConflict,match="STOP_CHANGE_OUTCOME_UNRESOLVED"):
        await store.confirm_existing_cnc_stop(
            c.id,"901",expected_revision=c.revision,broker_client=kite)
    assert store.get(c.id).phase=="PROTECTION_REQUIRED"
    assert outbox.get("ack-cert").status=="ACK_PENDING_VERIFICATION"


def _fake_ack_kite(campaign, *, trigger=95, gtts=None, orders=None,
                   broker_qty=None):
    from unittest.mock import AsyncMock
    from app.services.exchanges.kite.client import KiteClient
    kite=KiteClient(api_key="fake",api_secret="fake",
                    access_token="fake",is_paper=False)
    kite._account_id=campaign.account_id
    rec={
        "id":901,"status":"active",
        "condition":{"exchange":"NSE","tradingsymbol":"INFY",
                     "trigger_values":[trigger]},
        "orders":[{"exchange":"NSE","tradingsymbol":"INFY","transaction_type":"SELL","product":"CNC",
                   "quantity":4,"order_type":"LIMIT","price":float(trigger)-0.5}],
    }
    kite.get_gtt=AsyncMock(return_value=rec)
    kite.get_gtts=AsyncMock(return_value=gtts if gtts is not None else [{
        "id":901,"status":"active",
        "condition":{"exchange":"NSE","tradingsymbol":"INFY"},
    }])
    kite.get_orders=AsyncMock(return_value=orders if orders is not None else [])
    kite.get_positions_raw=AsyncMock(return_value={
        "net":[{"exchange":"NSE","tradingsymbol":"INFY","product":"CNC",
                "quantity":campaign.held_qty if broker_qty is None else broker_qty}],
    })
    kite.get_holdings=AsyncMock(return_value=[])
    for name in ("place_order","cancel_order","modify_order",
                 "place_gtt","modify_gtt","delete_gtt"):
        setattr(kite,name,AsyncMock(side_effect=AssertionError("NO BROKER MUTATION")))
    return kite


def _no_writes(kite):
    for name in ("place_order","cancel_order","modify_order",
                 "place_gtt","modify_gtt","delete_gtt"):
        getattr(kite,name).assert_not_called()


@pytest.mark.asyncio
async def test_verified_ack_atomically_advances_campaign_and_releases_fence():
    c=filled_campaign()
    outbox.reserve_stop_command(c.id,"ack-verified",kind="PLACE",
                                desired_trigger=95,expected_revision=c.revision)
    outbox.mark_outcome_unknown("ack-verified")
    outbox.record_unverified_broker_ack("ack-verified","901")
    kite=_fake_ack_kite(c)
    verified=await outbox.verify_acknowledged_stop("ack-verified",broker_client=kite)
    assert verified.status=="VERIFIED" and verified.broker_trigger_id=="901"
    saved=store.get(c.id)
    assert saved.phase=="PROTECTED"
    assert saved.protection.verified_active
    assert saved.protection.trigger_id=="901"
    assert saved.confirmed_bought==4 and saved.held_qty==4
    assert saved.revision==c.revision+1
    assert outbox.get("ack-verified").status=="VERIFIED"
    _no_writes(kite)
    with pytest.raises(outbox.StopCommandConflict,match="BROKER_ACK_UNVERIFIED"):
        await outbox.verify_acknowledged_stop("ack-verified",broker_client=kite)


@pytest.mark.asyncio
async def test_trailing_ack_only_increases_confirmed_stop_and_preserves_gtt_identity():
    c=protect_for_test(filled_campaign())
    outbox.reserve_stop_command(c.id,"trail-verified",kind="TIGHTEN",
                                desired_trigger=96,expected_revision=c.revision)
    outbox.mark_outcome_unknown("trail-verified")
    outbox.record_unverified_broker_ack("trail-verified","901")
    kite=_fake_ack_kite(c,trigger=96)
    result=await outbox.verify_acknowledged_stop("trail-verified",broker_client=kite)
    assert result.status=="VERIFIED"
    after=store.get(c.id)
    assert after.stop==Decimal("96")
    assert after.protection.trigger==Decimal("96")
    assert after.protection.trigger_id=="901"
    assert after.phase=="PROTECTED"
    _no_writes(kite)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault,expected",[
    ("missing","MISSING_OR_COMPETING_GTT"),
    ("competing","MISSING_OR_COMPETING_GTT"),
    ("order","WORKING_BROKER_ORDER"),
    ("qty","BROKER_HOLDING_MISMATCH"),
    ("weak","WEAKER_THAN_REQUEST"),
    ("wrong_child","GTT_CONTRACT_UNVERIFIED"),
    ("disconnect","BROKER_FACTS_UNAVAILABLE"),
])
async def test_broker_gtt_faults_leave_ack_and_campaign_unverified(fault,expected):
    from unittest.mock import AsyncMock
    c=filled_campaign()
    outbox.reserve_stop_command(c.id,"ack-fault",kind="PLACE",
                                desired_trigger=95,expected_revision=c.revision)
    outbox.mark_outcome_unknown("ack-fault")
    outbox.record_unverified_broker_ack("ack-fault","901")
    kite=_fake_ack_kite(c)
    if fault=="missing":
        kite.get_gtts=AsyncMock(return_value=[])
    elif fault=="competing":
        kite.get_gtts=AsyncMock(return_value=[
            {"id":901,"status":"active","condition":{
                "exchange":"NSE","tradingsymbol":"INFY"}},
            {"id":902,"status":"active","condition":{
                "exchange":"NSE","tradingsymbol":"INFY"}},
        ])
    elif fault=="order":
        kite.get_orders=AsyncMock(return_value=[{
            "exchange":"NSE","tradingsymbol":"INFY",
            "transaction_type":"SELL","status":"OPEN",
        }])
    elif fault=="qty":
        kite.get_positions_raw=AsyncMock(return_value={
            "net":[{"exchange":"NSE","tradingsymbol":"INFY",
                    "product":"CNC","quantity":3}]})
    elif fault=="weak":
        kite.get_gtt=AsyncMock(return_value={
            "id":901,"status":"active",
            "condition":{"exchange":"NSE","tradingsymbol":"INFY",
                         "trigger_values":[94]},
            "orders":[{"exchange":"NSE","tradingsymbol":"INFY","transaction_type":"SELL","product":"CNC",
                       "quantity":4,"order_type":"LIMIT","price":93.5}],
        })
    elif fault=="wrong_child":
        kite.get_gtt=AsyncMock(return_value={
            "id":901,"status":"active",
            "condition":{"exchange":"NSE","tradingsymbol":"INFY",
                         "trigger_values":[95]},
            "orders":[{"exchange":"NSE","tradingsymbol":"INFY","transaction_type":"BUY","product":"CNC",
                       "quantity":4,"order_type":"LIMIT","price":94.5}],
        })
    elif fault=="disconnect":
        kite.get_gtt=AsyncMock(side_effect=TimeoutError("Kite offline"))
    with pytest.raises(outbox.StopCommandConflict,match=expected):
        await outbox.verify_acknowledged_stop("ack-fault",broker_client=kite)
    assert outbox.get("ack-fault").status=="ACK_PENDING_VERIFICATION"
    assert store.get(c.id).phase=="PROTECTION_REQUIRED"
    _no_writes(kite)


@pytest.mark.asyncio
async def test_verified_stop_transaction_rollback_cannot_release_fence(monkeypatch):
    c=filled_campaign()
    outbox.reserve_stop_command(c.id,"ack-crash",kind="PLACE",
                                desired_trigger=95,expected_revision=c.revision)
    outbox.mark_outcome_unknown("ack-crash")
    outbox.record_unverified_broker_ack("ack-crash","901")
    kite=_fake_ack_kite(c)
    original=store._save
    def simulated_disk_fault(*args,**kwargs):
        raise RuntimeError("CRASH_DURING_ATOMIC_STOP_COMMIT")
    monkeypatch.setattr(store,"_save",simulated_disk_fault)
    with pytest.raises(RuntimeError,match="CRASH_DURING_ATOMIC_STOP_COMMIT"):
        await outbox.verify_acknowledged_stop("ack-crash",broker_client=kite)
    monkeypatch.setattr(store,"_save",original)
    assert outbox.get("ack-crash").status=="ACK_PENDING_VERIFICATION"
    assert store.get(c.id).phase=="PROTECTION_REQUIRED"
    recovered=await outbox.verify_acknowledged_stop("ack-crash",broker_client=kite)
    assert recovered.status=="VERIFIED"
    _no_writes(kite)


def test_existing_stop_outbox_table_upgrades_in_place_with_no_data_loss():
    # A deployed pre-ACK schema is automatically migrated on first read.
    # Never require the operator to drop a pending command table.
    with order_journal._transaction() as conn:
        conn.execute("""
            CREATE TABLE ema_live_stop_commands(
                command_id TEXT PRIMARY KEY,
                campaign_id TEXT NOT NULL,
                owner_uid TEXT NOT NULL,
                account_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                prior_gtt_id TEXT NOT NULL,
                desired_trigger TEXT NOT NULL,
                held_qty INTEGER NOT NULL,
                campaign_revision INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_ms INTEGER NOT NULL)
        """)
        conn.execute("""
            INSERT INTO ema_live_stop_commands VALUES(
                'legacy-pending','legacy-campaign','alice','KITE-LIVE-TEST',
                'PLACE','','95',4,2,'OUTCOME_UNKNOWN',1)
        """)
    persisted=outbox.get("legacy-pending")
    assert persisted.status=="OUTCOME_UNKNOWN"
    assert persisted.broker_trigger_id==""
    accepted=outbox.record_unverified_broker_ack("legacy-pending","901")
    assert accepted.status=="ACK_PENDING_VERIFICATION"
    assert accepted.broker_trigger_id=="901"
    with order_journal._transaction() as conn:
        indexes={r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()}
        assert "ux_ema_live_stop_outstanding_v2" in indexes


@pytest.mark.asyncio
async def test_trail_weak_against_requested_stop_leaves_original_gtt_and_fence():
    # The broker's GTT still meets the old 95 stop, but the acknowledged
    # trailing command requested 96. It MUST NOT be marked verified.
    c=protect_for_test(filled_campaign())
    outbox.reserve_stop_command(c.id,"trail-weak",kind="TIGHTEN",
                                desired_trigger=96,expected_revision=c.revision)
    outbox.mark_outcome_unknown("trail-weak")
    outbox.record_unverified_broker_ack("trail-weak","901")
    kite=_fake_ack_kite(c,trigger=95)
    with pytest.raises(outbox.StopCommandConflict,match="WEAKER_THAN_REQUEST"):
        await outbox.verify_acknowledged_stop("trail-weak",broker_client=kite)
    after=store.get(c.id)
    assert after.revision==c.revision
    assert after.stop==Decimal("95")
    assert after.protection.trigger_id=="901"
    assert outbox.get("trail-weak").status=="ACK_PENDING_VERIFICATION"
    _no_writes(kite)


def test_pre_send_claim_is_single_use_and_preserves_uncertain_state_after_restart():
    c=filled_campaign()
    outbox.reserve_stop_command(c.id,"claim-once",kind="PLACE",
                                desired_trigger=95,expected_revision=c.revision)
    first=outbox.claim_stop_transmission("claim-once")
    assert first.status=="OUTCOME_UNKNOWN"
    assert outbox.get("claim-once")==first
    with pytest.raises(outbox.StopCommandConflict,match="ALREADY_CLAIMED_OR_UNRESOLVED"):
        outbox.claim_stop_transmission("claim-once")
    with pytest.raises(outbox.StopCommandConflict,match="PREVIOUS_CHANGE_UNRESOLVED"):
        outbox.reserve_stop_command(c.id,"claim-other-key",kind="PLACE",
                                    desired_trigger=95,expected_revision=c.revision)
    assert store.get(c.id).phase=="PROTECTION_REQUIRED"


def test_pre_send_claim_refuses_signed_inventory_change():
    c=filled_campaign()
    outbox.reserve_stop_command(c.id,"claim-drift",kind="PLACE",
                                desired_trigger=95,expected_revision=c.revision)
    with order_journal._transaction() as conn:
        conn.execute("""
            UPDATE kite_inventory SET net_quantity=3
            WHERE account_id=? AND uid=? AND symbol=?
        """,(c.account_id,c.owner_uid,c.symbol))
    with pytest.raises(outbox.StopCommandConflict,match="INVENTORY_CHANGED"):
        outbox.claim_stop_transmission("claim-drift")
    assert outbox.get("claim-drift").status=="RESERVED"


def test_pre_send_claim_refuses_campaign_revision_change():
    c=filled_campaign()
    outbox.reserve_stop_command(c.id,"claim-revision",kind="PLACE",
                                desired_trigger=95,expected_revision=c.revision)
    changed=c.model_copy(update={"revision":c.revision+1})
    with order_journal._transaction() as conn:
        store._save(conn,changed)
    with pytest.raises(outbox.StopCommandConflict,match="CAMPAIGN_CHANGED"):
        outbox.claim_stop_transmission("claim-revision")
    assert outbox.get("claim-revision").status=="RESERVED"


@pytest.mark.asyncio
async def test_fake_kite_placement_drill_one_write_followed_by_broker_verified_ack():
    # Only an explicit AsyncMock receives a simulated GTT write. The real
    # adapter is disconnected and the live-entry switch stays OFF.
    from unittest.mock import AsyncMock
    from app.services.ema_trend.live_stop_draft import draft_stop_command
    c=filled_campaign()
    outbox.reserve_stop_command(c.id,"fake-place",kind="PLACE",
                                desired_trigger=95,expected_revision=c.revision)
    draft=draft_stop_command("fake-place",reference_bid=100,tick_size="0.05")
    kite=_fake_ack_kite(c)
    kite.place_gtt=AsyncMock(return_value={"trigger_id":901})
    assert outbox.claim_stop_transmission("fake-place").status=="OUTCOME_UNKNOWN"
    result=await kite.place_gtt(**draft.request)
    assert result=={"trigger_id":901}
    ack=outbox.record_unverified_broker_ack("fake-place",str(result["trigger_id"]))
    assert ack.status=="ACK_PENDING_VERIFICATION"
    verified=await outbox.verify_acknowledged_stop("fake-place",broker_client=kite)
    assert verified.status=="VERIFIED"
    assert store.get(c.id).phase=="PROTECTED"
    kite.place_gtt.assert_awaited_once_with(**draft.request)
    for name in ("place_order","cancel_order","modify_order",
                 "modify_gtt","delete_gtt"):
        getattr(kite,name).assert_not_called()


@pytest.mark.asyncio
async def test_fake_kite_trailing_stop_drill_is_same_gtt_id_and_stricter_stop():
    from unittest.mock import AsyncMock
    from app.services.ema_trend.live_stop_draft import draft_stop_command
    c=protect_for_test(filled_campaign())
    outbox.reserve_stop_command(c.id,"fake-trail",kind="TIGHTEN",
                                desired_trigger=96,expected_revision=c.revision)
    draft=draft_stop_command("fake-trail",reference_bid=100,tick_size="0.05")
    kite=_fake_ack_kite(c,trigger=96)
    kite.modify_gtt=AsyncMock(return_value={"trigger_id":901})
    assert outbox.claim_stop_transmission("fake-trail").status=="OUTCOME_UNKNOWN"
    result=await kite.modify_gtt(int(draft.prior_trigger_id),**draft.request)
    outbox.record_unverified_broker_ack("fake-trail",str(result["trigger_id"]))
    verified=await outbox.verify_acknowledged_stop("fake-trail",broker_client=kite)
    assert verified.status=="VERIFIED"
    assert store.get(c.id).stop==Decimal("96")
    kite.modify_gtt.assert_awaited_once_with(901,**draft.request)
    for name in ("place_order","cancel_order","modify_order",
                 "place_gtt","delete_gtt"):
        getattr(kite,name).assert_not_called()


@pytest.mark.asyncio
async def test_fake_kite_timeout_leaves_unknown_and_never_retries_an_order():
    from unittest.mock import AsyncMock
    from app.services.ema_trend.live_stop_draft import draft_stop_command,StopDraftBlocked
    c=filled_campaign()
    outbox.reserve_stop_command(c.id,"fake-timeout",kind="PLACE",
                                desired_trigger=95,expected_revision=c.revision)
    draft=draft_stop_command("fake-timeout",reference_bid=100,tick_size="0.05")
    kite=_fake_ack_kite(c)
    kite.place_gtt=AsyncMock(side_effect=TimeoutError("ACK lost after request"))
    outbox.claim_stop_transmission("fake-timeout")
    with pytest.raises(TimeoutError,match="ACK lost"):
        await kite.place_gtt(**draft.request)
    assert outbox.get("fake-timeout").status=="OUTCOME_UNKNOWN"
    with pytest.raises(outbox.StopCommandConflict,match="ALREADY_CLAIMED_OR_UNRESOLVED"):
        outbox.claim_stop_transmission("fake-timeout")
    with pytest.raises(StopDraftBlocked,match="UNKNOWN_OUTCOME_CANNOT_RETRY"):
        draft_stop_command("fake-timeout",reference_bid=100,tick_size="0.05")
    kite.place_gtt.assert_awaited_once()
    assert store.get(c.id).phase=="PROTECTION_REQUIRED"
