"""Live runtime: boot wiring that makes live entries reachable, and safe.

Covers O1 (safe-mode initialisation + operator resume), O2 (startup recovery with
backoff, observe-only), O3 (broker exposure refresher), the status surface and the
boot alert. Every broker is a fake; nothing touches the network.
"""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from app.services import live_runtime as lr
from app.services.safe_mode import NORMAL, SAFE_MODE, SafeModeService, SafeModeTrigger


@pytest.fixture(autouse=True)
def _fresh_runtime():
    lr.reset_for_tests()
    yield
    lr.reset_for_tests()


@pytest.fixture()
def safe_file(tmp_path, monkeypatch):
    path = tmp_path / "data" / "safe_mode.json"
    monkeypatch.setenv("STERLING_SAFE_MODE_FILE", str(path))
    return path


@pytest.fixture()
def clean_control_plane():
    """Tests that write control-plane rows put the shared test state back."""
    from app.services import live_safety

    live_safety.reset_all_for_tests()
    yield
    live_safety.reset_all_for_tests()


def _account(account_id="KITE-A1", uid="u1", *, connected=True, live=True,
             expires=None, **extra):
    return SimpleNamespace(
        id=account_id, user_id=uid, is_paper=not live, connected=connected,
        token_expires_at=expires, token_is_live=True, kite_user_id="AB1234", **extra,
    )


class _Registry:
    """Stand-in for the kite accounts module."""

    def __init__(self, accounts, client=None):
        self._accounts = list(accounts)
        self.client = client or object()
        self.acquired = []

    def bootstrap(self):
        return None

    def all_accounts(self):
        return list(self._accounts)

    async def acquire_client(self, account):
        self.acquired.append(account.id)
        return self.client


class _Service:
    """Stand-in for CanonicalExecutionService."""

    def __init__(self, outcomes=None, raises=None):
        self.calls = []
        self.outcomes = list(outcomes or [])
        self.raises = list(raises or [])

    async def startup_recovery(self, uid, account_id, broker_client):
        self.calls.append((uid, account_id, broker_client))
        if self.raises:
            exc = self.raises.pop(0)
            if exc is not None:
                raise exc
        if self.outcomes:
            return self.outcomes.pop(0)
        return _clean_outcome()


def _clean_outcome():
    return {"status": "success", "recovery_state": "CLEAN", "unresolved_count": 0,
            "pending_projections_count": 0, "protection_pending": False}


def _dirty_outcome():
    return {"status": "success", "recovery_state": "RECOVERY_REQUIRED",
            "unresolved_count": 2, "pending_projections_count": 0,
            "protection_pending": False}


# ---------------------------------------------------------------------------
# O1: safe mode
# ---------------------------------------------------------------------------


class TestSafeModeBoot:
    def test_a_missing_state_is_initialised_normal_when_provably_clean(self, safe_file):
        result = lr.initialise_safe_mode_if_clean(
            check=lambda: lr.CleanCheck(clean=True)
        )

        assert result.action == "initialised"
        assert result.state == NORMAL
        assert safe_file.exists()
        assert SafeModeService(safe_file).read().state == NORMAL
        assert "initialised at boot" in SafeModeService(safe_file).read().reason

    def test_an_unclean_system_is_left_in_safe_mode_and_nothing_is_written(self, safe_file):
        result = lr.initialise_safe_mode_if_clean(
            check=lambda: lr.CleanCheck(
                clean=False, blockers=("2 unresolved journal intent(s) for u1",),
                triggers=(SafeModeTrigger.UNRESOLVED_ORDER,),
            )
        )

        assert result.action == "not_clean"
        assert result.trading_blocked is True
        assert "unresolved journal intent" in result.reason
        assert not safe_file.exists()
        # And the read path still refuses to assume NORMAL.
        assert SafeModeService(safe_file).read().active is True

    def test_an_existing_safe_mode_is_never_cleared_at_boot(self, safe_file):
        service = SafeModeService(safe_file)
        service.engage(trigger=SafeModeTrigger.OPERATOR, reason="paused by owner")

        result = lr.initialise_safe_mode_if_clean(
            service, check=lambda: lr.CleanCheck(clean=True)
        )

        assert result.action == "left_engaged"
        assert result.trading_blocked is True
        assert service.read().active is True
        assert service.read().reason == "paused by owner"

    def test_an_existing_normal_state_is_left_alone(self, safe_file):
        service = SafeModeService(safe_file)
        service.initialise(operator_ack=True, note="earlier boot")
        before = safe_file.read_text()

        result = lr.initialise_safe_mode_if_clean(
            service, check=lambda: pytest.fail("must not re-prove a written state")
        )

        assert result.action == "already_normal"
        assert safe_file.read_text() == before

    def test_a_crash_while_proving_cleanliness_stays_safe_mode(self, safe_file):
        def boom():
            raise RuntimeError("journal exploded")

        result = lr.initialise_safe_mode_if_clean(check=boom)

        assert result.action == "error"
        assert result.state == SAFE_MODE
        assert not safe_file.exists()

    def test_the_retry_initialises_once_the_system_becomes_clean(self, safe_file, monkeypatch):
        verdicts = iter([
            lr.CleanCheck(clean=False, blockers=("1 unresolved intent",)),
            lr.CleanCheck(clean=True),
        ])
        monkeypatch.setattr(lr, "provably_clean", lambda: next(verdicts))

        first = lr.initialise_safe_mode_if_clean()
        lr._STATE.safe_mode_boot = first
        assert first.action == "not_clean"

        lr._retry_safe_mode_init()

        assert lr._STATE.safe_mode_boot.action == "initialised"
        assert SafeModeService(safe_file).read().state == NORMAL

    def test_the_retry_never_touches_an_operator_decision(self, safe_file, monkeypatch):
        service = SafeModeService(safe_file)
        service.engage(trigger=SafeModeTrigger.OPERATOR, reason="hold")
        lr._STATE.safe_mode_boot = lr.initialise_safe_mode_if_clean(service)
        monkeypatch.setattr(lr, "provably_clean", lambda: pytest.fail("not for an existing file"))

        lr._retry_safe_mode_init()

        assert service.read().active is True


