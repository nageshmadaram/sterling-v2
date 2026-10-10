"""Automatic broker-account binding: the happy path, and every refusal.

The Kite side is injected: the real read-only verification function runs against a
fake client, so the evidence files and the ACTIVE/VERIFIED transitions are real.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from app.core.broker_account_binding import BindingStatus
from app.services import live_posture as lp
from app.services.account_binding_service import (
    AccountBindingStore,
    binding_health,
    verification_is_current,
)
from app.services.execution_service import (
    CanonicalExecutionService,
    ExecutionRequest,
    ExposureEffect,
)

KITE_USER = "AB1234"


class FakeClient:
    _is_paper = False

    def __init__(self, account_id, profile_user=KITE_USER, fail=None):
        self._account_id = account_id
        self._kite_user_id = KITE_USER
        self.profile_user = profile_user
        self.fail = fail
        self.reads = 0

    async def get_profile(self):
        self.reads += 1
        if self.fail:
            raise RuntimeError(self.fail)
        return {"user_id": self.profile_user}

    async def get_positions_raw(self):
        return {"net": []}

    async def get_orders(self):
        return []

    async def get_gtts(self):
        return []


def live_account(account_id="KITE-APP-1", *, kite_user=KITE_USER, connected=True, live=True,
                 login_ms=None, sim=False, paper=False):
    return SimpleNamespace(
        id=account_id, user_id="owner", kite_user_id=kite_user, user_name="Family Owner",
        is_paper=paper, is_sim=sim, connected=connected, token_is_live=live,
        last_login_at_ms=login_ms if login_ms is not None else int(time.time() * 1000),
    )


class Alerts:
    def __init__(self):
        self.sent = []

    def __call__(self, **kw):
        self.sent.append(kw)

    def severities(self):
        return [a["severity"] for a in self.sent]


class World:
    """The injected Kite accounts: what is stored, and which client the broker hands out."""

    def __init__(self, monkeypatch, accounts):
        from app.services.exchanges.kite import accounts as real

        self.accounts = list(accounts)
        self.client = None
        monkeypatch.setattr(real, "bootstrap", lambda: None)
        monkeypatch.setattr(real, "live_accounts", lambda: list(self.accounts))
        monkeypatch.setattr(real, "get_active", self._active)

        async def acquire(account):
            return self.client or FakeClient(account.id)

        monkeypatch.setattr(real, "acquire_client", acquire)

    def _active(self, uid):
        return next((a for a in self.accounts if a.user_id == uid and not a.is_sim), None)


from tests.live_posture_real import restore_real_gates


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    restore_real_gates(monkeypatch)
    monkeypatch.setenv("STERLING_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("STERLING_DEPLOYMENT_ID", "prod-1")
    monkeypatch.delenv("STERLING_AUTO_BIND_ACCOUNT", raising=False)
    lp.reset_for_tests()
    yield
    lp.reset_for_tests()


async def bind(alerts=None, **kw):
    return await lp.ensure_account_binding(alert=alerts or Alerts(), **kw)


# --------------------------------------------------------------- happy path


class TestAutoBind:
    async def test_one_logged_in_live_account_is_declared_verified_and_activated(self, monkeypatch):
        World(monkeypatch, [live_account()])
        alerts = Alerts()

        outcome = await bind(alerts)

        assert outcome.action == "bound"
        binding = AccountBindingStore().active()
        assert binding.status is BindingStatus.ACTIVE
        assert binding.client_id == KITE_USER
        assert binding.account_scope == "live"
        assert binding.deployment_id == "prod-1"
        assert binding.live_ready is True
        assert binding.verification_account_id == "KITE-APP-1"
        assert verification_is_current(binding) is True
        health = binding_health()
        assert health["account_binding_ready"] and health["account_binding_live_ready"]
        assert alerts.severities() == ["INFO"]
        assert "never moved" in alerts.sent[0]["detail"]

    async def test_the_auto_declared_binding_satisfies_the_real_order_time_gate(self, monkeypatch):
        world = World(monkeypatch, [live_account()])
        await bind()
        request = ExecutionRequest(
            uid="owner", account_id="KITE-APP-1", strategy_id="sterling-kite",
            generation_id="g", signal_id="s", exchange="NFO", symbol="NIFTY26OCT25000CE",
            side="BUY", quantity=75, exposure_effect=ExposureEffect.INCREASE_EXPOSURE,
            order_type="market_order", capital_required=7500.0, available_capital=100000.0,
            payload={"product": "NRML"},
        )

        ok, reason = await CanonicalExecutionService().verify_production_account_binding(
            world.client or FakeClient("KITE-APP-1"), request)

        assert ok is True, reason

    async def test_running_again_changes_nothing_and_does_not_ask_the_broker(self, monkeypatch):
        world = World(monkeypatch, [live_account(login_ms=1_000)])
        await bind()
        world.client = FakeClient("KITE-APP-1")

        again = await bind()

        assert again.action == "current"
        assert world.client.reads == 0
        assert len(AccountBindingStore().all()) == 1

    async def test_a_new_login_re_verifies_without_touching_the_binding_identity(
            self, monkeypatch):
        world = World(monkeypatch, [live_account(login_ms=1_000)])
        await bind()
        first = AccountBindingStore().active()

        world.accounts[0].last_login_at_ms = int(time.time() * 1000) + 5_000   # logged in again
        world.client = FakeClient("KITE-APP-1")
        again = await bind()

        assert again.action == "reverified"
        second = AccountBindingStore().active()
        assert second.binding_id == first.binding_id
        assert second.status is BindingStatus.ACTIVE
        assert second.verified_at >= first.verified_at
        assert world.client.reads == 1

    async def test_it_can_be_switched_off(self, monkeypatch):
        World(monkeypatch, [live_account()])
        monkeypatch.setenv("STERLING_AUTO_BIND_ACCOUNT", "0")

        outcome = await bind()

        assert outcome.action == "disabled"
        assert AccountBindingStore().all() == []

    async def test_a_binding_left_declared_by_a_failed_attempt_is_reused_not_duplicated(
            self, monkeypatch):
        world = World(monkeypatch, [live_account()])
        world.client = FakeClient("KITE-APP-1", fail="Kite is down")
        first = await bind()
        assert first.action == "error"
        lp._BIND.next_attempt = 0.0
        world.client = FakeClient("KITE-APP-1")

        second = await bind()

        assert second.action == "bound"
        assert len(AccountBindingStore().all()) == 1


# ----------------------------------------------------------------- waiting


class TestWaitingIsNotRefusing:
    @pytest.mark.parametrize("account", [
        live_account(kite_user=""),                    # never logged in: no bound Kite user
        live_account(connected=False),                 # no token
        live_account(live=False),                      # token past its window
    ])
    async def test_no_session_no_binding_yet(self, monkeypatch, account):
        World(monkeypatch, [account])

        outcome = await bind()

        assert outcome.action == "waiting"
        assert AccountBindingStore().all() == []

    async def test_no_account_at_all(self, monkeypatch):
        World(monkeypatch, [])
        assert (await bind()).action == "waiting"

    async def test_the_simulation_tenant_is_never_a_candidate(self, monkeypatch):
        World(monkeypatch, [live_account("SIM-1", kite_user="SIM", sim=True),
                            live_account("SIM-2", kite_user="SIM", paper=True)])

        outcome = await bind()

        assert outcome.action == "waiting"
        assert AccountBindingStore().all() == []

    async def test_a_real_account_next_to_the_simulation_tenant_still_binds(self, monkeypatch):
        World(monkeypatch, [live_account("SIM-1", kite_user="SIM", sim=True), live_account()])
        assert (await bind()).action == "bound"
        assert AccountBindingStore().active().client_id == KITE_USER


# ----------------------------------------------------------------- refusals


class TestRefusals:
    async def test_two_live_accounts_is_ambiguous_and_nothing_is_guessed(self, monkeypatch):
        World(monkeypatch, [live_account("KITE-A"), live_account("KITE-B", kite_user="ZZ9999")])
        alerts = Alerts()

        outcome = await bind(alerts)

        assert outcome.action == "refused"
        assert AccountBindingStore().all() == []
        assert "exactly one" in alerts.sent[0]["detail"]

    async def test_a_changed_kite_user_under_an_active_binding_is_refused_never_rebound(
            self, monkeypatch):
        world = World(monkeypatch, [live_account()])
        await bind()
        before = AccountBindingStore().active()

        world.accounts[0].kite_user_id = "ZZ9999"          # somebody else logged in
        world.accounts[0].last_login_at_ms = int(time.time() * 1000) + 5_000
        alerts = Alerts()
        outcome = await bind(alerts)

        assert outcome.action == "refused"
        after = AccountBindingStore().active()
        assert after.binding_id == before.binding_id and after.client_id == KITE_USER
        assert after.verified_at == before.verified_at                # not even re-verified
        assert len(AccountBindingStore().all()) == 1                  # nothing new declared
        assert alerts.severities() == ["CRITICAL"]
        assert KITE_USER in alerts.sent[0]["detail"] and "ZZ9999" in alerts.sent[0]["detail"]
        assert "will not move a binding on its own" in alerts.sent[0]["detail"]

    async def test_a_login_that_is_the_wrong_zerodha_account_at_the_broker_is_refused(
            self, monkeypatch):
        world = World(monkeypatch, [live_account()])
        await bind()
        world.accounts[0].last_login_at_ms = int(time.time() * 1000) + 5_000
        world.client = FakeClient("KITE-APP-1", profile_user="ZZ9999")   # Kite says: not AB1234
        alerts = Alerts()

        outcome = await bind(alerts)

        assert outcome.action == "refused"
        assert alerts.severities() == ["CRITICAL"]
        assert AccountBindingStore().active().client_id == KITE_USER

    async def test_a_first_binding_the_broker_contradicts_never_becomes_active(self, monkeypatch):
        world = World(monkeypatch, [live_account()])
        world.client = FakeClient("KITE-APP-1", profile_user="ZZ9999")

        outcome = await bind()

        assert outcome.action == "refused"
        assert AccountBindingStore().active() is None
        assert [b.status for b in AccountBindingStore().all()] == [BindingStatus.DECLARED]

    async def test_a_binding_an_operator_deactivated_is_not_quietly_revived(self, monkeypatch):
        World(monkeypatch, [live_account()])
        await bind()
        store = AccountBindingStore()
        store.deactivate(store.active().binding_id)
        alerts = Alerts()

        outcome = await bind(alerts)

        assert outcome.action == "refused"
        assert store.active() is None
        assert "deactivated on purpose" in alerts.sent[0]["detail"]

    async def test_a_changed_deployment_id_is_a_real_mismatch_not_something_to_paper_over(
            self, monkeypatch):
        world = World(monkeypatch, [live_account()])
        await bind()
        monkeypatch.setenv("STERLING_DEPLOYMENT_ID", "some-other-host")
        world.accounts[0].last_login_at_ms = int(time.time() * 1000) + 5_000
        alerts = Alerts()

        outcome = await bind(alerts)

        assert outcome.action == "refused"
        assert AccountBindingStore().active().deployment_id == "prod-1"
        assert alerts.severities() == ["CRITICAL"]

    async def test_a_broker_outage_is_retried_later_and_is_not_an_alarm(self, monkeypatch):
        world = World(monkeypatch, [live_account()])
        world.client = FakeClient("KITE-APP-1", fail="connection reset")
        alerts = Alerts()

        outcome = await bind(alerts)

        assert outcome.action == "error"
        assert alerts.sent == []
        assert AccountBindingStore().active() is None
        # And it does not hammer Kite: the next minute-tick waits out the back-off.
        world.client = FakeClient("KITE-APP-1")
        assert (await bind(alerts)).action == "waiting"
        assert world.client.reads == 0

    async def test_the_third_consecutive_outage_is_reported(self, monkeypatch):
        world = World(monkeypatch, [live_account()])
        world.client = FakeClient("KITE-APP-1", fail="connection reset")
        alerts = Alerts()
        for _ in range(3):
            lp._BIND.next_attempt = 0.0
            await bind(alerts)

        assert alerts.severities() == ["WARNING"]

    async def test_an_unreadable_binding_store_is_a_refusal(self, monkeypatch, tmp_path):
        World(monkeypatch, [live_account()])
        directory = tmp_path / "data/manifests/account_bindings"
        directory.mkdir(parents=True)
        (directory / "broken.json").write_text("{nope")
        alerts = Alerts()

        outcome = await bind(alerts)

        assert outcome.action == "refused"
        assert alerts.severities() == ["CRITICAL"]


# ------------------------------------------------------------ deployment id


class TestDeploymentId:
    def test_the_operators_value_wins(self, monkeypatch, tmp_path):
        assert lp.ensure_deployment_id() == "prod-1"
        assert not (tmp_path / lp.DEPLOYMENT_ID_FILE).exists()

    def test_a_generated_id_is_persisted_exported_and_stable(self, monkeypatch, tmp_path):
        monkeypatch.delenv("STERLING_DEPLOYMENT_ID")

        first = lp.ensure_deployment_id()
        import os
        assert os.environ["STERLING_DEPLOYMENT_ID"] == first
        assert (tmp_path / lp.DEPLOYMENT_ID_FILE).read_text().strip() == first

        monkeypatch.delenv("STERLING_DEPLOYMENT_ID")          # a restart
        assert lp.ensure_deployment_id() == first

    def test_an_unusable_file_is_never_silently_replaced(self, monkeypatch, tmp_path):
        monkeypatch.delenv("STERLING_DEPLOYMENT_ID")
        path = tmp_path / lp.DEPLOYMENT_ID_FILE
        path.parent.mkdir(parents=True)
        path.write_text("   \n")
        with pytest.raises(RuntimeError, match="usable deployment id"):
            lp.ensure_deployment_id()
        assert path.read_text() == "   \n"
