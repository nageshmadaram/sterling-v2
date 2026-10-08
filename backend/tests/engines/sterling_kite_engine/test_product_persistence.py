"""Product identity survives journal recovery, registry reload and broker writes."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.kite_engine.canonical_entry import STRATEGY_ID as KITE_STRATEGY_ID

from app.services import db
from app.services.kite_engine import execution_lifecycle as life
from app.services.kite_engine import monitor, order_journal as journal, positions, protection


def _entry(product="MIS"):
    intent = journal.reserve(
        uid="product-owner", account_id="account", strategy_id=KITE_STRATEGY_ID,
        generation_id="v1", signal_id="product-signal", exchange="NFO",
        symbol="OPT", side="BUY", quantity=50, capital_required=5000,
        available_capital=100000,
        payload=dict(product=product, stop_mode="both", stop_premium=80,
                     entry_premium=100, lot_size=50, target_premium=120),
    )
    assert journal.claim_submission(intent.intent_key)
    order = dict(order_id="entry", tag=intent.tag, tradingsymbol="OPT", exchange="NFO",
                 product=product, transaction_type="BUY", quantity=50,
                 status="COMPLETE", filled_quantity=50, average_price=100)
    return intent, order


def _client(product="MIS"):
    return SimpleNamespace(
        _is_paper=False, _account_id="account", _account_generation="",
        get_positions_raw=AsyncMock(return_value={"net": [
            dict(tradingsymbol="OPT", exchange="NFO", product=product, quantity=50),
            dict(tradingsymbol="OPT", exchange="NFO",
                 product="NRML" if product != "NRML" else "MIS", quantity=500),
        ]}),
        place_gtt=AsyncMock(return_value={"trigger_id": 42}),
        modify_gtt=AsyncMock(return_value={"trigger_id": 42}),
        delete_gtt=AsyncMock(return_value={"trigger_id": 42}),
        place_order_option=AsyncMock(return_value={"order_id": "exit"}),
        get_orders=AsyncMock(return_value=[]),
        get_ltp=AsyncMock(return_value={"NFO:OPT": {"last_price": 100}}),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("product", ["MIS", "NRML", "CNC"])
async def test_journal_product_survives_recovery_reload_protection_and_exit(product):
    intent, order = _entry(product)
    journal.transition(intent.intent_key, "UNKNOWN")
    client = _client(product)
    client.get_orders.return_value = [order]

    await life.recover(client, intent.uid)
    held = positions.get(intent.uid, "OPT")
    assert held.product == product and held.qty == 50
    if product == "MIS":
        assert held.gtt_id == 0 and not held.protection_pending
        client.place_gtt.assert_not_awaited()
    else:
        assert held.gtt_id == 42
        assert all(leg["product"] == product for leg in client.place_gtt.await_args.kwargs["orders"])
    stored = json.loads(db.get_config(f"kite_engine_positions_{intent.uid}"))[0]
    assert stored["product"] == product

    # Rebuild the registry from durable state, then trail the real helper and exit.
    positions.reset(intent.uid)
    restored = positions.get(intent.uid, "OPT")
    assert restored is not held and restored.product == product
    restored.stop_premium = 90
    await life._protect(client, restored)
    if product == "MIS":
        client.modify_gtt.assert_not_awaited()
    else:
        assert all(leg["product"] == product for leg in client.modify_gtt.await_args.kwargs["orders"])
    assert await monitor._exit_position(client, intent.uid, restored, 100, reason="manual")
    sent = client.place_order_option.await_args
    assert sent.args == ("OPT", "sell", 50)
    assert sent.kwargs["product"] == product
    assert restored.status == positions.OPEN  # ACK is not a confirmed exit fill.
    assert restored.exit_order_id == "exit"


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_product", ["NRML", "", None, "UNKNOWN"])
async def test_mis_intent_rejects_cross_product_or_unknown_fill(wrong_product):
    intent, order = _entry()
    client = _client()
    await life.consume_order(client, intent.uid, {**order, "product": wrong_product})
    assert positions.get(intent.uid, "OPT") is None
    assert journal.find(uid=intent.uid, account_id="account", tag=intent.tag).state == "SUBMITTING"
    client.place_gtt.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("product", [None, "", "UNKNOWN"])
async def test_explicit_unknown_journal_product_never_defaults_to_nrml(product):
    intent, order = _entry(product)
    with pytest.raises(ValueError, match="journal_product_identity_unknown"):
        life.register_pending(intent, "entry")
    client = _client("NRML")
    await life.consume_order(client, intent.uid, {**order, "product": "NRML"})
    client.place_gtt.assert_not_awaited()
    assert positions.get(intent.uid, "OPT") is None


def test_repeated_projection_cannot_relabel_a_different_product():
    intent, _ = _entry()
    positions.register(positions.OpenPosition(
        uid=intent.uid, symbol="OPT", exchange="NFO", account_id="account",
        order_id="entry", product="NRML", status=positions.OPEN, qty=50,
    ))
    with pytest.raises(ValueError, match="position_journal_identity_mismatch"):
        life.register_pending(intent, "entry")
    assert positions.get(intent.uid, "OPT").product == "NRML"


@pytest.mark.asyncio
@pytest.mark.parametrize("product", [None, "", "UNKNOWN"])
async def test_unknown_persisted_product_never_sends_protection_or_exit(product):
    uid = "unknown-product"
    positions.register(positions.OpenPosition(
        uid=uid, symbol="OPT", exchange="NFO", account_id="account", product=product,
        status=positions.OPEN, qty=50, stop_premium=80, fill_price=100,
    ))
    positions.reset(uid)
    held = positions.get(uid, "OPT")
    client = _client("NRML")
    await life._protect(client, held)
    assert not await monitor._exit_position(client, uid, held, 100)
    client.place_gtt.assert_not_awaited()
    client.delete_gtt.assert_not_awaited()
    client.place_order_option.assert_not_awaited()


def test_legacy_nrml_row_and_intent_remain_compatible():
    intent = journal.reserve(
        uid="legacy", account_id="account", strategy_id="sterling-kite",
        generation_id="v1", signal_id="legacy", exchange="NFO", symbol="OPT",
        side="BUY", quantity=50, capital_required=5000, available_capital=100000,
    )
    assert life.register_pending(intent, "entry").product == "NRML"
    raw = json.loads(db.get_config("kite_engine_positions_legacy"))
    raw[0].pop("product")
    db.set_config("kite_engine_positions_legacy", json.dumps(raw))
    positions.reset("legacy")
    assert positions.get("legacy", "OPT").product == "NRML"


@pytest.mark.asyncio
async def test_paper_registration_preserves_explicit_product_and_blocks_cross_product_scale_in():
    client = _client()
    client._is_paper = True
    kwargs = dict(symbol="OPT", exchange="NFO", token=0, qty=50, lot_size=50,
                  entry_premium=100, stop_premium=80, order_id="paper-entry",
                  stop_mode="broker", product="MIS")
    armed = await protection.arm_position(client, "paper-product", **kwargs)
    assert armed.position.product == "MIS"
    assert client.place_gtt.await_args.kwargs["orders"][0]["product"] == "MIS"
    positions.reset("paper-product")
    assert positions.get("paper-product", "OPT").product == "MIS"
    with pytest.raises(ValueError, match="protection_position_identity_mismatch"):
        await protection.arm_position(client, "paper-product", **{**kwargs, "product": "NRML"})
    client.delete_gtt.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, "false", 0, 1])
async def test_unknown_broker_mode_never_enters_paper_protection_or_exit(mode):
    client = _client()
    client._is_paper = mode
    held = positions.register(positions.OpenPosition(
        uid="unknown-mode", symbol="OPT", exchange="NFO", account_id="account",
        product="MIS", status=positions.OPEN, qty=50, gtt_id=42,
    ))
    assert not await monitor._exit_position(client, held.uid, held, 100)
    with pytest.raises(ValueError, match="protection_broker_mode_unknown"):
        await protection.arm_position(
            client, held.uid, symbol="OPT", exchange="NFO", token=0, qty=50,
            lot_size=50, entry_premium=100, stop_premium=80, order_id="entry", stop_mode="both",
        )
    client.place_gtt.assert_not_awaited()
    client.delete_gtt.assert_not_awaited()
    client.place_order_option.assert_not_awaited()
