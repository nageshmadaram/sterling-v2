"""Item 4a: a durable plan store and the arm endpoint.

A plan that exists only in memory cannot be armed safely: a restart between
publishing and arming loses the thing the operator approved, and a browser retry
can arm a plan twice. The store makes a plan an artifact with a revision, and
arming is a state transition against that revision — not a message.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

_IST = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture
def store(tmp_path):
    from app.services.snapback_plan_store import PlanStore

    return PlanStore(db_path=str(tmp_path / "plans.db"))


def _plan(**over):
    base = dict(
        plan_id="PLAN-1",
        opportunity_id="OPP-1",
        account_id="KITE-1",
        owner_uid="default",
        option_symbol="NIFTY26OCT25000PE",
        option_quantity=75,
        futures_symbol="NIFTY26OCTFUT",
        target_futures_quantity=75,
        max_option_price=120.0,
        hedge_price_limit=25100.0,
        risk_amount=9000.0,
        cash_required=9000.0,
        margin_required=140000.0,
        policy_snapshot_hash="pol-1",
        runtime_build_sha="build-1",
    )
    base.update(over)
    return base


# ------------------------------------------------------------- persistence

def test_consume_rejects_a_stale_observed_revision(store):
    from app.services.snapback_plan_store import PlanConflictError

    store.publish(**_plan())
    armed = store.arm(
        "PLAN-1", expected_revision=1, idempotency_key="arm-1", armed_by="u"
    )
    stale_revision = armed["revision"]
    store.mark_reconciling("PLAN-1", reason="new recovery evidence")

    with pytest.raises(PlanConflictError, match="revision changed"):
        store.consume("PLAN-1", expected_revision=stale_revision)

    assert store.get("PLAN-1")["status"] == "RECONCILING"


def test_only_one_worker_can_allocate_recovery_from_the_same_observed_revision(store):
    from app.services.snapback_plan_store import PlanConflictError

    store.publish(**_plan())
    store.arm('PLAN-1', expected_revision=1, idempotency_key='arm-1', armed_by='u')
    recon = store.mark_reconciling(
        'PLAN-1', reason='hedge_rejected_zero_fill:broker rejected'
    )
    observed = recon['revision']

    first = store.begin_recovery_attempt(
        'PLAN-1', expected_revision=observed, reason='worker-one'
    )
    assert first['revision'] == observed + 1

    with pytest.raises(PlanConflictError, match='revision changed'):
        store.begin_recovery_attempt(
            'PLAN-1', expected_revision=observed, reason='worker-two'
        )


def test_recovery_attempt_returns_the_revision_owned_by_its_cas(store, monkeypatch):
    """A post-commit reread must not transfer another worker's newer revision."""
    store.publish(**_plan())
    store.arm(
        "PLAN-1", expected_revision=1, idempotency_key="arm-1", armed_by="u"
    )
    recon = store.mark_reconciling(
        "PLAN-1", reason="hedge_rejected_zero_fill:broker rejected"
    )
    observed = recon["revision"]

    real_get = store.get
    calls = 0

    def racing_get(plan_id):
        nonlocal calls
        calls += 1
        row = real_get(plan_id)
        if calls > 1 and row is not None:
            return {**row, "revision": int(row["revision"]) + 1}
        return row

    monkeypatch.setattr(store, "get", racing_get)

    attempt = store.begin_recovery_attempt(
        "PLAN-1",
        expected_revision=observed,
        reason="retry confirmed zero-fill rejection",
    )

    assert attempt["revision"] == observed + 1
    assert calls == 1


