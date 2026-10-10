"""The virtual tenant's account: shape, persistence, isolation in both directions."""
from __future__ import annotations

import sys

import pytest
from tenancy_support import (
    OWNER, SIM_ACCOUNT_ID, SIM_UID, FakeSimClient, install_fake_sim_client, live_account,
)

from app.services import db
from app.services.exchanges.kite import accounts as kite_accounts
from app.services.exchanges.kite import auth as kite_auth
from app.services.exchanges.kite.models import KiteAccountCreate, KiteAccountUpdate

SimAccountProtected = kite_accounts.SimAccountProtected


def _rows():
    with db._conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM kite_accounts ORDER BY id").fetchall()]


# ── shape ────────────────────────────────────────────────────────────────────
def test_ensure_sim_account_creates_the_virtual_account():
    a = kite_accounts.ensure_sim_account(OWNER)
    assert (a.id, a.user_id, a.label) == (SIM_ACCOUNT_ID, SIM_UID, "Simulation")
    assert a.is_active and a.is_paper and a.is_sim
    assert a.connected and a.token_is_live          # the synthetic, always-valid session
    assert not a.has_credentials and not a.has_refresh_token
    assert (a.api_key, a.api_secret_enc, a.access_token_enc, a.refresh_token_enc) == ("", "", "", "")
    assert a.kite_user_id == SIM_ACCOUNT_ID         # synthetic broker id, unique per owner


def test_sim_account_is_durable_and_reloads_identically():
    kite_accounts.ensure_sim_account(OWNER)
    (loaded,) = [x for x in kite_accounts._load_from_db() if x.id == SIM_ACCOUNT_ID]
    assert loaded.user_id == SIM_UID and loaded.is_active and loaded.is_paper
    assert loaded.connected                         # kite_engine reads _load_from_db(): a.connected and a.is_active


def test_ensure_is_idempotent_and_repairs_drift():
    first = kite_accounts.ensure_sim_account(OWNER)
    assert kite_accounts.ensure_sim_account(OWNER) is first
    assert len(_rows()) == 1
    first.label, first.is_active = "renamed", False   # drift in memory and on disk
    kite_accounts._persist(first)
    healed = kite_accounts.ensure_sim_account(OWNER)
    assert (healed.label, healed.is_active) == ("Simulation", True)
    assert len(_rows()) == 1


def test_ensure_accepts_a_sim_uid_as_the_owner():
    assert kite_accounts.ensure_sim_account(SIM_UID).id == SIM_ACCOUNT_ID


def test_each_owner_gets_its_own_virtual_account():
    a, b = kite_accounts.ensure_sim_account("alice"), kite_accounts.ensure_sim_account("bob")
    assert a.id != b.id and a.kite_user_id != b.kite_user_id


# ── isolation, both directions ───────────────────────────────────────────────
def test_get_active_of_sim_uid_is_only_the_sim_account():
    live = live_account()
    kite_accounts.ensure_sim_account(OWNER)
    assert kite_accounts.get_active(SIM_UID).id == SIM_ACCOUNT_ID
    assert kite_accounts.get_active(OWNER).id == live.id


def test_get_active_of_a_real_uid_never_returns_a_sim_account():
    sim = kite_accounts.ensure_sim_account(OWNER)
    assert kite_accounts.get_active(OWNER) is None
    # even if a sim account were mislabelled onto a real tenant in memory
    sim.user_id = OWNER
    assert kite_accounts.get_active(OWNER) is None
    assert kite_accounts.list_accounts(OWNER) == []
    assert kite_accounts.get(OWNER, SIM_ACCOUNT_ID) is None


def test_the_two_populations_cannot_look_each_other_up():
    live = live_account()
    kite_accounts.ensure_sim_account(OWNER)
    assert kite_accounts.get(OWNER, SIM_ACCOUNT_ID) is None
    assert kite_accounts.get(SIM_UID, live.id) is None
    assert [a.id for a in kite_accounts.list_accounts(OWNER)] == [live.id]
    assert [a.id for a in kite_accounts.list_accounts(SIM_UID)] == [SIM_ACCOUNT_ID]


def test_live_accounts_never_include_the_sim_account_but_all_accounts_does():
    live = live_account()
    kite_accounts.ensure_sim_account(OWNER)
    assert [a.id for a in kite_accounts.live_accounts()] == [live.id]
    assert {a.id for a in kite_accounts.all_accounts()} == {live.id, SIM_ACCOUNT_ID}