class TestProvablyClean:
    @pytest.fixture()
    def stores(self, monkeypatch):
        from app.services import (
            adaptive_edge_positions,
            db,
            exposure_snapshot,
            gamma_move_positions,
            intraday_positions,
        )
        from app.services.kite_engine import order_journal, positions

        monkeypatch.setattr(db, "is_available", lambda: True)
        monkeypatch.setattr(db, "get_execution_control", lambda **_k: {"initialized": 0})
        monkeypatch.setattr(exposure_snapshot, "_known_local_uids", lambda: ("u1",))
        monkeypatch.setattr(order_journal, "unresolved", lambda *_a, **_k: [])
        monkeypatch.setattr(order_journal, "pending_projection", lambda *_a, **_k: [])
        monkeypatch.setattr(positions, "open_positions", lambda _u: [])
        monkeypatch.setattr(positions, "unreadable_reason", lambda _u: "")
        monkeypatch.setattr(intraday_positions, "open_positions", lambda _u: [])
        monkeypatch.setattr(adaptive_edge_positions, "open_positions", lambda _u: [])
        monkeypatch.setattr(gamma_move_positions, "open_positions", lambda _u: [])
        return SimpleNamespace(journal=order_journal, positions=positions, db=db)

    def test_nothing_unresolved_is_clean(self, stores):
        verdict = lr.provably_clean()
        assert verdict.clean is True and verdict.blockers == ()

    def test_an_unresolved_journal_intent_blocks(self, stores, monkeypatch):
        monkeypatch.setattr(stores.journal, "unresolved", lambda *_a, **_k: [object()])
        verdict = lr.provably_clean()
        assert verdict.clean is False
        assert "unresolved journal intent" in verdict.blockers[0]
        assert SafeModeTrigger.UNRESOLVED_ORDER in verdict.triggers

    def test_a_pending_projection_blocks(self, stores, monkeypatch):
        monkeypatch.setattr(stores.journal, "pending_projection", lambda *_a, **_k: [object()])
        assert lr.provably_clean().clean is False

    def test_an_open_position_with_protection_pending_blocks(self, stores, monkeypatch):
        position = SimpleNamespace(symbol="NIFTY26OCT25000CE", protection_pending=True)
        monkeypatch.setattr(stores.positions, "open_positions", lambda _u: [position])
        verdict = lr.provably_clean()
        assert verdict.clean is False
        assert "NIFTY26OCT25000CE" in verdict.blockers[0]
        assert SafeModeTrigger.PROTECTION_MISSING in verdict.triggers

    def test_a_protected_open_position_does_not_block(self, stores, monkeypatch):
        position = SimpleNamespace(symbol="NIFTY26OCT25000CE", protection_pending=False)
        monkeypatch.setattr(stores.positions, "open_positions", lambda _u: [position])
        assert lr.provably_clean().clean is True

    def test_an_unreadable_control_plane_blocks(self, stores, monkeypatch):
        from app.services import db

        def unreadable(**_k):
            raise db.ControlPlaneUnavailableError("db gone")

        monkeypatch.setattr(stores.db, "get_execution_control", unreadable)
        verdict = lr.provably_clean()
        assert verdict.clean is False
        assert "control plane unreadable" in verdict.blockers[0]

    def test_an_unavailable_database_blocks(self, stores, monkeypatch):
        monkeypatch.setattr(stores.db, "is_available", lambda: False)
        assert lr.provably_clean().clean is False

    def test_an_unreadable_position_store_blocks(self, stores, monkeypatch):
        def broken(_uid):
            raise RuntimeError("registry corrupt")

        monkeypatch.setattr(stores.positions, "open_positions", broken)
        verdict = lr.provably_clean()
        assert verdict.clean is False
        assert "unreadable" in verdict.blockers[0]


class TestResumeSafeMode:
    def _engage(self, safe_file, trigger, reason="x"):
        service = SafeModeService(safe_file)
        service.engage(trigger=trigger, reason=reason)
        return service

    def _resume(self, service, **overrides):
        kwargs = dict(actor="owner", confirm=lr.RESUME_CONFIRMATION, reason="checked the book",
                      acknowledge_investigation=False, service=service)
        kwargs.update(overrides)
        return lr.resume_safe_mode(**kwargs)

    def test_it_requires_the_confirmation_phrase(self, safe_file):
        service = self._engage(safe_file, SafeModeTrigger.OPERATOR)
        with pytest.raises(lr.SafeModeResumeRefused) as excinfo:
            self._resume(service, confirm="yes")
        assert excinfo.value.status_code == 400
        assert service.read().active is True

    def test_it_requires_a_reason(self, safe_file):
        service = self._engage(safe_file, SafeModeTrigger.OPERATOR)
        with pytest.raises(lr.SafeModeResumeRefused) as excinfo:
            self._resume(service, reason=" ")
        assert excinfo.value.status_code == 400

    def test_an_operator_safe_mode_lifts_without_acknowledgement(self, safe_file):
        service = self._engage(safe_file, SafeModeTrigger.OPERATOR, "lunch break")

        result = self._resume(service)

        assert result["resumed"] is True
        assert result["resumed_by"] == "owner"
        assert result["resumed_at"]
        assert result["state"] == NORMAL
        assert service.read().state == NORMAL

    def test_who_and_when_are_recorded_in_the_state_and_the_audit_log(self, safe_file):
        service = self._engage(safe_file, SafeModeTrigger.OPERATOR)

        self._resume(service, actor="asha", reason="back from lunch")

        assert "asha" in service.read().reason
        assert "back from lunch" in service.read().reason
        audit = (safe_file.parent / "safe_mode_audit.jsonl").read_text().splitlines()
        event = json.loads(audit[-1])
        assert event["event"] == "SAFE_MODE_RESUMED"
        assert event["actor"] == "asha"
        assert event["reason"] == "back from lunch"
        assert event["at"]
        assert event["triggers"] == [SafeModeTrigger.OPERATOR]

    @pytest.mark.parametrize("trigger", [
        SafeModeTrigger.UNRESOLVED_ORDER,
        SafeModeTrigger.PROTECTION_MISSING,
        SafeModeTrigger.RECONCILIATION_FAILURE,
        SafeModeTrigger.POSITION_MISMATCH,
        SafeModeTrigger.EXECUTION_DISCREPANCY,
    ])
    def test_an_investigation_trigger_needs_the_acknowledgement_flag(self, safe_file, trigger):
        service = self._engage(safe_file, trigger, "found a mismatch")

        with pytest.raises(lr.SafeModeResumeRefused) as excinfo:
            self._resume(service)

        assert excinfo.value.status_code == 409
        assert trigger in str(excinfo.value)
        assert service.read().active is True
        audit = (safe_file.parent / "safe_mode_audit.jsonl").read_text().splitlines()
        assert json.loads(audit[-1])["event"] == "SAFE_MODE_RESUME_REFUSED"

    def test_an_investigation_trigger_lifts_with_the_acknowledgement(self, safe_file):
        service = self._engage(safe_file, SafeModeTrigger.PROTECTION_MISSING)

        result = self._resume(service, acknowledge_investigation=True)

        assert result["resumed"] is True
        assert service.read().state == NORMAL

    def test_a_missing_state_file_needs_the_acknowledgement_then_initialises(self, safe_file):
        service = SafeModeService(safe_file)
        assert not safe_file.exists()

        with pytest.raises(lr.SafeModeResumeRefused):
            self._resume(service)
        assert not safe_file.exists()

        result = self._resume(service, acknowledge_investigation=True)

        assert result["resumed"] is True
        assert service.read().state == NORMAL

    def test_resuming_a_normal_system_is_a_recorded_noop(self, safe_file):
        service = SafeModeService(safe_file)
        service.initialise(operator_ack=True)

        result = self._resume(service)

        assert result["resumed"] is False and result["already_normal"] is True

    def test_status_reports_what_lifting_would_require(self, safe_file, monkeypatch):
        monkeypatch.setattr(lr, "provably_clean", lambda: lr.CleanCheck(clean=True))
        service = self._engage(safe_file, SafeModeTrigger.UNRESOLVED_ORDER, "stuck order")

        status = lr.safe_mode_status(service)

        assert status["active"] is True
        assert status["requires_acknowledgement"] is True
        assert status["blocking_triggers"] == [SafeModeTrigger.UNRESOLVED_ORDER]
        assert status["confirmation_phrase"] == lr.RESUME_CONFIRMATION
        assert status["clean_check"] == {"clean": True, "blockers": []}


