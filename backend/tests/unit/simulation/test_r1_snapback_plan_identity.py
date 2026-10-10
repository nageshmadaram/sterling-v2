"""A Snapback plan id is per tenant: the virtual tenant and the live tenant never share one."""
from __future__ import annotations

import hashlib

import pytest
from tenancy_support import OWNER, SIM_UID

from app.services.snapback_plan_producer import plan_id_for
from tests.snapback_live_harness import live_env, swing_row
from tests.snapback_live_harness import cycle as _cycle
from tests.snapback_live_harness import signal_id as _signal_id

_ = live_env   # registers the shared ``env`` fixture


def test_a_real_uid_keeps_the_legacy_digest():
    sid = "snapback:fade_up:NIFTY:123"
    legacy = "SBP-" + hashlib.sha256(sid.encode()).hexdigest()[:20]
    assert plan_id_for(sid) == legacy
    assert plan_id_for(sid, OWNER) == legacy
    assert plan_id_for(sid, "") == legacy


def test_a_sim_uid_gets_its_own_plan_id():
    sid = "snapback:fade_up:NIFTY:123"
    sim = plan_id_for(sid, SIM_UID)
    assert sim != plan_id_for(sid, OWNER) and sim.startswith("SBP-")
    assert sim == plan_id_for(sid, SIM_UID)                         # still derived, never random
    assert sim != plan_id_for(sid, "sim:someone-else")
    assert sim != plan_id_for(sid + "x", SIM_UID)


def test_a_published_sim_plan_does_not_block_the_live_tenant_on_the_same_signal(env):
    row = swing_row(signal_id=_signal_id())
    env.rows = [row]
    env.plan_store.publish(
        plan_id=plan_id_for(row["signal_id"], SIM_UID), opportunity_id=row["signal_id"],
        account_id="SIM-X", owner_uid=SIM_UID, option_symbol="NIFTY26NOV25000PE",
        option_quantity=75)

    status = _cycle(env)

    assert status["entries"] == 1, status
    assert env.plan_store.get(plan_id_for(row["signal_id"], env.uid))["status"] == "CONSUMED"


def test_a_published_live_plan_does_not_block_the_sim_tenant_on_the_same_signal(env):
    row = swing_row(signal_id=_signal_id())
    env.plan_store.publish(
        plan_id=plan_id_for(row["signal_id"], env.uid), opportunity_id=row["signal_id"],
        account_id="KITE-X", owner_uid=env.uid, option_symbol="NIFTY26NOV25000PE",
        option_quantity=75)
    assert env.plan_store.get(plan_id_for(row["signal_id"], SIM_UID)) is None


def test_the_risk_approver_resolves_the_plan_of_its_own_tenant(env):
    from app.services.snapback_live_wiring import SnapbackRiskApprover

    sid = _signal_id()
    for uid in (env.uid, SIM_UID):
        env.plan_store.publish(
            plan_id=plan_id_for(sid, uid), opportunity_id=sid, account_id="A", owner_uid=uid,
            option_symbol="X", option_quantity=75, cash_required=uid.__len__())
    approver = SnapbackRiskApprover(plan_store=env.plan_store, broker_client_fn=lambda *_: None)
    assert approver._plan_for(sid, env.uid)["owner_uid"] == env.uid
    assert approver._plan_for(sid, SIM_UID)["owner_uid"] == SIM_UID
    assert approver._plan_for(sid + ":hedge-recovery:2", SIM_UID)["owner_uid"] == SIM_UID
