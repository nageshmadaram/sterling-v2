"""The virtual tenant never counts as a broker account; a live uid never resolves to it.

Every test forces the failure the guard exists for: the sim account is made to LOOK live
(``is_paper`` False) in memory, so a filter that only trusted ``is_paper`` would let it through.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from tenancy_support import OWNER, SIM_ACCOUNT_ID, SIM_UID, install_fake_sim_client, live_account

from app.core import auth as core_auth
from app.services import external_positions as ep
from app.services import live_runtime as lr
from app.services.exchanges.kite import accounts as kite_accounts


@pytest.fixture
def both():
    live = live_account()
    sim = kite_accounts.ensure_sim_account(OWNER)
    return live, sim


@pytest.fixture
def sim_looks_live(both):
    """Worst case: the sim account is (wrongly) flagged live in memory."""
    live, sim = both
    sim.is_paper = False
    yield live, sim
    sim.is_paper = True


# ── live_runtime ─────────────────────────────────────────────────────────────
def test_live_runtime_recovers_only_real_accounts(sim_looks_live):
    live, _sim = sim_looks_live
    assert [a.id for a in lr._live_accounts()] == [live.id]


def test_live_runtime_ignores_a_sim_account_when_the_registry_is_a_fake():
    registry = SimpleNamespace(
        bootstrap=lambda: None,
        all_accounts=lambda: [
            SimpleNamespace(id=SIM_ACCOUNT_ID, user_id=SIM_UID, is_paper=False, is_active=True),
            SimpleNamespace(id="K1", user_id="u1", is_paper=False, is_active=True),
        ],
    )
    assert [a.id for a in lr._live_accounts(registry)] == ["K1"]


# ── external_positions ───────────────────────────────────────────────────────
def test_live_account_ids_never_include_the_sim_account(sim_looks_live):
    live, _sim = sim_looks_live
    assert ep.live_account_ids() == (live.id,)


async def test_external_exposure_never_reads_or_asks_the_sim_account(sim_looks_live, monkeypatch):
    live, _sim = sim_looks_live
    asked = []

    async def fake_for_client(client, *, resolved_account="", scan_automation=False):
        asked.append(getattr(client, "_account_id", ""))
        return ep.ExternalExposure(readable=True, accounts=(resolved_account,), positions=())

    monkeypatch.setattr(ep, "_external_for_client", fake_for_client)
    install_fake_sim_client(monkeypatch)
    result = await ep.external_exposure()
    assert result.readable
    assert asked == [live.id]


def _record(account_ids):
    from datetime import datetime, timezone
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(), "readable": True, "count": 0,
        "instruments": [], "automation": [], "accounts": list(account_ids),
        "by_account": {i: {"account": i, "readable": True, "count": 0, "instruments": [],
                           "automation": []} for i in account_ids},
    }


def test_admission_never_blocks_the_sim_account_for_not_being_in_the_broker_observation(both, monkeypatch):
    live, _sim = both
    monkeypatch.setattr(ep, "last_observation", lambda: _record([live.id]))   # sim is not in it
    assert ep.admission_blocker(SIM_ACCOUNT_ID) == ("", "")
    assert ep.admission_blocker(live.id) == ("", "")


def test_admission_still_blocks_a_live_account_missing_from_the_observation(both, monkeypatch):
    live, _sim = both
    monkeypatch.setattr(ep, "last_observation", lambda: _record(["SOMEONE-ELSE"]))
    code, _reason = ep.admission_blocker(live.id)
    assert code == "external_exposure_unreadable"            # live behaviour is unchanged
    assert ep.admission_blocker(SIM_ACCOUNT_ID) == ("", "")  # the virtual one is not asked


def test_admission_for_the_sim_account_does_not_depend_on_a_live_account_existing(monkeypatch):
    kite_accounts.ensure_sim_account(OWNER)               # no live account, no observation at all
    monkeypatch.setattr(ep, "last_observation", lambda: None)
    assert ep.admission_blocker(SIM_ACCOUNT_ID) == ("", "")


# ── core.auth ────────────────────────────────────────────────────────────────
def test_the_anonymous_dev_identity_is_not_disabled_by_a_sim_account_alone():
    kite_accounts.ensure_sim_account(OWNER)
    assert core_auth._live_account_exists() is False      # only a real live account closes that door


def test_a_live_account_still_closes_the_anonymous_dev_identity(both):
    assert core_auth._live_account_exists() is True


# ── operator report ──────────────────────────────────────────────────────────
def _broker_session_check(monkeypatch):
    import urllib.request

    def no_service(*args, **kwargs):             # the doctor also probes the running service
        raise OSError("not running in this test")

    monkeypatch.setattr(urllib.request, "urlopen", no_service)
    from app.core.operator_report import lane_doctor_checks

    (check,) = [c for c in lane_doctor_checks() if c.name == "broker_session"]
    return check


def test_operator_report_does_not_count_the_sim_account_as_a_broker_session(sim_looks_live, monkeypatch):
    live, _sim = sim_looks_live
    kite_accounts._accounts.pop(live.id)                  # only the (live-looking) sim account remains
    check = _broker_session_check(monkeypatch)
    assert check.passed is not True
    assert "no broker account is configured" in check.detail


def test_operator_report_still_sees_a_real_account(both, monkeypatch):
    check = _broker_session_check(monkeypatch)
    assert check.passed is True and "live account(s)" in check.detail
    assert SIM_ACCOUNT_ID not in check.detail


# ── nothing in a live uid's world can reach the sim account ──────────────────
async def test_acquire_client_for_a_live_uid_never_yields_the_sim_client(both, monkeypatch):
    built = install_fake_sim_client(monkeypatch)
    live, _sim = both
    client = await kite_accounts.acquire_client(kite_accounts.get_active(OWNER))
    assert client._account_id == live.id and client._is_paper is False
    assert built == []
    await kite_accounts.release_client(live.id)


async def test_acquire_client_for_the_sim_uid_never_yields_a_real_client(both, monkeypatch):
    built = install_fake_sim_client(monkeypatch)
    _live, sim = both
    client = await kite_accounts.acquire_client(kite_accounts.get_active(SIM_UID))
    assert client is built[0] and client._is_paper is True
    assert client._account_id == sim.id
    await kite_accounts.release_client(sim.id)
