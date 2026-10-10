"""The virtual tenant never sits in a live scan loop: live tenants go first, awaited, and a slow or hung
virtual pass cannot delay them."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from tenancy_support import OWNER, SIM_UID

from app.services import gamma_move_runner as gm
from app.services import sim_scan_lane as lane


@pytest.fixture(autouse=True)
def _lane_clean():
    lane.cancel_all()
    yield
    lane.cancel_all()


# ── the lane ──────────────────────────────────────────────────────────────────
def test_split_keeps_real_tenants_apart_from_virtual_ones():
    assert lane.split(["a", SIM_UID, "b", "sim:b"]) == (["a", "b"], [SIM_UID, "sim:b"])


async def test_a_lane_pass_is_single_flight_and_frees_its_slot():
    gate, runs = asyncio.Event(), []

    async def work():
        runs.append(1)
        await gate.wait()

    assert lane.spawn("k", SIM_UID, work) is True
    assert lane.spawn("k", SIM_UID, work) is False           # the previous pass is still running
    assert lane.spawn("other", SIM_UID, work) is True        # another loop has its own lane
    gate.set()
    await lane.drain()
    assert len(runs) == 2 and lane.running() == 0
    assert lane.spawn("k", SIM_UID, work) is True            # and the slot is free again
    await lane.drain()


async def test_a_failing_or_hung_pass_never_raises_into_the_caller():
    async def boom():
        raise RuntimeError("sim blew up")

    async def hang():
        await asyncio.Event().wait()

    assert lane.spawn("k", SIM_UID, boom)
    await lane.drain()
    assert lane.spawn("k", SIM_UID, hang, timeout_s=0.05)
    await asyncio.wait_for(lane.drain(), timeout=2.0)
    assert lane.running() == 0


# ── Gamma Unwind ──────────────────────────────────────────────────────────────
async def test_gamma_scans_real_tenants_in_order_and_never_awaits_the_virtual_one(monkeypatch):
    seen, gate = [], asyncio.Event()

    async def scan_uid(uid, now, out):
        seen.append(uid)
        if uid == SIM_UID:
            await gate.wait()                                # a pass that takes "forever"
        else:
            out[uid] = {"scanned": True}

    monkeypatch.setattr(gm, "_kite_user_ids", lambda: [SIM_UID, "u1", OWNER])   # sim first, even
    monkeypatch.setattr(gm, "_scan_uid", scan_uid)
    out = await asyncio.wait_for(gm.scan_all_once(), timeout=1.0)
    assert out == {"u1": {"scanned": True}, OWNER: {"scanned": True}}
    assert seen[:2] == ["u1", OWNER]                         # real tenants first, in order
    await gm.scan_all_once()                                 # the next 5 s cycle is not blocked either
    await asyncio.sleep(0)
    assert seen.count(SIM_UID) == 1                          # and the virtual pass is single flight
    gate.set()
    await lane.drain()


async def test_gamma_virtual_pass_failure_does_not_reach_the_live_cycle(monkeypatch):
    async def scan_uid(uid, now, out):
        if uid == SIM_UID:
            raise RuntimeError("virtual scan failed")
        out[uid] = {"ok": True}

    monkeypatch.setattr(gm, "_kite_user_ids", lambda: [OWNER, SIM_UID])
    monkeypatch.setattr(gm, "_scan_uid", scan_uid)
    assert await gm.scan_all_once() == {OWNER: {"ok": True}}
    await lane.drain()


# ── Supertrend ────────────────────────────────────────────────────────────────
async def test_supertrend_scans_real_accounts_and_never_awaits_the_virtual_one(monkeypatch):
    from app.services.exchanges.kite import accounts as kite_accounts
    from app.services.kite_engine import service

    live = SimpleNamespace(user_id=OWNER, id="K1", connected=True, is_active=True)
    sim = SimpleNamespace(user_id=SIM_UID, id="SIM-X", connected=True, is_active=True)
    scanned, gate = [], asyncio.Event()

    class Client:
        async def get_profile(self):
            return {}

    async def acquire(acct):
        return Client()

    async def scan_user(client, uid):
        scanned.append(uid)
        if uid == SIM_UID:
            await gate.wait()

    monkeypatch.setattr(kite_accounts, "_load_from_db", lambda: [sim, live])
    monkeypatch.setattr(kite_accounts, "acquire_client", acquire)
    monkeypatch.setattr(service, "scan_user", scan_user)
    before = service._last_scan_wall_s
    await asyncio.wait_for(service._scan_all_connected_once(), timeout=1.0)
    assert scanned[0] == OWNER and service._last_scan_wall_s >= before
    await asyncio.sleep(0)
    assert scanned.count(SIM_UID) == 1 and lane.running("supertrend") == 1
    gate.set()
    await lane.drain()


async def test_a_virtual_supertrend_pass_does_not_set_the_live_scan_clock(monkeypatch):
    from app.services.exchanges.kite import accounts as kite_accounts
    from app.services.kite_engine import service

    sim = SimpleNamespace(user_id=SIM_UID, id="SIM-X", connected=True, is_active=True)

    class Client:
        async def get_profile(self):
            return {}

    async def acquire(acct):
        return Client()

    async def scan_user(client, uid):
        return None

    monkeypatch.setattr(kite_accounts, "_load_from_db", lambda: [sim])
    monkeypatch.setattr(kite_accounts, "acquire_client", acquire)
    monkeypatch.setattr(service, "scan_user", scan_user)
    monkeypatch.setattr(service, "_last_scan_wall_s", 0.0)
    monkeypatch.setattr(service, "_first_scan_done", False)
    await service._scan_all_connected_once()
    await lane.drain()
    assert service._first_scan_done is False and service._last_scan_wall_s == 0.0


# ── Snapback ──────────────────────────────────────────────────────────────────
async def test_snapback_cycles_real_tenants_first_and_a_slow_virtual_cycle_never_delays_them(monkeypatch):
    from app.services import snapback_dispatcher as sd

    cycles, gate = [], asyncio.Event()
    dispatcher = sd.SnapbackDispatcher()

    async def cycle(uid):
        cycles.append(uid)
        if uid == SIM_UID:
            await gate.wait()
        return {}

    monkeypatch.setattr(sd, "_kite_user_ids", lambda: [SIM_UID, OWNER])
    monkeypatch.setattr(dispatcher, "cycle", cycle)
    task = asyncio.create_task(dispatcher.run_forever(0.01))
    await asyncio.sleep(0.15)
    task.cancel()
    gate.set()
    await asyncio.gather(task, return_exceptions=True)
    await lane.drain()
    assert cycles.count(OWNER) >= 3 and cycles.count(SIM_UID) == 1


async def test_a_virtual_snapback_cycle_does_not_vouch_for_the_live_dispatcher(monkeypatch):
    from app.services import snapback_dispatcher as sd
    from app.services import snapback_health

    beats = []
    monkeypatch.setattr(snapback_health, "record_cycle", lambda name: beats.append(name))
    dispatcher = sd.SnapbackDispatcher()

    async def locked(uid):
        return {}

    monkeypatch.setattr(dispatcher, "_cycle_locked", locked)
    await dispatcher.cycle(SIM_UID)
    assert beats == []
    await dispatcher.cycle(OWNER)
    assert beats == ["live_dispatcher"]