def test_recovery_attempt_revision_and_reason_evolution_are_durable(store):
    store.publish(**_plan())
    store.arm("PLAN-1", expected_revision=1, idempotency_key="arm-1", armed_by="u")
    recon = store.mark_reconciling("PLAN-1", reason="hedge_rejected_zero_fill:broker rejected")
    first = store.begin_recovery_attempt(
        "PLAN-1",
        expected_revision=recon["revision"],
        reason="retry zero-fill rejection",
    )

    assert recon["status"] == "RECONCILING"
    assert first["revision"] == recon["revision"] + 1

    # Same state, new evidence: keep revision fixed but preserve the changed reason.
    same_revision = store.mark_reconciling("PLAN-1", reason="hedge_submission_unknown")
    assert same_revision["revision"] == first["revision"]

    events = store.events("PLAN-1")
    recovery = [e for e in events if e["event"] == "RECOVERY_ATTEMPT"]
    recon_events = [e for e in events if e["event"] == "RECONCILING"]
    assert len(recovery) == 1
    assert len(recon_events) == 2
    assert "hedge_submission_unknown" in recon_events[-1]["detail_json"]


def test_reconciling_event_persists_changed_leg_evidence_without_revision_churn(store):
    import json

    store.publish(**_plan())
    store.arm("PLAN-1", expected_revision=1, idempotency_key="arm-1", armed_by="u")

    first = store.mark_reconciling(
        "PLAN-1",
        reason="protection_not_active",
        details={"option_filled": 75, "hedge_filled": 75, "hedge_target": 75},
    )
    second = store.mark_reconciling(
        "PLAN-1",
        reason="protection_not_active",
        details={"option_filled": 75, "hedge_filled": 25, "hedge_target": 75},
    )

    assert second["revision"] == first["revision"]
    events = [e for e in store.events("PLAN-1") if e["event"] == "RECONCILING"]
    assert len(events) == 2
    latest = json.loads(events[-1]["detail_json"])
    assert latest["reason"] == "protection_not_active"
    assert latest["hedge_filled"] == 25
    assert latest["hedge_target"] == 75


def test_reconciling_state_is_durable_and_idempotent(store, tmp_path):
    from app.services.snapback_plan_store import PlanStore

    store.publish(**_plan())
    armed = store.arm(
        "PLAN-1", expected_revision=1, idempotency_key="arm-1", armed_by="u"
    )
    first = store.mark_reconciling("PLAN-1", reason="hedge_fill_mismatch")
    second = store.mark_reconciling("PLAN-1", reason="hedge_fill_mismatch")

    assert armed["status"] == "ARMED"
    assert first["status"] == "RECONCILING"
    assert second["status"] == "RECONCILING"
    assert second["revision"] == first["revision"]

    restarted = PlanStore(db_path=str(tmp_path / "plans.db"))
    restored = restarted.get("PLAN-1")
    assert restored is not None
    assert restored["status"] == "RECONCILING"
    events = restarted.events("PLAN-1")
    assert [e["event"] for e in events].count("RECONCILING") == 1



def test_a_published_plan_survives_a_restart(store, tmp_path):
    from app.services.snapback_plan_store import PlanStore

    store.publish(**_plan())

    reopened = PlanStore(db_path=store.db_path)
    plan = reopened.get("PLAN-1")

    assert plan is not None
    assert plan["option_symbol"] == "NIFTY26OCT25000PE"
    assert plan["status"] == "PUBLISHED"
    assert plan["revision"] == 1


def test_publishing_the_same_plan_twice_is_refused(store):
    from app.services.snapback_plan_store import PlanConflictError

    store.publish(**_plan())

    with pytest.raises(PlanConflictError):
        store.publish(**_plan())


def test_an_unknown_plan_reads_as_absent(store):
    assert store.get("PLAN-NOPE") is None


# -------------------------------------------------------------------- arm


def test_arming_requires_the_expected_revision(store):
    from app.services.snapback_plan_store import PlanConflictError

    store.publish(**_plan())

    with pytest.raises(PlanConflictError):
        store.arm("PLAN-1", expected_revision=7, idempotency_key="k1", armed_by="u1")