def test_kite_postbacks_never_resolve_to_a_sim_account():
    kite_accounts.ensure_sim_account(OWNER)
    assert kite_accounts.find_by_kite_user_id(SIM_ACCOUNT_ID) is None


def test_migrating_legacy_paper_accounts_never_converts_the_sim_account():
    """The virtual account is is_paper by design; the boot conversion must not make it live."""
    legacy = live_account(connected=False, label="legacy")
    kite_accounts._accounts[legacy.id].is_paper = True      # a row an older build wrote
    kite_accounts._persist(kite_accounts._accounts[legacy.id])
    kite_accounts.ensure_sim_account(OWNER)

    results = kite_accounts.migrate_legacy_paper_accounts()

    assert [r.account_id for r in results] == [legacy.id]    # only the legacy row was touched
    sim = kite_accounts.get(SIM_UID, SIM_ACCOUNT_ID)
    assert sim.is_paper is True
    assert [r["is_paper"] for r in _rows() if r["id"] == SIM_ACCOUNT_ID] == [1]
    assert kite_accounts.get(OWNER, legacy.id).is_paper is False


# ── nothing can make it live or give it a credential ─────────────────────────
def test_the_sim_tenant_cannot_gain_another_account():
    kite_accounts.ensure_sim_account(OWNER)
    with pytest.raises(SimAccountProtected):
        kite_accounts.add(SIM_UID, KiteAccountCreate(label="x", api_key="k", api_secret="s"))
    assert len(_rows()) == 1


@pytest.mark.parametrize("change", [
    KiteAccountUpdate(api_key="K"), KiteAccountUpdate(api_secret="S"),
    KiteAccountUpdate(is_paper=False), KiteAccountUpdate(label="x"),
])
def test_update_refuses_the_sim_account(change):
    kite_accounts.ensure_sim_account(OWNER)
    with pytest.raises(SimAccountProtected):
        kite_accounts.update(SIM_UID, SIM_ACCOUNT_ID, change)
    assert kite_accounts.get(SIM_UID, SIM_ACCOUNT_ID).is_paper is True


def test_delete_activate_login_logout_identity_refuse_the_sim_account():
    kite_accounts.ensure_sim_account(OWNER)
    with pytest.raises(SimAccountProtected):
        kite_accounts.delete(SIM_UID, SIM_ACCOUNT_ID)
    with pytest.raises(SimAccountProtected):
        kite_accounts.set_active(SIM_UID, SIM_ACCOUNT_ID)
    with pytest.raises(SimAccountProtected):
        kite_accounts.save_session(SIM_UID, SIM_ACCOUNT_ID, access_token="T", kite_user_id="Z")
    with pytest.raises(SimAccountProtected):
        kite_accounts.clear_session(SIM_UID, SIM_ACCOUNT_ID)
    with pytest.raises(SimAccountProtected):
        kite_accounts.set_identity(SIM_UID, SIM_ACCOUNT_ID, kite_user_id="Z")
    assert len(_rows()) == 1 and kite_accounts.get_active(SIM_UID).connected


def test_a_session_for_the_sim_account_cannot_be_forced_through_the_owner_either():
    kite_accounts.ensure_sim_account(OWNER)
    # the owner's uid does not even see the sim account, so the write finds nothing
    assert kite_accounts.save_session(OWNER, SIM_ACCOUNT_ID, access_token="T", kite_user_id="Z") is None
    assert kite_accounts.delete(OWNER, SIM_ACCOUNT_ID) is False
    assert kite_accounts.get(SIM_UID, SIM_ACCOUNT_ID).access_token_enc == ""


@pytest.mark.parametrize("mutate", [
    lambda a: setattr(a, "is_paper", False),
    lambda a: setattr(a, "api_key", "KEY"),
    lambda a: setattr(a, "api_secret_enc", "enc"),
    lambda a: setattr(a, "access_token_enc", "enc"),
    lambda a: setattr(a, "refresh_token_enc", "enc"),
    lambda a: setattr(a, "user_id", OWNER),             # sim id under a real uid
])
def test_persisting_a_malformed_sim_account_is_refused(mutate):
    sim = kite_accounts.ensure_sim_account(OWNER)
    mutate(sim)
    with pytest.raises(SimAccountProtected):
        kite_accounts._persist(sim)


