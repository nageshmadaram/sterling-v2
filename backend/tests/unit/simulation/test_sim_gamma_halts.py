"""Gamma's halt sites: a real uid engages the process-wide kill switch exactly as before; the virtual tenant
halts ONLY itself (its own control-plane row) and still raises the CRITICAL alert. A simulation never reaches
the global switch, and a legacy instant-fill client halts nothing."""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.engines.gamma_move import GammaMoveConfig
from app.services import db, gamma_move_runner as gr, live_safety
from app.services.simulation import halt, tenant
from tests.services.test_gamma_move_production import position, signal
from tests.unit.simulation.tenancy_support import OWNER, SIM_ACCOUNT_ID, SIM_UID, wipe_tenants

SIM = SimpleNamespace(_is_paper=True, _is_simulated=True, _account_id=SIM_ACCOUNT_ID)
LIVE = SimpleNamespace(_is_paper=False, _account_id="acct-live")
LEGACY = SimpleNamespace(_is_paper=True, _account_id="paper-1")


@pytest.fixture(autouse=True)
def world(monkeypatch):
    from datetime import datetime, timedelta, timezone
    monkeypatch.setattr(gr, "_now", lambda: datetime(2026, 10, 5, 11, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))))
    wipe_tenants()
    tenant.ensure_control_plane(OWNER)
    w = SimpleNamespace(global_halts=[], alerts=[], notes=[])
    monkeypatch.setattr(live_safety, "set_kill_switch", lambda enabled, reason="": w.global_halts.append((enabled, reason)))
    monkeypatch.setattr(gr, "_alert_unresolved", lambda uid, pos, **kw: w.alerts.append((uid, kw)))
    monkeypatch.setattr(gr, "session_for", lambda *_a, **_k: SimpleNamespace(
        note=lambda kind, message: w.notes.append((kind, message)), cfg=GammaMoveConfig(stop_mode="both").validate(),
        signals={}, subscribed=set()))
    yield w
    wipe_tenants()


def sim_row():
    row = db.get_execution_control(uid=SIM_UID, account_id=SIM_ACCOUNT_ID)
    return row["operator_state"], row["reason_code"]


def sim_halted():
    return sim_row() == ("HALTED", halt.SIM_HALT_CODE)


# ── the helper's three outcomes ───────────────────────────────────────────────────────────
def test_a_real_uid_engages_the_global_switch_exactly_as_before(world):
    gr._halt_new_entries(OWNER, LIVE, "Gamma-Unwind something")
    assert world.global_halts == [(True, "Gamma-Unwind something")] and not sim_halted()


def test_the_virtual_tenant_halts_only_itself(world):
    gr._halt_new_entries(SIM_UID, SIM, "Gamma-Unwind something")
    assert world.global_halts == [] and sim_halted()


def test_a_simulated_client_under_a_real_uid_never_reaches_the_global_switch(world):
    gr._halt_new_entries(OWNER, SIM, "miswired")
    assert world.global_halts == [] and not sim_halted()


# ── _halt_unresolved_exit: the halt AND the CRITICAL alert ────────────────────────────────
@pytest.mark.parametrize("uid,client,global_halt,sim_halt,alert", [
    (SIM_UID, SIM, False, True, True),          # finding: the virtual tenant halts and pages
    (OWNER, LIVE, True, False, True),           # live behaviour is identical
    (OWNER, LEGACY, False, False, False),       # the legacy instant-fill client has nothing to halt
    (OWNER, SIM, False, False, False),          # a miswired virtual client neither halts live nor pages live
])
def test_an_unresolved_exit_halts_and_alerts_per_tenant(world, uid, client, global_halt, sim_halt, alert):
    pos = SimpleNamespace(instrument=SimpleNamespace(tradingsymbol="NIFTY26OCT24500CE"))
    gr._halt_unresolved_exit(uid, client, pos, "OID-1")
    assert bool(world.global_halts) is global_halt and sim_halted() is sim_halt and bool(world.alerts) is alert
    if global_halt:
        assert world.global_halts == [(True, "Gamma-Unwind exit unresolved for NIFTY26OCT24500CE order OID-1")]
    if alert:
        assert world.alerts[0][0] == uid and world.alerts[0][1]["what"] == "EXIT"