def test_arming_advances_the_revision(store):
    store.publish(**_plan())

    armed = store.arm("PLAN-1", expected_revision=1, idempotency_key="k1", armed_by="u1")

    assert armed["status"] == "ARMED"
    assert armed["revision"] == 2
    assert armed["armed_by"] == "u1"


def test_a_repeated_arm_with_the_same_key_does_not_arm_twice(store):
    """A browser retry must not create a second authorization."""
    store.publish(**_plan())

    first = store.arm("PLAN-1", expected_revision=1, idempotency_key="k1", armed_by="u1")
    second = store.arm("PLAN-1", expected_revision=1, idempotency_key="k1", armed_by="u1")

    assert second["revision"] == first["revision"]
    assert second["idempotent_replay"] is True


def test_a_different_key_cannot_rearm_an_armed_plan(store):
    from app.services.snapback_plan_store import PlanConflictError

    store.publish(**_plan())
    store.arm("PLAN-1", expected_revision=1, idempotency_key="k1", armed_by="u1")

    with pytest.raises(PlanConflictError):
        store.arm("PLAN-1", expected_revision=2, idempotency_key="k2", armed_by="u1")


def test_an_expired_plan_cannot_be_armed(store):
    from app.services.snapback_plan_store import PlanExpiredError

    store.publish(**_plan(expires_at=datetime(2026, 9, 17, 9, 30, tzinfo=_IST).isoformat()))

    with pytest.raises(PlanExpiredError):
        store.arm(
            "PLAN-1", expected_revision=1, idempotency_key="k1", armed_by="u1",
            now=datetime(2026, 9, 17, 9, 31, tzinfo=_IST),
        )


def test_a_cancelled_plan_cannot_be_armed(store):
    from app.services.snapback_plan_store import PlanConflictError

    store.publish(**_plan())
    store.cancel("PLAN-1", reason="superseded")

    with pytest.raises(PlanConflictError):
        store.arm("PLAN-1", expected_revision=2, idempotency_key="k1", armed_by="u1")


# ------------------------------------------------------------- the endpoint


def _client(store, monkeypatch, **overrides):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.v1.endpoints import snapback_plans

    monkeypatch.setattr(snapback_plans, "_store", lambda: store)
    monkeypatch.setattr(
        snapback_plans, "_admission",
        lambda **_k: overrides.get("admission", {"allowed": True, "mode": "LIVE",
                                                 "reasons": []}),
    )
    # Execution belongs to the dispatcher; these tests are about authorization.
    scheduled = overrides.setdefault("scheduled", [])
    monkeypatch.setattr(
        snapback_plans, "_schedule_execution",
        lambda uid, plan_id: scheduled.append((uid, plan_id)) or "scheduled",
    )

    app = FastAPI()
    app.include_router(snapback_plans.router)
    return TestClient(app)


def test_foreign_user_cannot_read_arm_or_list_plan_events(store, monkeypatch):
    store.publish(**_plan(owner_uid="owner-a"))
    client = _client(store, monkeypatch)

    headers = {"X-User-Id": "owner-b"}
    assert client.get("/snapback/plans/PLAN-1", headers=headers).status_code == 404
    assert client.get(
        "/snapback/plans/PLAN-1/events", headers=headers
    ).status_code == 404
    assert client.post(
        "/snapback/plans/PLAN-1/arm",
        headers=headers,
        json={"expected_revision": 1, "idempotency_key": "foreign"},
    ).status_code == 404
    assert store.get("PLAN-1")["status"] == "PUBLISHED"


def test_legacy_unowned_plan_is_not_exposed_over_http(store, monkeypatch):
    store.publish(**_plan(owner_uid=""))
    client = _client(store, monkeypatch)

    assert client.get("/snapback/plans/PLAN-1").status_code == 404


def test_arming_an_unknown_plan_is_404(store, monkeypatch):
    client = _client(store, monkeypatch)

    response = client.post(
        "/snapback/plans/PLAN-NOPE/arm",
        json={"expected_revision": 1, "idempotency_key": "k1"},
    )

    assert response.status_code == 404


