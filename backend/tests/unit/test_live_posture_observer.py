"""The static-egress observer: trust on first use, never adopt a change, grace on silence.

No test here reaches the network: the echo services are injected.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.core.deployment_identity import DeploymentIdentityStore
from app.services import live_posture as lp
from app.services.continuity_doctor import egress_status

IP = "203.0.113.7"
NEW = "198.51.100.9"
SERVICES = ("https://echo-a.test", "https://echo-b.test", "https://echo-c.test")


from tests.live_posture_real import restore_real_gates


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    restore_real_gates(monkeypatch)
    monkeypatch.setenv("STERLING_STATE_ROOT", str(tmp_path))
    monkeypatch.delenv("STERLING_STATIC_EGRESS_IP", raising=False)
    lp.reset_for_tests()
    yield
    lp.reset_for_tests()


class Echo:
    """Injectable echo services: a dict url -> text (or an exception to raise)."""

    def __init__(self, answers):
        self.answers = answers

    async def __call__(self, url):
        value = self.answers[url]
        if isinstance(value, BaseException):
            raise value
        if value == "hang":
            await asyncio.sleep(60)
        return value


def echo_all(ip):
    return Echo({u: ip for u in SERVICES})


class Alerts:
    def __init__(self):
        self.sent = []

    def __call__(self, **kw):
        self.sent.append(kw)

    def severities(self):
        return [a["severity"] for a in self.sent]


async def observe(echo, alerts, **kw):
    return await lp.observe_once(fetch=echo, services=SERVICES, alert=alerts, **kw)


# ---------------------------------------------------------------- discovery


class TestDiscovery:
    async def test_two_agreeing_services_are_enough(self):
        found = await lp.discover_outbound_ip(
            Echo({SERVICES[0]: IP, SERVICES[1]: IP, SERVICES[2]: NEW}), SERVICES)
        assert found.ip == IP

    async def test_a_single_answer_is_a_rumour(self):
        found = await lp.discover_outbound_ip(
            Echo({SERVICES[0]: IP, SERVICES[1]: RuntimeError("down"),
                  SERVICES[2]: "<html>nope</html>"}), SERVICES)
        assert found.ip is None
        assert "only 1 of 3" in found.reason

    async def test_services_that_disagree_give_no_address(self):
        found = await lp.discover_outbound_ip(
            Echo({SERVICES[0]: IP, SERVICES[1]: NEW, SERVICES[2]: RuntimeError("down")}),
            SERVICES)
        assert found.ip is None
        assert "disagree" in found.reason

    async def test_nonsense_and_local_addresses_are_not_answers(self):
        found = await lp.discover_outbound_ip(
            Echo({SERVICES[0]: "127.0.0.1", SERVICES[1]: "0.0.0.0", SERVICES[2]: "hello"}),
            SERVICES)
        assert found.ip is None
        assert "could be reached" in found.reason

    async def test_a_hanging_service_cannot_stall_the_others(self, monkeypatch):
        monkeypatch.setattr(lp, "ECHO_TIMEOUT_S", 0.05)
        started = asyncio.get_running_loop().time()
        found = await lp.discover_outbound_ip(
            Echo({SERVICES[0]: IP, SERVICES[1]: IP, SERVICES[2]: "hang"}), SERVICES)
        assert found.ip == IP
        assert asyncio.get_running_loop().time() - started < 5

    async def test_ipv6_is_accepted_as_a_literal(self):
        found = await lp.discover_outbound_ip(
            Echo({u: "2001:db8::7\n" for u in SERVICES}), SERVICES)
        assert found.ip == "2001:db8::7"


# ------------------------------------------------------- trust on first use


class TestTrustOnFirstUse:
    async def test_the_first_address_is_registered_and_the_owner_is_told_once(self):
        alerts = Alerts()
        outcome = await observe(echo_all(IP), alerts)

        assert outcome.outcome == 'awaiting_registration' and outcome.ip == IP
        store = DeploymentIdentityStore()
        assert store.registration() is None
        assert [o.outbound_ip for o in store.read()] == [IP]
        assert len(alerts.sent) == 1
        assert 'New entries remain blocked' in alerts.sent[0]['detail']
        assert egress_status().verified is False

    async def test_a_second_look_at_the_same_address_is_quiet(self):
        alerts = Alerts()
        DeploymentIdentityStore().register(IP, source="operator")
        await observe(echo_all(IP), alerts)
        later = datetime.now(timezone.utc) + timedelta(minutes=10)
        outcome = await observe(echo_all(IP), alerts, now=lambda: later)

        assert outcome.outcome == "confirmed"
        assert len(alerts.sent) == 0                       # registered address is quiet
        history = DeploymentIdentityStore().read()
        assert len(history) == 1                           # confirmed in place, not appended
        assert history[0].observed_at == later.isoformat()

    async def test_nothing_is_registered_when_the_address_cannot_be_read(self):
        alerts = Alerts()
        outcome = await observe(Echo({u: RuntimeError("down") for u in SERVICES}), alerts)

        assert outcome.outcome == "unreadable"
        assert DeploymentIdentityStore().registration() is None
        assert alerts.sent == []
        assert egress_status().verified is False

    async def test_an_address_the_operator_declared_is_not_overwritten_by_first_sight(
            self, monkeypatch):
        monkeypatch.setenv("STERLING_STATIC_EGRESS_IP", IP)
        alerts = Alerts()
        outcome = await observe(echo_all(NEW), alerts)

        assert outcome.outcome == "mismatch"
        assert DeploymentIdentityStore().registration() is None
        assert "STERLING_STATIC_EGRESS_IP" in alerts.sent[0]["detail"]


# ------------------------------------------------------ a changed address


class TestAChangedAddress:
    async def _registered(self, alerts):
        DeploymentIdentityStore().register(IP, source="operator")
        await observe(echo_all(IP), alerts)
        alerts.sent.clear()

    async def test_a_different_address_is_recorded_never_adopted_and_refuses_entries(self):
        alerts = Alerts()
        await self._registered(alerts)

        outcome = await observe(echo_all(NEW), alerts)

        assert outcome.outcome == "mismatch" and outcome.registered_ip == IP
        assert DeploymentIdentityStore().registration()["registered_ip"] == IP
        status = egress_status()
        assert status.verified is False
        assert NEW in status.reason and IP in status.reason
        assert alerts.severities() == ["CRITICAL"]
        detail = alerts.sent[0]["detail"]
        assert IP in detail and NEW in detail
        assert "Kite will refuse orders until the Zerodha whitelist" in detail
        assert f"sterlingctl egress accept {NEW}" in detail

    async def test_the_alert_repeats_with_the_same_identity_so_the_outbox_deduplicates_it(self):
        alerts = Alerts()
        await self._registered(alerts)
        await observe(echo_all(NEW), alerts)
        await observe(echo_all(NEW), alerts)

        assert len({a["key"] for a in alerts.sent}) == 1

    async def test_going_back_to_the_registered_address_restores_entries_and_says_so(self):
        alerts = Alerts()
        await self._registered(alerts)
        await observe(echo_all(NEW), alerts)
        alerts.sent.clear()

        outcome = await observe(echo_all(IP), alerts)

        assert outcome.outcome == "confirmed"
        assert egress_status().verified is True
        assert alerts.severities() == ["INFO"]

    async def test_an_unreadable_registration_file_is_never_registered_over(self):
        alerts = Alerts()
        store = DeploymentIdentityStore()
        store.registration_path.parent.mkdir(parents=True, exist_ok=True)
        store.registration_path.write_text("{garbled")

        outcome = await observe(echo_all(NEW), alerts)

        assert outcome.outcome == "registration_unreadable"
        assert store.registration_path.read_text() == "{garbled"
        assert alerts.severities() == ["CRITICAL"]
        assert egress_status().verified is False


# ------------------------------------------------------------ grace period


class TestGraceOnSilence:
    @pytest.fixture(autouse=True)
    def registered_address(self):
        DeploymentIdentityStore().register(IP, source='operator')

    async def test_the_last_good_reading_stays_valid_inside_the_grace_period(self):
        alerts = Alerts()
        lp._STATE.running = True                    # the observer is what judges freshness
        t0 = datetime.now(timezone.utc)
        await observe(echo_all(IP), alerts, now=lambda: t0)
        alerts.sent.clear()

        down = Echo({u: RuntimeError("down") for u in SERVICES})
        t1 = t0 + timedelta(minutes=20)
        outcome = await observe(down, alerts, now=lambda: t1)

        assert outcome.outcome == "unreadable"
        assert egress_status(now=lambda: t1).verified is True
        assert alerts.sent == []

    async def test_after_the_grace_period_entries_are_refused_with_an_explicit_reason(self):
        alerts = Alerts()
        lp._STATE.running = True
        t0 = datetime.now(timezone.utc)
        await observe(echo_all(IP), alerts, now=lambda: t0)
        alerts.sent.clear()

        down = Echo({u: RuntimeError("down") for u in SERVICES})
        t1 = t0 + lp.OBSERVATION_GRACE + timedelta(minutes=5)
        await observe(down, alerts, now=lambda: t1)

        status = egress_status(now=lambda: t1)
        assert status.verified is False
        assert "35 min ago" in status.reason and "limit 30 min" in status.reason
        assert "none of the 3 echo services" in status.reason
        assert alerts.severities() == ["CRITICAL"]
        # Still down on the next try: no second alert for the same outage.
        await observe(down, alerts, now=lambda: t1 + timedelta(minutes=1))
        assert alerts.severities() == ["CRITICAL"]

    async def test_a_healthy_observer_is_never_refused_for_staleness(self):
        alerts = Alerts()
        lp._STATE.running = True
        t0 = datetime.now(timezone.utc)
        await observe(echo_all(IP), alerts, now=lambda: t0)
        # Just before the next scheduled cycle, plus a slow echo round.
        just_before_next = t0 + timedelta(seconds=lp.OBSERVER_INTERVAL_S + 30)
        assert egress_status(now=lambda: just_before_next).verified is True

    def test_the_freshness_window_matches_the_cadence(self):
        interval = timedelta(seconds=lp.OBSERVER_INTERVAL_S)
        assert lp.OBSERVATION_GRACE >= 2 * interval
        assert lp.OBSERVER_RETRY_S < lp.OBSERVER_INTERVAL_S
        # Without the observer the hand-recorded window applies, which is far wider.
        from app.core.deployment_identity import MAX_OBSERVATION_AGE
        assert lp.OBSERVATION_GRACE < MAX_OBSERVATION_AGE

    async def test_without_an_observer_the_hand_recorded_window_still_applies(self):
        alerts = Alerts()
        t0 = datetime.now(timezone.utc)
        await observe(echo_all(IP), alerts, now=lambda: t0)

        assert lp.observer_active() is False
        assert egress_status(now=lambda: t0 + timedelta(hours=5)).verified is True


# ------------------------------------------------------------- accepting


class TestAcceptingANewAddress:
    async def _changed(self):
        DeploymentIdentityStore().register(IP, source="operator")
        alerts = Alerts()
        await observe(echo_all(IP), alerts)
        await observe(echo_all(NEW), alerts)

    async def test_an_accepted_address_becomes_the_registered_one(self):
        await self._changed()
        assert egress_status().verified is False

        result = await lp.accept_egress_ip(NEW, by="owner", fetch=echo_all(NEW), services=SERVICES)

        assert result["verified"] is True
        row = DeploymentIdentityStore().registration()
        assert row["registered_ip"] == NEW and row["source"] == "operator" and row["by"] == "owner"
        assert [p["registered_ip"] for p in row["previous"]] == [IP]
        assert egress_status().verified is True

    async def test_an_address_the_host_is_not_using_is_refused(self):
        await self._changed()
        with pytest.raises(ValueError, match="answers from"):
            await lp.accept_egress_ip("192.0.2.55", fetch=echo_all(NEW), services=SERVICES)
        assert DeploymentIdentityStore().registration()["registered_ip"] == IP

    async def test_nothing_is_accepted_while_the_address_cannot_be_confirmed(self):
        await self._changed()
        down = Echo({u: RuntimeError("down") for u in SERVICES})
        with pytest.raises(ValueError, match="cannot confirm"):
            await lp.accept_egress_ip(NEW, fetch=down, services=SERVICES)

    async def test_the_environment_pin_must_be_changed_in_the_environment(self, monkeypatch):
        monkeypatch.setenv("STERLING_STATIC_EGRESS_IP", IP)
        with pytest.raises(ValueError, match="Change that line"):
            await lp.accept_egress_ip(NEW, fetch=echo_all(NEW), services=SERVICES)

    async def test_garbage_is_not_an_address(self):
        with pytest.raises(ValueError, match="valid IP"):
            await lp.accept_egress_ip("my-router", fetch=echo_all(NEW), services=SERVICES)


# ------------------------------------------------------------------ loop


class TestTheLoop:
    async def test_the_loop_observes_at_boot_and_marks_the_observer_active(self):
        DeploymentIdentityStore().register(IP, source="operator")
        alerts = Alerts()
        cycles = []

        async def after():
            cycles.append(1)

        task = asyncio.create_task(lp.observer_loop(
            interval=0.01, retry=0.01, after_cycle=after,
            fetch=echo_all(IP), services=SERVICES, alert=alerts))
        for _ in range(200):
            await asyncio.sleep(0.02)
            if len(cycles) >= 2:
                break
        assert lp.observer_active() is True
        assert egress_status().verified is True
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert lp.observer_active() is False
        assert len(cycles) >= 2

    async def test_one_failed_cycle_does_not_end_the_observer(self):
        calls = {"n": 0}

        async def flaky(url):
            calls["n"] += 1
            if calls["n"] <= 3:
                raise RuntimeError("boom")
            return IP

        task = asyncio.create_task(lp.observer_loop(
            interval=0.01, retry=0.01, fetch=flaky, services=SERVICES, alert=Alerts()))
        for _ in range(300):
            await asyncio.sleep(0.02)
            if lp.observer_status()["last_outcome"] in {"awaiting_registration", "confirmed"}:
                break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert lp.observer_status()["last_ip"] == IP

    async def test_the_suite_never_starts_the_real_observer(self, monkeypatch):
        assert lp.start_observer() is None                 # PYTEST_CURRENT_TEST is set
        monkeypatch.setenv("STERLING_POSTURE_IN_TESTS", "1")
        monkeypatch.setenv("STERLING_EGRESS_OBSERVER", "0")
        assert lp.start_observer() is None                 # and an explicit off wins