# ── an entry whose broker stop cannot be confirmed ────────────────────────────────────────
async def _arm(monkeypatch, uid, client):
    sig = signal(dte=1)
    stored = {}

    async def get_client(_uid):
        return client

    async def submit(**kwargs):
        return SimpleNamespace(success=True, status="ACKNOWLEDGED", order_id="GAMMA-1", error=None)

    async def resolve(**_kwargs):
        return SimpleNamespace(filled_quantity=10, average_price=100.0, terminal=True, settled=True,
                               reconciliation_required=False, projection_version=7, status="COMPLETE", reason="")

    def put(_uid, pos):
        stored["pos"] = pos
        return pos

    def mark_filled(_uid, _symbol, price):
        stored["pos"].status = "open"
        return stored["pos"]

    async def protection_fails(*_a, **_k):
        return False

    async def subscribe(*_a, **_k):
        return None

    from app.services import canonical_fill_reconciliation, canonical_strategy_entry
    cfg = GammaMoveConfig(execution_lots=1, stop_mode="both").validate()
    monkeypatch.setattr(gr, "session_for", lambda *_a, **_k: SimpleNamespace(
        signals={sig.id: sig}, note=lambda kind, message: None, cfg=cfg, subscribed=set()))
    monkeypatch.setattr(gr, "get_config", lambda _uid=None: cfg)
    monkeypatch.setattr(gr.positions_store, "get", lambda *_a: None)
    monkeypatch.setattr(gr.positions_store, "put", put)
    monkeypatch.setattr(gr.positions_store, "mark_filled", mark_filled)
    monkeypatch.setattr(gr, "_client", get_client)
    monkeypatch.setattr(gr, "_safety", lambda *_a, **_k: (True, ""))
    monkeypatch.setattr(gr, "_ensure_protection", protection_fails)
    monkeypatch.setattr(gr, "_subscribe_watched", subscribe)
    monkeypatch.setattr(canonical_strategy_entry, "submit_entry", submit)
    monkeypatch.setattr(canonical_fill_reconciliation, "resolve_entry_fill", resolve)
    monkeypatch.setattr(canonical_fill_reconciliation, "mark_projected", lambda _r: True)
    return await gr.arm(uid, sig.id)


async def test_a_live_entry_without_broker_protection_still_halts_globally(world, monkeypatch):
    result = await _arm(monkeypatch, OWNER, LIVE)
    assert result["code"] == "protection_not_confirmed" and world.global_halts and not sim_halted()


async def test_a_virtual_entry_without_a_confirmed_stop_halts_its_own_tenant_and_reports_it(world, monkeypatch):
    result = await _arm(monkeypatch, SIM_UID, SIM)
    assert result["ok"] is False and result["code"] == "protection_not_confirmed"
    assert sim_halted() and world.global_halts == []


# ── T1: the stop could not be synchronised (the site that used the account row, not the client) ──
async def _t1(monkeypatch, uid, client, *, active_mode):
    pos = position(hold="intraday")
    pos.status, pos.stop_mode = "open", "both"
    monkeypatch.setattr(gr.positions_store, "put", lambda _uid, p: p)
    monkeypatch.setattr(gr, "is_paper", lambda _uid: active_mode)       # what accounts.get_active(uid) resolved to

    async def get_client(_uid):
        return client

    async def unprotected(*_a, **_k):
        return False

    monkeypatch.setattr(gr, "_client", get_client)
    monkeypatch.setattr(gr, "_ensure_protection", unprotected)
    monkeypatch.setattr(gr._journal, "gm_t1", lambda *_a, **_k: None)
    return await gr._manage_position_tick(uid, pos, pos.t1)


async def test_t1_sync_failure_halts_the_virtual_tenant_even_when_its_account_row_is_missing(world, monkeypatch):
    """get_active(sim uid) returning None makes is_paper None; that must not reach the REAL kill switch."""
    await _t1(monkeypatch, SIM_UID, SIM, active_mode=None)
    assert sim_halted() and world.global_halts == []


async def test_t1_sync_failure_on_live_money_still_halts_globally_and_unknown_mode_stays_fail_closed(world, monkeypatch):
    await _t1(monkeypatch, OWNER, LIVE, active_mode=False)
    assert len(world.global_halts) == 1 and not sim_halted()
    await _t1(monkeypatch, OWNER, SimpleNamespace(_is_paper=None), active_mode=None)       # unknown mode: halt, as before
    assert len(world.global_halts) == 2


async def test_t1_sync_failure_on_a_legacy_paper_client_halts_nothing(world, monkeypatch):
    await _t1(monkeypatch, OWNER, LEGACY, active_mode=True)
    assert world.global_halts == [] and not sim_halted()


# ── the invariant: no Gamma halt site can reach the process-wide switch by itself ──────────
def test_no_gamma_halt_site_calls_the_global_kill_switch_directly():
    source = Path(gr.__file__).read_text()
    assert not re.search(r"live_safety\s*\.\s*set_kill_switch", source)
    assert source.count("_halt_new_entries(") >= 9              # every site routes through simulation.halt
