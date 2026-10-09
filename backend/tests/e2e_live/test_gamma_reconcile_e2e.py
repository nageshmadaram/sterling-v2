"""A Gamma reconcile pass must leave a held, protected position alone.

Regression: ``gamma_move_runner`` read the broker's book through ``client.get_positions()``. The real client maps
those rows into models, the readers only parse dicts, so against a real session they saw no row and answered "the
broker holds none": the pass released the position's broker stop and closed it in the registry while 75 units were
still held. The same read serves the virtual client, which mirrors the real one's shapes.
"""
from __future__ import annotations

import pytest

from tests.e2e_live import scenarios as S
from tests.e2e_live.machine import UID
from tests.e2e_live.test_admission_dry_run import _explain, _fresh_live_machine

SIM = "sim:default"


@pytest.mark.asyncio
async def test_a_reconcile_pass_keeps_a_held_live_gamma_position_and_its_broker_stop(machine):
    await _fresh_live_machine(machine)
    S.operator_sets_daily_loss(machine)
    S.configure_gamma(machine)
    first = await S.drive_gamma(machine)
    assert first.ordered and first.protected, _explain(first)
    runner = machine.m("app.services.gamma_move_runner")
    store = machine.m("app.services.gamma_move_positions")
    (trigger,) = machine.exchange.active_gtts()

    for _ in range(2):
        await runner.reconcile(UID)

    pos = store.get(UID, S.GAMMA_SYMBOL)
    assert pos is not None and pos.is_open and pos.gtt_id == trigger["id"], "the position was closed in the registry"
    assert [g["id"] for g in machine.exchange.active_gtts()] == [trigger["id"]], "the broker stop was released"
    assert machine.exchange.net_quantity(S.GAMMA_SYMBOL) == 75
    assert machine.m("app.services.live_safety").kill_switch_state()["enabled"] is False
    client = await machine.client()
    assert await runner._broker_long_quantity(client, pos) == 75


@pytest.mark.asyncio
async def test_a_reconcile_pass_keeps_a_held_virtual_gamma_position_and_its_virtual_stop(machine):
    from tests.e2e_live.test_sim_e2e import _sim_world, settle, sim_http
    world = await _sim_world(machine)
    assert sim_http(machine, "PATCH", "/kite/engine/config", json={"auto_execute": True}).status_code == 200
    assert sim_http(machine, "PUT", "/config/gamma-move", json={
        "enabled": True, "auto_execute": True, "execution_lots": 1, "stop_mode": "both"}).status_code == 200
    runner = machine.m("app.services.gamma_move_runner")
    store = machine.m("app.services.gamma_move_positions")
    machine.exchange.set_quote(f"NFO:{S.GAMMA_SYMBOL}", 120.0, bid=119.5, ask=120.0)
    await runner._register_signals(SIM, [S.gamma_signal(machine, entry=120.0)])
    await settle(world)
    (gtt,) = world.client.exchange.gtts()

    for _ in range(2):
        await runner.reconcile(SIM)

    pos = store.get(SIM, S.GAMMA_SYMBOL)
    assert pos is not None and pos.is_open and pos.gtt_id == gtt["id"]
    assert world.client.exchange.gtt(gtt["id"])["status"] == "active"
    assert world.ledger.account_view(UID, world.client.exchange.today())["open_positions"] == 1
    assert machine.exchange.order_posts == [] and machine.exchange.gtts == {}