def test_a_real_account_cannot_live_under_a_sim_uid():
    live = live_account()
    live.user_id = SIM_UID
    with pytest.raises(SimAccountProtected):
        kite_accounts._persist(live)


def test_malformed_sim_rows_are_ignored_on_load_and_healed_by_ensure():
    kite_accounts.ensure_sim_account(OWNER)
    with db._conn() as c:                                   # tampered: the sim account marked live + keyed
        c.execute("UPDATE kite_accounts SET is_paper=0, api_key='K', api_secret_enc='S' WHERE id=?",
                  (SIM_ACCOUNT_ID,))
    assert [a.id for a in kite_accounts._load_from_db()] == []
    kite_accounts._accounts.clear()
    healed = kite_accounts.ensure_sim_account(OWNER)
    assert healed.is_paper and not healed.api_key and healed.connected
    assert [r["is_paper"] for r in _rows()] == [1]


# ── the client it builds ─────────────────────────────────────────────────────
async def test_build_client_returns_the_simulated_client_bound_to_the_owner(monkeypatch):
    built = install_fake_sim_client(monkeypatch)
    sim = kite_accounts.ensure_sim_account(OWNER)
    client = kite_accounts.build_client(sim)
    assert isinstance(client, FakeSimClient) and client.owner == OWNER
    assert client._is_paper is True
    assert (client._account_id, client._owner_user_id) == (SIM_ACCOUNT_ID, SIM_UID)
    assert kite_accounts.client_is_current(client, user_id=SIM_UID)
    assert built == [client]


async def test_acquire_client_caches_the_sim_client_per_account(monkeypatch):
    built = install_fake_sim_client(monkeypatch)
    sim = kite_accounts.ensure_sim_account(OWNER)
    a = await kite_accounts.acquire_client(sim)
    assert await kite_accounts.acquire_client(sim) is a and len(built) == 1
    await kite_accounts.release_client(sim.id)


async def test_a_sim_client_that_does_not_declare_no_real_money_is_refused(monkeypatch):
    class Wrong(FakeSimClient):
        _is_paper = False

    install_fake_sim_client(monkeypatch, Wrong)
    sim = kite_accounts.ensure_sim_account(OWNER)
    with pytest.raises(RuntimeError, match="no_real_money"):
        kite_accounts.build_client(sim)


async def test_missing_sim_client_module_fails_closed(monkeypatch):
    monkeypatch.setitem(sys.modules, "app.services.simulation.sim_client", None)
    sim = kite_accounts.ensure_sim_account(OWNER)
    with pytest.raises(RuntimeError, match="simulation_client_unavailable"):
        kite_accounts.build_client(sim)


async def test_provider_resolves_the_owners_live_client_never_the_sim_one(monkeypatch):
    built = install_fake_sim_client(monkeypatch)
    live = live_account()
    sim = kite_accounts.ensure_sim_account(OWNER)
    provider = kite_accounts.build_client(sim).provider
    real = await provider()
    assert real._account_id == live.id and real._is_paper is False
    assert real is await kite_accounts.acquire_client(live)  # the same warm client the engines use
    await kite_accounts.release_client(live.id)


@pytest.mark.parametrize("setup", ["no_account", "logged_out", "expired"])
async def test_provider_fails_closed_without_a_real_session(monkeypatch, setup):
    install_fake_sim_client(monkeypatch)
    sim = kite_accounts.ensure_sim_account(OWNER)
    if setup == "logged_out":
        live_account(connected=False)
    elif setup == "expired":
        live = live_account()
        kite_accounts._accounts[live.id].token_expires_at_ms = 1
    provider = kite_accounts.build_client(sim).provider
    with pytest.raises(kite_accounts.SimNoLiveSession):
        await provider()


async def test_provider_never_hands_back_the_sim_account(monkeypatch):
    install_fake_sim_client(monkeypatch)
    sim = kite_accounts.ensure_sim_account(OWNER)
    provider = kite_accounts.owner_live_client_provider(SIM_UID)   # even asked for the sim uid
    with pytest.raises(kite_accounts.SimNoLiveSession):
        await provider()
    assert sim.is_sim


async def test_sim_session_is_always_healthy_and_never_calls_kite(monkeypatch):
    sim = kite_accounts.ensure_sim_account(OWNER)
    health = await kite_auth.ensure_session(SIM_UID, sim)
    assert health.connected and health.validated