# ---------------------------------------------------------------------------
# the HTTP surface
# ---------------------------------------------------------------------------


class TestOpsApi:
    @pytest.fixture()
    def client(self, safe_file, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api.v1.endpoints.ops_live import router

        monkeypatch.setattr(lr, "provably_clean", lambda: lr.CleanCheck(clean=True))
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        return TestClient(app)

    def _body(self, **overrides):
        body = {"confirm": lr.RESUME_CONFIRMATION, "reason": "owner checked",
                "acknowledge_investigation": False}
        body.update(overrides)
        return body

    def test_safe_mode_status_endpoint(self, client, safe_file):
        SafeModeService(safe_file).engage(trigger=SafeModeTrigger.OPERATOR, reason="hold")

        response = client.get("/api/v1/ops/safe-mode")

        assert response.status_code == 200
        assert response.json()["state"] == SAFE_MODE
        assert response.json()["confirmation_phrase"] == lr.RESUME_CONFIRMATION

    def test_resume_lifts_an_operator_safe_mode(self, client, safe_file):
        SafeModeService(safe_file).engage(trigger=SafeModeTrigger.OPERATOR, reason="hold")

        response = client.post("/api/v1/ops/safe-mode/resume", json=self._body())

        assert response.status_code == 200, response.text
        assert response.json()["resumed"] is True
        assert response.json()["resumed_by"]
        assert SafeModeService(safe_file).read().state == NORMAL

    def test_resume_refuses_without_the_confirmation_phrase(self, client, safe_file):
        SafeModeService(safe_file).engage(trigger=SafeModeTrigger.OPERATOR, reason="hold")

        response = client.post("/api/v1/ops/safe-mode/resume", json=self._body(confirm="ok"))

        assert response.status_code == 400
        assert SafeModeService(safe_file).read().active is True

    def test_resume_refuses_an_investigation_trigger_without_acknowledgement(
        self, client, safe_file
    ):
        SafeModeService(safe_file).engage(
            trigger=SafeModeTrigger.RECONCILIATION_FAILURE, reason="mismatch"
        )

        refused = client.post("/api/v1/ops/safe-mode/resume", json=self._body())
        assert refused.status_code == 409
        assert "RECONCILIATION_FAILURE" in refused.json()["detail"]

        accepted = client.post(
            "/api/v1/ops/safe-mode/resume", json=self._body(acknowledge_investigation=True)
        )
        assert accepted.status_code == 200

    def test_resume_is_not_available_to_a_non_admin(self, client, safe_file):
        SafeModeService(safe_file).engage(trigger=SafeModeTrigger.OPERATOR, reason="hold")

        response = client.post(
            "/api/v1/ops/safe-mode/resume", json=self._body(),
            headers={"X-User-Id": "some-family-member"},
        )

        assert response.status_code == 403
        assert SafeModeService(safe_file).read().active is True

    def test_resume_rejects_a_missing_body(self, client):
        assert client.post("/api/v1/ops/safe-mode/resume", json={}).status_code == 422

    def test_live_status_endpoint_is_authenticated_and_has_the_headline_fields(
        self, client, monkeypatch
    ):
        monkeypatch.setattr(lr, "_live_accounts", lambda *_a, **_k: [])
        response = client.get("/api/v1/ops/live-status")

        assert response.status_code == 200, response.text
        body = response.json()
        assert {"live_execution", "safe_mode", "recovery", "exposure", "admission",
                "summary"} <= set(body)

    def test_production_requires_a_bearer_token(self, safe_file, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api.v1.endpoints.ops_live import router

        monkeypatch.setenv("ENVIRONMENT", "production")
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        client = TestClient(app)

        assert client.get("/api/v1/ops/live-status").status_code == 401
        assert client.get("/api/v1/ops/safe-mode").status_code == 401
        assert client.post("/api/v1/ops/safe-mode/resume", json=self._body()).status_code == 401


# ---------------------------------------------------------------------------
# O2: startup recovery
# ---------------------------------------------------------------------------


class TestRecoveryCycle:
    @pytest.mark.asyncio
    async def test_recovery_runs_for_each_live_account_with_uid_account_and_client(
        self, clean_control_plane
    ):
        registry = _Registry([
            _account("KITE-A1", "u1"), _account("KITE-A2", "u2"),
            _account("KITE-OTHER", "u3", live=False),
        ])
        service = _Service()

        results = await lr.run_recovery_cycle(service=service, kite_accounts=registry)

        assert [(c[0], c[1]) for c in service.calls] == [("u1", "KITE-A1"), ("u2", "KITE-A2")]
        assert all(c[2] is registry.client for c in service.calls)
        assert [r.state for r in results] == [lr.CLEAN, lr.CLEAN]
        assert registry.acquired == ["KITE-A1", "KITE-A2"]

    @pytest.mark.asyncio
    async def test_an_account_with_no_session_waits_instead_of_failing(self):
        registry = _Registry([_account(connected=False)])
        service = _Service()

        results = await lr.run_recovery_cycle(service=service, kite_accounts=registry)

        assert service.calls == []
        assert results[0].state == lr.WAITING_FOR_SESSION

    @pytest.mark.asyncio
    async def test_an_expired_session_waits(self):
        registry = _Registry([_account(expires=1)])
        registry._accounts[0].token_is_live = False
        service = _Service()

        results = await lr.run_recovery_cycle(service=service, kite_accounts=registry)

        assert service.calls == []
        assert results[0].state == lr.WAITING_FOR_SESSION

    @pytest.mark.asyncio
    async def test_a_transient_broker_error_is_recorded_and_not_raised(self):
        registry = _Registry([_account()])
        service = _Service(raises=[TimeoutError("kite timed out")])

        results = await lr.run_recovery_cycle(service=service, kite_accounts=registry)

        assert results[0].state == lr.ERROR
        assert "TimeoutError" in results[0].detail

    @pytest.mark.asyncio
    async def test_unresolved_state_is_reported_as_recovery_required(self):
        registry = _Registry([_account()])
        service = _Service(outcomes=[_dirty_outcome()])

        results = await lr.run_recovery_cycle(service=service, kite_accounts=registry)

        assert results[0].state == lr.RECOVERY_REQUIRED
        assert results[0].unresolved == 2

    @pytest.mark.asyncio
    async def test_a_service_error_outcome_is_not_clean(self):
        registry = _Registry([_account()])
        service = _Service(outcomes=[{"status": "error", "reason": "Control plane uninitialized"}])

        results = await lr.run_recovery_cycle(service=service, kite_accounts=registry)

        assert results[0].state == lr.ERROR
        assert "Control plane" in results[0].detail

    @pytest.mark.asyncio
    async def test_the_periodic_pass_skips_accounts_that_are_clean_and_retries_the_rest(
        self, clean_control_plane
    ):
        from app.services import db

        registry = _Registry([_account("KITE-A1", "u1"), _account("KITE-A2", "u2")])
        service = _Service(outcomes=[_clean_outcome(), _dirty_outcome()])
        for uid, acct in (("u1", "KITE-A1"), ("u2", "KITE-A2")):
            db.set_recovery_state("CLEAN", reason_code="t", uid=uid, account_id=acct)

        await lr.run_recovery_cycle(service=service, kite_accounts=registry)
        assert len(service.calls) == 2

        service.outcomes = [_clean_outcome()]
        await lr.run_recovery_cycle(service=service, kite_accounts=registry, only_unclean=True)

        # Only the account that was RECOVERY_REQUIRED ran again.
        assert [(c[0], c[1]) for c in service.calls[2:]] == [("u2", "KITE-A2")]

    @pytest.mark.asyncio
    async def test_a_durable_recovery_required_after_a_clean_pass_is_retried(
        self, clean_control_plane
    ):
        from app.services import db

        registry = _Registry([_account("KITE-A1", "u1")])
        service = _Service()
        db.set_recovery_state("CLEAN", reason_code="t", uid="u1", account_id="KITE-A1")
        await lr.run_recovery_cycle(service=service, kite_accounts=registry)
        assert len(service.calls) == 1

        # An uncertain broker submit later flips the durable state.
        db.set_recovery_state("RECOVERY_REQUIRED", reason_code="BROKER_TRANSPORT_UNCERTAINTY",
                              uid="u1", account_id="KITE-A1")
        await lr.run_recovery_cycle(service=service, kite_accounts=registry, only_unclean=True)

        assert len(service.calls) == 2

    @pytest.mark.asyncio
    async def test_it_never_resubmits_an_order(self, clean_control_plane):
        """Recovery is observe-only: the broker client never sees a write."""
        from app.services.execution_service import CanonicalExecutionService

        class _Client:
            _account_id = "KITE-REC"
            writes = []

            async def get_orders(self):
                return []

            async def place_order(self, *a, **k):
                self.writes.append("place_order")
                raise AssertionError("recovery must never place an order")

            async def place_gtt(self, *a, **k):
                self.writes.append("place_gtt")
                raise AssertionError("recovery found no position to protect")

        client = _Client()
        registry = _Registry([_account("KITE-REC", "u-rec")], client=client)

        results = await lr.run_recovery_cycle(
            service=CanonicalExecutionService(), kite_accounts=registry
        )

        assert results[0].state == lr.CLEAN, results[0].detail
        assert _Client.writes == []


class TestRootScope:
    """Admission also reads the root control scope that real-account recovery never writes."""

    @pytest.mark.asyncio
    async def test_the_root_scope_is_cleared_once_every_live_account_is_clean(
        self, clean_control_plane
    ):
        from app.services import db

        with db._conn() as c:
            c.execute("DELETE FROM execution_control")
        assert db.get_execution_control()["recovery_state"] == "RECOVERY_REQUIRED"

        registry = _Registry([_account("KITE-A1", "u1"), _account("KITE-A2", "u2")])
        await lr.run_recovery_cycle(service=_Service(), kite_accounts=registry)

        root = db.get_execution_control()
        assert root["recovery_state"] == "CLEAN"
        assert root["actor"] == lr.ACTOR
        assert root["initialized"] == 1

    @pytest.mark.asyncio
    async def test_the_root_scope_stays_while_any_account_is_not_clean(self, clean_control_plane):
        from app.services import db

        with db._conn() as c:
            c.execute("DELETE FROM execution_control")
        registry = _Registry([_account("KITE-A1", "u1"), _account("KITE-A2", "u2")])
        service = _Service(outcomes=[_clean_outcome(), _dirty_outcome()])

        await lr.run_recovery_cycle(service=service, kite_accounts=registry)

        assert db.get_execution_control()["recovery_state"] == "RECOVERY_REQUIRED"

    @pytest.mark.asyncio
    async def test_a_marker_from_before_this_boot_is_retired(self, clean_control_plane):
        from app.services import db

        db.set_recovery_state("RECOVERY_REQUIRED", reason_code="STARTUP",
                              reason="older cli start", actor="sterlingctl")
        lr._STATE.booted_ms = int(time.time() * 1000) + 5_000

        await lr.run_recovery_cycle(
            service=_Service(), kite_accounts=_Registry([_account()])
        )

        assert db.get_execution_control()["recovery_state"] == "CLEAN"

    @pytest.mark.asyncio
    async def test_a_live_uncertainty_written_during_this_run_is_not_overridden(
        self, clean_control_plane
    ):
        from app.services import db

        lr._STATE.booted_ms = int(time.time() * 1000) - 60_000
        db.set_recovery_state("RECOVERY_REQUIRED", reason_code="BROKER_TRANSPORT_UNCERTAINTY",
                              reason="uncertain submit", actor="system")

        await lr.run_recovery_cycle(
            service=_Service(), kite_accounts=_Registry([_account()])
        )

        root = db.get_execution_control()
        assert root["recovery_state"] == "RECOVERY_REQUIRED"
        assert root["reason_code"] == "BROKER_TRANSPORT_UNCERTAINTY"

    @pytest.mark.asyncio
    async def test_the_operator_halt_is_never_touched(self, clean_control_plane):
        from app.services import db

        db.set_operator_state("HALTED", reason_code="operator", reason="stop")
        db.set_recovery_state("RECOVERY_REQUIRED", reason_code="STARTUP", actor="sterlingctl")
        lr._STATE.booted_ms = int(time.time() * 1000) + 5_000

        await lr.run_recovery_cycle(
            service=_Service(), kite_accounts=_Registry([_account()])
        )

        root = db.get_execution_control()
        assert root["recovery_state"] == "CLEAN"
        assert root["operator_state"] == "HALTED"

    @pytest.mark.asyncio
    async def test_no_live_account_leaves_the_control_plane_alone(self, clean_control_plane):
        from app.services import db

        with db._conn() as c:
            c.execute("DELETE FROM execution_control")

        await lr.run_recovery_cycle(service=_Service(), kite_accounts=_Registry([]))

        assert db.get_execution_control()["initialized"] == 0


class TestRecoveryLoop:
    @pytest.mark.asyncio
    async def test_it_backs_off_until_it_succeeds_then_settles_to_the_periodic_pace(
        self, monkeypatch
    ):
        monkeypatch.setattr(lr, "_after_state_change", lambda: asyncio.sleep(0))
        delays = []

        async def fake_sleep(seconds):
            delays.append(seconds)

        outcomes = iter([
            [lr.AccountRecovery("u", "a", state=lr.WAITING_FOR_SESSION)],
            [lr.AccountRecovery("u", "a", state=lr.ERROR)],
            [lr.AccountRecovery("u", "a", state=lr.ERROR)],
            [lr.AccountRecovery("u", "a", state=lr.CLEAN)],
            [lr.AccountRecovery("u", "a", state=lr.CLEAN)],
        ])

        async def cycle(*, only_unclean):
            return next(outcomes)

        await lr.recovery_loop(sleep=fake_sleep, cycle=cycle, max_cycles=5)

        assert delays == [
            lr.RECOVERY_INITIAL_DELAY_S,
            lr.RECOVERY_BACKOFF_START_S * 2,
            lr.RECOVERY_BACKOFF_START_S * 4,
            lr.RECOVERY_BACKOFF_START_S * 8,
            lr.RECOVERY_PERIODIC_S,
            lr.RECOVERY_PERIODIC_S,
        ]

    @pytest.mark.asyncio
    async def test_the_backoff_is_capped(self, monkeypatch):
        monkeypatch.setattr(lr, "_after_state_change", lambda: asyncio.sleep(0))
        delays = []

        async def fake_sleep(seconds):
            delays.append(seconds)

        async def cycle(*, only_unclean):
            return [lr.AccountRecovery("u", "a", state=lr.ERROR)]

        await lr.recovery_loop(sleep=fake_sleep, cycle=cycle, max_cycles=12)

        assert max(delays[1:]) == lr.RECOVERY_PERIODIC_S

    @pytest.mark.asyncio
    async def test_the_first_pass_covers_everyone_and_later_passes_only_the_unclean(
        self, monkeypatch
    ):
        monkeypatch.setattr(lr, "_after_state_change", lambda: asyncio.sleep(0))
        seen = []

        async def cycle(*, only_unclean):
            seen.append(only_unclean)
            return [lr.AccountRecovery("u", "a", state=lr.CLEAN)]

        await lr.recovery_loop(sleep=lambda _s: asyncio.sleep(0), cycle=cycle, max_cycles=3)

        assert seen == [False, True, True]

    @pytest.mark.asyncio
    async def test_a_crashing_cycle_never_kills_the_loop(self, monkeypatch):
        monkeypatch.setattr(lr, "_after_state_change", lambda: asyncio.sleep(0))
        calls = {"n": 0}

        async def cycle(*, only_unclean):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("boom")
            return [lr.AccountRecovery("u", "a", state=lr.CLEAN)]

        await lr.recovery_loop(sleep=lambda _s: asyncio.sleep(0), cycle=cycle, max_cycles=4)

        assert calls["n"] == 4

    @pytest.mark.asyncio
    async def test_cancellation_propagates(self):
        started = asyncio.Event()

        async def cycle(*, only_unclean):
            started.set()
            await asyncio.sleep(3600)

        task = asyncio.create_task(
            lr.recovery_loop(initial_delay=0, sleep=lambda _s: asyncio.sleep(0), cycle=cycle)
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# ---------------------------------------------------------------------------
# O3: exposure observation refresher
# ---------------------------------------------------------------------------


class TestExposureRefresher:
    def _recorded(self):
        box = []
        return box, box.append

    @pytest.mark.asyncio
    async def test_it_records_what_the_broker_said(self):
        from app.services.external_positions import ExternalExposure

        box, recorder = self._recorded()

        async def exposure():
            return ExternalExposure(readable=True, accounts=("KITE-A1",))

        state = await lr.refresh_exposure_once(
            kite_accounts=_Registry([_account()]), exposure_fn=exposure, recorder=recorder
        )

        assert state.outcome == "ok"
        assert len(box) == 1 and box[0].readable is True

    @pytest.mark.asyncio
    async def test_it_skips_cleanly_with_no_live_account(self):
        box, recorder = self._recorded()

        async def exposure():
            pytest.fail("must not ask a broker that is not there")

        state = await lr.refresh_exposure_once(
            kite_accounts=_Registry([_account(live=False)]), exposure_fn=exposure,
            recorder=recorder,
        )

        assert state.outcome == "skipped"
        assert "no live broker account" in state.detail
        assert box == []

    @pytest.mark.asyncio
    async def test_it_skips_cleanly_with_no_session(self):
        box, recorder = self._recorded()

        async def exposure():
            pytest.fail("must not ask without a session")

        state = await lr.refresh_exposure_once(
            kite_accounts=_Registry([_account(connected=False)]), exposure_fn=exposure,
            recorder=recorder,
        )

        assert state.outcome == "skipped" and state.detail == "no live broker session"
        assert box == []

    @pytest.mark.asyncio
    async def test_an_unreadable_answer_is_recorded_as_unreadable(self):
        from app.services.external_positions import ExternalExposure

        box, recorder = self._recorded()

        async def exposure():
            return ExternalExposure(readable=False, detail="gateway down")

        state = await lr.refresh_exposure_once(
            kite_accounts=_Registry([_account()]), exposure_fn=exposure, recorder=recorder
        )

        assert state.outcome == "unreadable" and state.consecutive_failures == 1
        assert box[0].readable is False

    @pytest.mark.asyncio
    async def test_an_exception_is_recorded_not_raised(self):
        box, recorder = self._recorded()

        async def exposure():
            raise ConnectionError("reset by peer")

        state = await lr.refresh_exposure_once(
            kite_accounts=_Registry([_account()]), exposure_fn=exposure, recorder=recorder
        )

        assert state.outcome == "unreadable"
        assert "ConnectionError" in box[0].detail
        assert box[0].readable is False

    @pytest.mark.asyncio
    async def test_a_hung_broker_call_times_out_into_an_unreadable_record(self):
        box, recorder = self._recorded()

        async def exposure():
            await asyncio.sleep(3600)

        state = await lr.refresh_exposure_once(
            kite_accounts=_Registry([_account()]), exposure_fn=exposure, recorder=recorder,
            timeout=0.05,
        )

        assert state.outcome == "unreadable"
        assert "timed out" in box[0].detail

    @pytest.mark.asyncio
    async def test_the_event_loop_is_not_blocked_by_the_refresh(self):
        """A slow broker answer must not stall other coroutines."""
        from app.services.external_positions import ExternalExposure

        ticks = []

        async def heartbeat():
            for _ in range(5):
                ticks.append(time.monotonic())
                await asyncio.sleep(0.01)

        async def exposure():
            await asyncio.sleep(0.08)
            return ExternalExposure(readable=True, accounts=("KITE-A1",))

        _, recorder = self._recorded()
        await asyncio.gather(
            heartbeat(),
            lr.refresh_exposure_once(
                kite_accounts=_Registry([_account()]), exposure_fn=exposure, recorder=recorder
            ),
        )

        assert len(ticks) == 5

    @pytest.mark.asyncio
    async def test_the_loop_survives_a_failing_refresh_and_runs_every_interval(self, monkeypatch):
        monkeypatch.setattr(lr, "_after_state_change", lambda: asyncio.sleep(0))
        delays, calls = [], {"n": 0}

        async def fake_sleep(seconds):
            delays.append(seconds)

        async def refresh():
            calls["n"] += 1
            if calls["n"] in (1, 3):
                raise RuntimeError("registry exploded")

        await lr.exposure_refresher_loop(sleep=fake_sleep, refresh=refresh, max_cycles=4)

        assert calls["n"] == 4
        assert delays == [lr.EXPOSURE_INITIAL_DELAY_S] + [lr.EXPOSURE_REFRESH_S] * 4
        assert lr.EXPOSURE_REFRESH_S == 20.0

    @pytest.mark.asyncio
    async def test_the_real_recorder_writes_the_observation_admission_reads(
        self, tmp_path, monkeypatch
    ):
        from app.services import external_positions as ep

        monkeypatch.setenv("STERLING_EXTERNAL_EXPOSURE_FILE", str(tmp_path / "ext.json"))
        monkeypatch.setattr(ep, "_live_account_configured", lambda: True)
        monkeypatch.setattr(ep, "live_account_ids", lambda: ("KITE-A1",))

        async def exposure():
            return ep.ExternalExposure(readable=True, accounts=("KITE-A1",))

        await lr.refresh_exposure_once(
            kite_accounts=_Registry([_account("KITE-A1")]), exposure_fn=exposure
        )

        assert ep.last_observation()["readable"] is True
        assert ep.admission_blocker() == ("", "")

    @pytest.mark.asyncio
    async def test_a_skip_never_overwrites_an_existing_observation(self, tmp_path, monkeypatch):
        from app.services import external_positions as ep

        path = tmp_path / "ext.json"
        path.write_text('{"marker": "keep"}')
        monkeypatch.setenv("STERLING_EXTERNAL_EXPOSURE_FILE", str(path))

        await lr.refresh_exposure_once(kite_accounts=_Registry([]))

        assert json.loads(path.read_text()) == {"marker": "keep"}


# ---------------------------------------------------------------------------
# entries become reachable: the whole chain, with fakes
# ---------------------------------------------------------------------------


class TestEntriesBecomeReachable:
    @pytest.mark.asyncio
    async def test_a_cold_machine_opens_entries_only_after_the_runtime_has_run(
        self, tmp_path, monkeypatch, clean_control_plane
    ):
        from app.services import db
        from app.services import external_positions as ep
        from app.services.safety_supervisor import SafetySupervisor

        monkeypatch.setenv("STERLING_SAFE_MODE_FILE", str(tmp_path / "safe_mode.json"))
        monkeypatch.setenv("STERLING_EXTERNAL_EXPOSURE_FILE", str(tmp_path / "ext.json"))
        with db._conn() as c:
            c.execute("DELETE FROM execution_control")

        account = _account("KITE-LIVE", "owner")
        registry = _Registry([account])
        monkeypatch.setattr("app.services.exchanges.kite.accounts.bootstrap", lambda: None)
        monkeypatch.setattr(
            "app.services.exchanges.kite.accounts.all_accounts", lambda: [account]
        )

        class _Broker:
            _account_id = "KITE-LIVE"

            async def get_positions_raw(self):
                return {"net": []}

            async def get_orders(self):
                return []

            async def get_gtts(self):
                # Sterling's own protective stop on a contract it holds.
                return [{
                    "id": 4242, "status": "active",
                    "condition": {"exchange": "NFO", "tradingsymbol": "NIFTY26OCT25000CE"},
                    "orders": [{"exchange": "NFO", "tradingsymbol": "NIFTY26OCT25000CE",
                                "product": "NRML", "transaction_type": "SELL"}],
                }]

            async def get_alerts(self):
                return []

        broker = _Broker()
        registry.client = broker

        async def acquire(_account):
            return broker

        monkeypatch.setattr("app.services.exchanges.kite.accounts.acquire_client", acquire)
        monkeypatch.setattr(
            ep, "_known_symbols", lambda _a: {("NFO", "NRML", "NIFTY26OCT25000CE")}
        )
        monkeypatch.setattr(ep, "_known_broker_ids", lambda _a: (frozenset({"4242"}), frozenset()))

        def admitted():
            return SafetySupervisor().authorize(uid="owner", account_id="KITE-LIVE")

        # Cold machine: nothing initialised, every gate refuses.
        cold = admitted()
        assert cold.allowed is False
        assert {"safe_mode", "recovery_required"} & set(cold.snapshot.blockers)

        boot = lr.initialise_safe_mode_if_clean(check=lambda: lr.CleanCheck(clean=True))
        assert boot.state == NORMAL
        await lr.run_recovery_cycle(service=_Service(), kite_accounts=registry)
        await lr.refresh_exposure_once(kite_accounts=registry)

        warm = admitted()
        assert warm.allowed is True, warm.reason

        # The kill-style safety controls still refuse: engage safe mode again.
        SafeModeService(tmp_path / "safe_mode.json").engage(
            trigger=SafeModeTrigger.OPERATOR, reason="owner pause"
        )
        paused = admitted()
        assert paused.allowed is False
        assert "safe_mode" in paused.snapshot.blockers


# ---------------------------------------------------------------------------
# live execution kill switch state, status surface, boot alert
# ---------------------------------------------------------------------------


class TestLiveExecutionState:
    @pytest.mark.parametrize("value", ["0", "false", "OFF", " no ", "Disabled", "garbage", "ofF "])
    def test_the_kill_switch_values(self, value, monkeypatch):
        monkeypatch.setenv(lr.LIVE_EXECUTION_ENV, value)

        state = lr.live_execution_state()

        assert state["kill_switch"] is True
        assert state["enabled"] is False

    @pytest.mark.parametrize("value", [None, "", "  ", "1", "true", "on", "YES", "enabled"])
    def test_anything_else_leaves_the_kill_switch_off(self, value, monkeypatch):
        if value is None:
            monkeypatch.delenv(lr.LIVE_EXECUTION_ENV, raising=False)
        else:
            monkeypatch.setenv(lr.LIVE_EXECUTION_ENV, value)

        assert lr.live_execution_state()["kill_switch"] is False

    def test_a_disabled_master_floor_is_reported_even_without_the_kill_switch(self, monkeypatch):
        from app.core import lane_registry

        monkeypatch.delenv(lr.LIVE_EXECUTION_ENV, raising=False)
        monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", False)
        monkeypatch.delattr(lane_registry, "live_execution_enabled", raising=False)

        state = lr.live_execution_state()

        assert state["master_floor"] is False
        assert state["enabled"] is False
        assert state["kill_switch"] is False


class TestStatus:
    @pytest.fixture()
    def wired(self, safe_file, tmp_path, monkeypatch):
        SafeModeService(safe_file).initialise(operator_ack=True)
        monkeypatch.setenv("STERLING_EXTERNAL_EXPOSURE_FILE", str(tmp_path / "ext.json"))
        account = _account(
            "KITE-LIVE", "owner",
            api_key="API-KEY-SECRET-123", api_secret="API-SECRET-456",
            access_token="ACCESS-TOKEN-789", refresh_token="REFRESH-TOKEN-000",
        )
        monkeypatch.setattr(lr, "_live_accounts", lambda *_a, **_k: [account])
        # Admission reads the live-account set from the same registry the status
        # does; pin it so the result does not depend on the shared test database.
        from app.services import external_positions as ep

        monkeypatch.setattr(ep, "live_account_ids", lambda: ("KITE-LIVE",))
        return account

    def test_the_status_contains_no_secret_or_broker_identity(self, wired):
        status = lr.live_status()
        blob = json.dumps(status)

        for secret in ("API-KEY-SECRET-123", "API-SECRET-456", "ACCESS-TOKEN-789",
                       "REFRESH-TOKEN-000", "AB1234"):
            assert secret not in blob
        assert "access_token" not in blob and "api_secret" not in blob

    def test_the_status_reports_every_gate(self, wired):
        status = lr.live_status()

        assert status["live_accounts"] == 1
        assert status["safe_mode"]["state"] == NORMAL
        assert status["recovery"]["overall"] == lr.PENDING
        assert status["recovery"]["accounts"][0]["account_id"] == "KITE-LIVE"
        assert status["exposure"]["observed"] is False
        assert status["admission"]["may_open_new_exposure"] is False
        assert status["family_mode_setting_ignored"] is False

    def test_the_status_line_says_all_four_things(self, wired, monkeypatch):
        monkeypatch.setenv(lr.LIVE_EXECUTION_ENV, "off")

        line = lr.summary_line()

        assert line.startswith("LIVE STATUS:")
        assert "execution=KILLED" in line
        assert "safe_mode=NORMAL" in line
        assert "recovery=PENDING" in line
        assert "exposure_observation=never observed" in line
        assert "new_entries=BLOCKED" in line

    def test_exposure_freshness_is_reported(self, wired, tmp_path):
        from app.services import external_positions as ep

        ep.record_observation(
            ep.ExternalExposure(readable=True, accounts=("KITE-LIVE",),
                                owned_automation=("gtt:1:ACTIVE",))
        )

        exposure = lr.live_status()["exposure"]

        assert exposure["observed"] is True and exposure["fresh"] is True
        assert exposure["readable"] is True
        assert exposure["sterling_owned_orders_and_stops"] == 1
        assert "fresh" in lr.summary_line()

    def test_a_stale_observation_is_reported_as_stale(self, wired, tmp_path):
        (tmp_path / "ext.json").write_text(json.dumps({
            "observed_at": "2020-01-01T00:00:00+00:00", "readable": True, "count": 0,
            "accounts": ["KITE-LIVE"],
        }))

        exposure = lr.live_status()["exposure"]

        assert exposure["fresh"] is False
        assert "STALE" in lr.summary_line()

    def test_the_retired_family_mode_setting_is_flagged(self, wired, monkeypatch):
        monkeypatch.setenv("STERLING_FAMILY_MODE", "true")
        assert lr.live_status()["family_mode_setting_ignored"] is True

    def test_the_status_is_still_produced_when_the_registry_is_unreadable(self, monkeypatch, safe_file):
        def boom(*_a, **_k):
            raise RuntimeError("registry down")

        monkeypatch.setattr(lr, "_live_accounts", boom)

        status = lr.live_status()

        assert status["live_accounts"] == 0
        assert "registry down" in status["recovery"]["registry_error"]


class TestBootAlert:
    def test_the_boot_alert_goes_to_the_owner_only_through_the_outbox(self, monkeypatch):
        sent = []

        def enqueue(uid, **kwargs):
            sent.append((uid, kwargs))
            return "TGL-1"

        monkeypatch.setattr(
            lr, "_live_accounts",
            lambda *_a, **_k: [_account("KITE-A", "owner"), _account("KITE-B", "owner")],
        )
        status = {"safe_mode": {"active": False}, "summary": "LIVE STATUS: ok"}

        count = lr.announce_boot(status, enqueue=enqueue)

        assert count == 1
        assert [u for u, _ in sent] == ["owner"]
        kwargs = sent[0][1]
        assert kwargs["strategy"] == "Sterling"
        assert kwargs["severity"] == "INFO"
        assert kwargs["dedupe_key"].startswith("live-boot:")
        assert "LIVE STATUS" in kwargs["detail"]

    def test_an_engaged_safe_mode_is_a_critical_alert_that_says_how_to_resume(self, monkeypatch):
        sent = []
        monkeypatch.setattr(lr, "_live_accounts", lambda *_a, **_k: [_account("K", "owner")])
        status = {"safe_mode": {"active": True, "reason": "unresolved order"},
                  "summary": "LIVE STATUS: blocked"}

        lr.announce_boot(status, enqueue=lambda uid, **kw: sent.append(kw) or "id")

        assert sent[0]["severity"] == "CRITICAL"
        assert "SAFE_MODE is engaged" in sent[0]["detail"]
        assert "unresolved order" in sent[0]["detail"]

    def test_it_is_announced_once_per_boot(self, monkeypatch):
        sent = []
        monkeypatch.setattr(lr, "_live_accounts", lambda *_a, **_k: [_account("K", "owner")])
        status = {"safe_mode": {"active": False}, "summary": "s"}

        lr.announce_boot(status, enqueue=lambda uid, **kw: sent.append(kw) or "id")
        lr.announce_boot(status, enqueue=lambda uid, **kw: sent.append(kw) or "id")

        assert len(sent) == 1

    def test_a_failing_outbox_never_raises(self, monkeypatch):
        monkeypatch.setattr(lr, "_live_accounts", lambda *_a, **_k: [_account("K", "owner")])

        def boom(uid, **kw):
            raise RuntimeError("outbox locked")

        status = {"safe_mode": {"active": False}, "summary": "s"}

        assert lr.announce_boot(status, enqueue=boom) == 0

    def test_with_no_live_account_the_default_owner_is_used(self, monkeypatch):
        sent = []
        monkeypatch.setattr(lr, "_live_accounts", lambda *_a, **_k: [])

        lr.announce_boot(
            {"safe_mode": {"active": False}, "summary": "s"},
            enqueue=lambda uid, **kw: sent.append(uid) or "id",
        )

        assert sent == ["default"]

    @pytest.mark.asyncio
    async def test_a_status_change_is_logged_and_entries_opening_is_announced_once(
        self, monkeypatch, caplog
    ):
        queued = []
        statuses = iter([
            {"summary": "LIVE STATUS: blocked", "safe_mode": {"active": False},
             "admission": {"may_open_new_exposure": False}},
            {"summary": "LIVE STATUS: open", "safe_mode": {"active": False},
             "admission": {"may_open_new_exposure": True}},
            {"summary": "LIVE STATUS: open", "safe_mode": {"active": False},
             "admission": {"may_open_new_exposure": True}},
        ])
        monkeypatch.setattr(lr, "live_status", lambda: next(statuses))
        monkeypatch.setattr(lr, "announce_boot", lambda status: 1)
        monkeypatch.setattr(
            lr, "_enqueue", lambda *a, **k: queued.append(a[0]) or 1
        )
        lr._STATE.boot_announced = True

        with caplog.at_level("INFO", logger=lr.log.name):
            await lr._after_state_change()
            await lr._after_state_change()
            await lr._after_state_change()

        assert queued == ["LIVE READY"]
        lines = [r.message for r in caplog.records if "LIVE STATUS" in r.message]
        assert lines == ["LIVE STATUS: blocked", "LIVE STATUS: open"]


# ---------------------------------------------------------------------------
# lifespan hooks
# ---------------------------------------------------------------------------


class TestLifespanHooks:
    @pytest.mark.asyncio
    async def test_start_runs_the_boot_step_and_starts_both_tasks_and_stop_cancels_them(
        self, safe_file, monkeypatch
    ):
        monkeypatch.setattr(lr, "provably_clean", lambda: lr.CleanCheck(clean=True))
        monkeypatch.setattr(lr, "_live_accounts", lambda *_a, **_k: [])

        handles = lr.start_live_runtime()

        try:
            assert safe_file.exists()
            assert SafeModeService(safe_file).read().state == NORMAL
            assert lr._STATE.safe_mode_boot.action == "initialised"
            assert {t.get_name() for t in handles.tasks} == {"live-recovery", "live-exposure"}
            assert all(not t.done() for t in handles.tasks)
        finally:
            await lr.stop_live_runtime(handles)

        assert all(t.done() for t in handles.tasks)

    @pytest.mark.asyncio
    async def test_a_boot_step_failure_leaves_safe_mode_engaged_and_still_starts(
        self, safe_file, monkeypatch, caplog
    ):
        def boom():
            raise RuntimeError("cannot prove anything")

        monkeypatch.setattr(lr, "provably_clean", boom)
        monkeypatch.setattr(lr, "_live_accounts", lambda *_a, **_k: [])

        with caplog.at_level("CRITICAL", logger=lr.log.name):
            handles = lr.start_live_runtime()
        try:
            assert not safe_file.exists()
            assert SafeModeService(safe_file).read().active is True
            assert any("SAFE_MODE REMAINS ENGAGED" in r.message for r in caplog.records)
        finally:
            await lr.stop_live_runtime(handles)

    @pytest.mark.asyncio
    async def test_the_kill_switch_and_retired_family_mode_are_reported_at_boot(
        self, safe_file, monkeypatch, caplog
    ):
        monkeypatch.setattr(lr, "provably_clean", lambda: lr.CleanCheck(clean=True))
        monkeypatch.setattr(lr, "_live_accounts", lambda *_a, **_k: [])
        monkeypatch.setenv(lr.LIVE_EXECUTION_ENV, "off")
        monkeypatch.setenv("STERLING_FAMILY_MODE", "true")

        with caplog.at_level("INFO", logger=lr.log.name):
            handles = lr.start_live_runtime()
        try:
            text = "\n".join(r.message for r in caplog.records)
            assert "KILL SWITCH IS ON" in text
            assert "Family Mode is retired" in text
            assert "LIVE STATUS:" in text
            assert "execution=KILLED" in text
        finally:
            await lr.stop_live_runtime(handles)

    @pytest.mark.asyncio
    async def test_stop_tolerates_no_handles(self):
        await lr.stop_live_runtime(None)
