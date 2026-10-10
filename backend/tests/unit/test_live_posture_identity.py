"""The identity store's observer support: last-confirmed, registration, fail-closed reads."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.deployment_identity import (
    DeploymentIdentityStore,
    RegistrationUnreadable,
    resolve_registered,
    verify_deployment_identity,
)

IP = "203.0.113.7"
OTHER = "198.51.100.9"


@pytest.fixture()
def store(tmp_path):
    return DeploymentIdentityStore(tmp_path / "deployment_identity.json")


@pytest.fixture(autouse=True)
def _no_declared_ip(monkeypatch):
    monkeypatch.delenv("STERLING_STATIC_EGRESS_IP", raising=False)


class TestObserve:
    def test_an_unchanged_address_moves_last_confirmed_without_growing_the_file(self, store):
        t0 = datetime.now(timezone.utc) - timedelta(minutes=20)
        store.observe(host_id="nuc", outbound_ip=IP, observed_at=t0.isoformat())
        t1 = datetime.now(timezone.utc)
        store.observe(host_id="nuc", outbound_ip=IP, observed_at=t1.isoformat())

        history = store.read()
        assert len(history) == 1
        assert history[0].observed_at == t1.isoformat()

    def test_a_changed_address_is_appended_so_the_move_stays_visible(self, store):
        store.observe(host_id="nuc", outbound_ip=IP)
        store.observe(host_id="nuc", outbound_ip=OTHER)
        store.observe(host_id="nuc", outbound_ip=OTHER)

        assert [o.outbound_ip for o in store.read()] == [IP, OTHER]

    def test_record_is_still_append_only(self, store):
        store.record(host_id="nuc", outbound_ip=IP)
        store.record(host_id="nuc", outbound_ip=IP)
        assert len(store.read()) == 2

    def test_a_bad_address_is_refused(self, store):
        with pytest.raises(ValueError):
            store.observe(host_id="nuc", outbound_ip="not-an-ip")


class TestRegistration:
    def test_nothing_registered_reads_as_none(self, store):
        assert store.registration() is None
        assert resolve_registered(None, store) == ("", "")

    def test_registering_is_remembered_with_the_previous_one(self, store):
        store.register(IP, source="first-observation")
        store.register(OTHER, source="operator", by="owner")

        row = store.registration()
        assert row["registered_ip"] == OTHER
        assert row["source"] == "operator"
        assert [p["registered_ip"] for p in row["previous"]] == [IP]

    def test_the_environment_wins_over_the_file(self, store, monkeypatch):
        store.register(IP, source="first-observation")
        monkeypatch.setenv("STERLING_STATIC_EGRESS_IP", OTHER)
        assert resolve_registered(None, store) == (OTHER, "environment")

    def test_the_file_answers_when_the_environment_is_silent(self, store):
        store.register(IP, source="first-observation")
        assert resolve_registered(None, store) == (IP, "registration")

    def test_an_invalid_address_cannot_be_registered(self, store):
        with pytest.raises(ValueError):
            store.register("operator-label", source="operator")
        assert store.registration() is None

    @pytest.mark.parametrize("body", ["{not json", "[]", '{"registered_ip": "label"}'])
    def test_an_unreadable_registration_is_never_read_as_none(self, store, body):
        store.registration_path.parent.mkdir(parents=True, exist_ok=True)
        store.registration_path.write_text(body)

        with pytest.raises(RegistrationUnreadable):
            store.registration()
        verdict = verify_deployment_identity(store=store)
        assert verdict.verified is False
        assert "cannot be read" in verdict.reason


class TestVerdictFromTheRegistrationFile:
    def test_a_matching_fresh_observation_verifies_through_the_file(self, store):
        store.register(IP, source="first-observation")
        store.observe(host_id="nuc", outbound_ip=IP)

        verdict = verify_deployment_identity(store=store)
        assert verdict.verified is True
        assert verdict.source == "registration"

    def test_a_different_address_blocks_and_names_the_accept_command(self, store):
        store.register(IP, source="first-observation")
        store.observe(host_id="nuc", outbound_ip=OTHER)

        verdict = verify_deployment_identity(store=store)
        assert verdict.verified is False
        assert OTHER in verdict.reason and IP in verdict.reason
        assert f"sterlingctl egress accept {OTHER}" in verdict.reason

    def test_a_tight_window_makes_an_old_observation_unknown_with_minutes(self, store):
        store.register(IP, source="first-observation")
        old = datetime.now(timezone.utc) - timedelta(minutes=45)
        store.observe(host_id="nuc", outbound_ip=IP, observed_at=old.isoformat())

        verdict = verify_deployment_identity(store=store, max_age=timedelta(minutes=30))
        assert verdict.verified is None
        assert "45 min ago" in verdict.reason and "limit 30 min" in verdict.reason
        # The same observation is fine under the default hand-recorded window.
        assert verify_deployment_identity(store=store).verified is True