def test_a_revision_conflict_is_409(store, monkeypatch):
    store.publish(**_plan())
    client = _client(store, monkeypatch)

    response = client.post(
        "/snapback/plans/PLAN-1/arm",
        json={"expected_revision": 99, "idempotency_key": "k1"},
    )

    assert response.status_code == 409


def test_arming_a_plan_succeeds_when_the_live_system_admits_it(store, monkeypatch):
    store.publish(**_plan())
    scheduled = []
    client = _client(store, monkeypatch, scheduled=scheduled)

    response = client.post(
        "/snapback/plans/PLAN-1/arm",
        json={"expected_revision": 1, "idempotency_key": "k1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ARMED" and body["mode"] == "LIVE"
    assert body["execution"] == "scheduled"
    # Arming authorises and hands the plan to the dispatcher; it places nothing.
    assert scheduled == [("default", "PLAN-1")]


def test_a_replayed_arm_does_not_schedule_a_second_execution(store, monkeypatch):
    store.publish(**_plan())
    scheduled = []
    client = _client(store, monkeypatch, scheduled=scheduled)

    body = {"expected_revision": 1, "idempotency_key": "k1"}
    client.post("/snapback/plans/PLAN-1/arm", json=body)
    again = client.post("/snapback/plans/PLAN-1/arm", json=body)

    assert again.json()["idempotent_replay"] is True
    assert again.json()["execution"] == "already_replayed"
    assert len(scheduled) == 1


def test_live_is_the_only_mode(store, monkeypatch):
    store.publish(**_plan())
    client = _client(store, monkeypatch)

    response = client.post(
        "/snapback/plans/PLAN-1/arm",
        json={"expected_revision": 1, "idempotency_key": "k1", "mode": "SIMULATED"},
    )

    assert response.status_code == 422
    assert store.get("PLAN-1")["status"] == "PUBLISHED"


def test_arming_is_refused_with_the_real_reason_codes(store, monkeypatch):
    store.publish(**_plan())
    client = _client(store, monkeypatch, admission={
        "allowed": False, "mode": "LIVE", "reasons": ["kill_switch", "safe_mode"],
    })

    response = client.post(
        "/snapback/plans/PLAN-1/arm",
        json={"expected_revision": 1, "idempotency_key": "k1", "mode": "LIVE"},
    )

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["reasons"] == ["kill_switch", "safe_mode"]
    assert "kill switch" in detail["reason_text"][0].lower()
    assert store.get("PLAN-1")["status"] == "PUBLISHED"   # nothing was recorded as armed


# ----------------------------------------------------------- real admission


def _admit(monkeypatch, *, cfg=None, conditions=None, uid="u1"):
    from app.api.v1.endpoints import snapback_plans
    from app.engines.snapback import SnapbackConfig
    from app.services import snapback
    from app.services.snapback_readiness import LiveConditions

    monkeypatch.setattr(snapback, "get_config", lambda u=None: cfg or SnapbackConfig(enabled=True))
    monkeypatch.setattr("app.services.snapback_readiness.live_conditions",
                        lambda u, c=None, account_id="": conditions or LiveConditions())
    return snapback_plans._admission(mode="LIVE", account_id="KITE-1", uid=uid)


def test_admission_is_open_when_nothing_blocks_live_entry(monkeypatch):
    verdict = _admit(monkeypatch)

    assert verdict == {"allowed": True, "mode": "LIVE", "reasons": []}


def test_admission_carries_every_real_blocker(monkeypatch):
    from app.services.snapback_readiness import LiveConditions

    verdict = _admit(monkeypatch, conditions=LiveConditions(
        blockers=["kill_switch", "daily_loss_halt"], session=["session_closed"]))

    assert verdict["allowed"] is False
    assert verdict["reasons"] == ["kill_switch", "daily_loss_halt", "session_closed"]


def test_admission_refuses_a_switched_off_engine_and_an_unsupported_configuration(monkeypatch):
    from app.engines.snapback import SnapbackConfig

    off = _admit(monkeypatch, cfg=SnapbackConfig(enabled=False))
    spread = _admit(monkeypatch, cfg=SnapbackConfig(enabled=True, short_leg_delta=0.3))

    assert off["reasons"] == ["engine_disabled"]
    assert spread["reasons"] == ["spreads_not_supported_live"]


def test_admission_fails_closed_when_conditions_cannot_be_read(monkeypatch):
    from app.api.v1.endpoints import snapback_plans
    from app.engines.snapback import SnapbackConfig
    from app.services import snapback

    def boom(*a, **k):
        raise RuntimeError("store unreadable")

    monkeypatch.setattr(snapback, "get_config", lambda u=None: SnapbackConfig(enabled=True))
    monkeypatch.setattr("app.services.snapback_readiness.live_conditions", boom)

    verdict = snapback_plans._admission(mode="LIVE", account_id="KITE-1", uid="u1")

    assert verdict["allowed"] is False and "conditions_unreadable" in verdict["reasons"]


def test_any_other_mode_is_not_admitted():
    from app.api.v1.endpoints import snapback_plans

    assert snapback_plans._admission(mode="PAPER", account_id="A")["reasons"] == ["unsupported_mode"]


# -------------------------------------------------------- publishing a plan


def test_publishing_a_plan_hands_the_signal_id_to_the_dispatcher(store, monkeypatch):
    from app.services import snapback_dispatcher

    seen = []

    class FakeDispatcher:
        async def publish_from_signal(self, uid, signal_id):
            seen.append((uid, signal_id))
            return {"ok": True, "plan_id": "SBP-1", "revision": 1, "status": "PUBLISHED"}

    monkeypatch.setattr(snapback_dispatcher, "get_dispatcher", lambda: FakeDispatcher())
    client = _client(store, monkeypatch)

    response = client.post("/snapback/plans", json={"signal_id": "snapback:fade_up:NIFTY:1"})

    assert response.status_code == 200 and response.json()["plan_id"] == "SBP-1"
    assert seen == [("default", "snapback:fade_up:NIFTY:1")]


def test_a_refused_plan_says_why_and_an_unknown_signal_is_404(store, monkeypatch):
    from app.services import snapback_dispatcher

    class FakeDispatcher:
        async def publish_from_signal(self, uid, signal_id):
            if signal_id == "missing":
                return {"ok": False, "reasons": ["signal_not_found"]}
            return {"ok": False, "reasons": ["hedge_lots_zero"], "detail": "raw hedge 52 units"}

    monkeypatch.setattr(snapback_dispatcher, "get_dispatcher", lambda: FakeDispatcher())
    client = _client(store, monkeypatch)

    refused = client.post("/snapback/plans", json={"signal_id": "sig"})
    missing = client.post("/snapback/plans", json={"signal_id": "missing"})

    assert refused.status_code == 422
    assert refused.json()["detail"]["reasons"] == ["hedge_lots_zero"]
    assert missing.status_code == 404


def test_the_browser_supplies_a_signal_id_and_nothing_else():
    from app.api.v1.endpoints import snapback_plans

    assert set(snapback_plans.PublishRequest.model_fields) == {"signal_id"}
    assert set(snapback_plans.ArmRequest.model_fields) == {
        "expected_revision", "idempotency_key", "mode"}


def test_a_retried_arm_returns_the_same_result(store, monkeypatch):
    store.publish(**_plan())
    client = _client(store, monkeypatch)

    body = {"expected_revision": 1, "idempotency_key": "k1"}
    first = client.post("/snapback/plans/PLAN-1/arm", json=body)
    second = client.post("/snapback/plans/PLAN-1/arm", json=body)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["revision"] == first.json()["revision"]
