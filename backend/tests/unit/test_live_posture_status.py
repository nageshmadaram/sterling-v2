"""The live status carries the posture facts; the boot says it plainly; the routes behave."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import live_posture as endpoint
from app.core.auth import UserContext, get_current_user, require_admin
from app.core.deployment_identity import DeploymentIdentityStore
from app.services import live_posture as lp
from app.services import live_runtime as lr

IP = "203.0.113.7"
NEW = "198.51.100.9"


from tests.live_posture_real import restore_real_gates


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    restore_real_gates(monkeypatch)
    from app.services import db
    from app.services.auth import user_store
    monkeypatch.setattr(db, '_DB_PATH', str(tmp_path/'posture.db'))
    assert db.init()
    monkeypatch.setattr(user_store, '_users', {})
    monkeypatch.setattr(user_store, '_loaded', True)
    user_store._init_table()
    user_store.add('operator', 'Synthetic-posture-passphrase-123', role='admin', user_id='default')
    monkeypatch.setenv("STERLING_STATE_ROOT", str(tmp_path))
    monkeypatch.delenv("STERLING_STATIC_EGRESS_IP", raising=False)
    monkeypatch.delenv("STERLING_DEPLOYMENT_ID", raising=False)
    lp.reset_for_tests()
    lr.reset_for_tests()
    yield
    lp.reset_for_tests()
    lr.reset_for_tests()


class TestPostureFacts:
    @pytest.mark.kite_features('default', 'owner')
    def test_a_fresh_machine_names_exactly_what_is_missing(self):
        posture = lp.posture_status(refresh=True)

        assert posture["ready"] is False
        assert posture["secrets"]["ok"] is True            # the suite pins strong keys
        assert posture["egress"]["registered"] is False
        assert posture["binding"]["active"] is False
        assert posture["missing"] == ["static IP not registered yet", "account binding"]
        assert posture["setup_hint"] == ""

    def test_weak_keys_are_reported_with_the_one_line_that_fixes_them(self, monkeypatch):
        monkeypatch.delenv("STERLING_SECRET_KEY")

        posture = lp.posture_status(refresh=True)

        assert posture["secrets"]["ok"] is False
        assert "STERLING_SECRET_KEY" in posture["secrets"]["problem"]
        assert "secure keys" in posture["missing"]
        assert posture["setup_hint"] == (
            "Live trading needs secure keys: run python scripts/sterling_live_setup.py")

    def test_a_registered_and_confirmed_address_reads_as_verified(self):
        store = DeploymentIdentityStore()
        store.register(IP, source="first-observation")
        store.observe(host_id="nuc", outbound_ip=IP)

        egress = lp.posture_status(refresh=True)["egress"]

        assert egress["registered"] and egress["verified"], egress
        assert egress["registered_ip"] == IP and egress["registration_source"] == "registration"
        assert egress["observed_ip"] == IP

    def test_a_changed_address_reads_as_registered_but_not_verified(self):
        store = DeploymentIdentityStore()
        store.register(IP, source="first-observation")
        store.observe(host_id="nuc", outbound_ip=NEW)

        posture = lp.posture_status(refresh=True)

        assert posture["egress"]["registered"] is True
        assert posture["egress"]["verified"] is False
        assert "static IP not verified" in posture["missing"]

    def test_no_secret_value_ever_appears_in_the_posture(self, monkeypatch):
        import json
        posture = lp.posture_status(refresh=True)
        blob = json.dumps(posture)
        import os
        for name in ("STERLING_SECRET_KEY", "STERLING_JWT_SECRET"):
            assert os.environ[name] not in blob

    def test_the_facts_are_cached_briefly(self, monkeypatch):
        first = lp.posture_status(refresh=True)
        monkeypatch.delenv("STERLING_SECRET_KEY")
        assert lp.posture_status() is first
        assert lp.posture_status(refresh=True)["secrets"]["ok"] is False


class TestLiveStatus:
    @pytest.mark.kite_features('default', 'owner')
    def test_the_live_status_carries_the_posture_and_a_clause_in_the_summary_line(self):
        status = lr.live_status()

        assert status["posture"]["missing"] == ["static IP not registered yet", "account binding"]
        assert status["summary"].endswith(
            "· Posture: Missing (static IP not registered yet, account binding)")

    def test_a_ready_posture_says_ok(self, monkeypatch):
        monkeypatch.setattr(lp, "posture_status", lambda **_k: {"ready": True, "missing": []})
        assert lr.live_status()["summary"].endswith("· Posture: OK")

    def test_a_posture_that_cannot_be_read_never_breaks_the_status(self, monkeypatch):
        def boom(**_k):
            raise RuntimeError("store unreadable")

        monkeypatch.setattr(lp, "posture_status", boom)

        status = lr.live_status()

        assert status["posture"]["ready"] is False
        assert "RuntimeError" in status["posture"]["error"]
        assert "Posture: Missing (posture unreadable)" in status["summary"]

    def test_a_status_without_posture_still_summarises(self):
        status = lr.live_status()
        status.pop("posture")
        assert "posture" not in lr.summary_line(status)

    def test_a_change_in_what_is_missing_is_a_new_status_event(self):
        a = lr.live_status()
        b = dict(a, posture={"ready": True, "missing": []})
        assert lr._status_key(a) != lr._status_key(b)


class TestBootAlert:
    def _announce(self, status):
        sent = []

        def enqueue(uid, **kw):
            sent.append(kw)
            return True

        lr.announce_boot(status, enqueue=enqueue)
        return sent

    def test_missing_secure_keys_make_the_boot_alert_critical_and_say_what_to_run(self, monkeypatch):
        monkeypatch.delenv("STERLING_SECRET_KEY")
        sent = self._announce(lr.live_status())

        assert sent and sent[0]["severity"] == "CRITICAL"
        assert "Live posture is incomplete: secure keys" in sent[0]["detail"]
        assert "python scripts/sterling_live_setup.py" in sent[0]["detail"]

    @pytest.mark.kite_features('default', 'owner')
    def test_a_not_yet_registered_address_is_listed_but_is_not_an_alarm(self):
        sent = self._announce(lr.live_status())

        assert sent and sent[0]["severity"] == "INFO"
        assert "static IP not registered yet" in sent[0]["detail"]


class TestBootLine:
    def test_the_line_is_printed_once_and_is_plain(self, capsys):
        line = lp.announce_secure_keys_needed(RuntimeError("Live security error: ..."))

        out = capsys.readouterr().out
        assert out == line + "\n"
        assert line == "Live trading needs secure keys: run python scripts/sterling_live_setup.py"

    def test_the_application_boot_calls_it_where_live_security_is_validated(self):
        from pathlib import Path

        source = (Path(__file__).resolve().parents[2] / "main.py").read_text()
        assert source.count("announce_secure_keys_needed") == 2        # production + live check
        assert "boot_deployment_id" in source
        assert "live_posture_router" in source

    def test_the_boot_never_writes_a_deployment_id_under_the_test_suite(self, tmp_path):
        assert lp.boot_deployment_id() == ""
        assert not (tmp_path / lp.DEPLOYMENT_ID_FILE).exists()


class TestRoutes:
    @pytest.fixture()
    def client(self):
        app = FastAPI()
        app.include_router(endpoint.router, prefix="/api/v1")
        user = UserContext(user_id="owner", username="owner", role="admin")
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[require_admin] = lambda: user
        return TestClient(app)

    def test_the_status_route_returns_the_posture(self, client):
        body = client.get("/api/v1/ops/live-posture").json()
        assert set(body) >= {"ready", "missing", "secrets", "egress", "binding"}

    def test_accepting_needs_the_confirmation_phrase(self, client):
        response = client.post("/api/v1/ops/live-posture/egress/accept",
                               json={"ip": NEW, "confirm": "yes"})
        assert response.status_code == 400

    def test_accepting_registers_the_address_the_host_really_uses(self, client, monkeypatch):
        DeploymentIdentityStore().register(IP, source="first-observation")

        async def discover(fetch=None, services=None):
            return lp.Discovery(NEW)

        monkeypatch.setattr(lp, "discover_outbound_ip", discover)

        response = client.post("/api/v1/ops/live-posture/egress/accept",
                               json={"ip": NEW, "confirm": endpoint.ACCEPT_CONFIRMATION})

        assert response.status_code == 200
        assert response.json()["registered_ip"] == NEW and response.json()["verified"] is True
        assert DeploymentIdentityStore().registration()["by"] == "owner"

    def test_an_address_the_host_does_not_use_is_a_conflict(self, client, monkeypatch):
        async def discover(fetch=None, services=None):
            return lp.Discovery(IP)

        monkeypatch.setattr(lp, "discover_outbound_ip", discover)

        response = client.post("/api/v1/ops/live-posture/egress/accept",
                               json={"ip": NEW, "confirm": endpoint.ACCEPT_CONFIRMATION})

        assert response.status_code == 409
        assert "nothing was changed" in response.json()["detail"]

    def test_both_routes_are_behind_authentication(self):
        app = FastAPI()
        app.include_router(endpoint.router, prefix="/api/v1")
        anonymous = TestClient(app)
        # The development identity seam would let an anonymous call through; assert the
        # dependencies are the shared ones so production auth applies.
        deps = {r.path: [d.call for d in r.dependant.dependencies] for r in endpoint.router.routes}
        assert get_current_user in deps["/ops/live-posture"]
        assert require_admin in deps["/ops/live-posture/egress/accept"]
        assert anonymous is not None
