"""Phase 8 owner-scoped GET-only strategy catalogue / audit-verified gate status."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.test_offline_scheduler_phase5 import lab, schedule, policy

from app.api.v1.endpoints.offline_observability import router
from app.persistence import strategy_registry_service as registry
from app.domain.execution_validation import ApprovalMode, GateState


def test_offline_catalogue_requires_signed_actor_and_uses_existing_registry(lab):
    app=FastAPI()
    app.include_router(router,prefix="/api/v1")
    client=TestClient(app)
    base="/api/v1/offline-validation/strategies"
    assert client.get(base).status_code==401
    assert client.post(base,headers={"Authorization":"Bearer admin"}).status_code==405
    own=client.get(base,headers={"Authorization":"Bearer admin"})
    assert own.status_code==200,own.text
    payload=own.json()
    assert payload["read_only"] is True and payload["execution_authorized"] is False
    assert payload["source"]=="offline_versioned_registry"
    assert [x["id"] for x in payload["strategies"]]==[lab.strategy]
    other=client.get(base,headers={"Authorization":"Bearer outside"})
    assert other.status_code==200 and other.json()["strategies"]==[]
    assert all(set(route.methods)=={"GET"} for route in router.routes)


def test_offline_strategy_status_verified_gate_states_no_health_score(lab):
    app=FastAPI()
    app.include_router(router,prefix="/api/v1")
    client=TestClient(app)
    base=f"/api/v1/offline-validation/strategies/{lab.strategy}/status"
    offline_spec=schedule(lab)
    lab.service.schedule(access_token="admin",spec=offline_spec,risk_policy=policy(),
                         reason="offline read-only schedule fixture")
    lab.service.set_pause(access_token="admin",strategy_id=lab.strategy,
                          schedule_id=offline_spec.schedule_id,
                          paused=True,reason="offline operator pause")
    unauthorized=client.get(base,headers={"Authorization":"Bearer outside"})
    assert unauthorized.status_code==403
    response=client.get(base,headers={"Authorization":"Bearer admin"})
    assert response.status_code==200,response.text
    data=response.json()
    assert data["registry"]["id"]==lab.strategy
    assert data["offline_gate"]["system_state"]=="disabled"
    assert data["offline_gate"]["strategy_state"]=="disabled"
    assert data["offline_gate"]["live_execution_enabled"] is False
    assert data["offline_gate"]["scope"]=="offline_validation_only"
    assert data["strategy_health"]["score"] is None
    assert data["execution_authorized"] is False
    assert len(data["schedules"])==1
    assert data["schedules"][0]["kind"]=="manual"
    assert data["schedules"][0]["state"]=="paused"
    assert data["schedules"][0]["execution_authorized"] is False
    assert len(data["versions"])==1
    assert data["versions"][0]["id"]==lab.version

    lab.gate.transition(access_token="admin",strategy_id=None,
                        new_state=GateState.EMERGENCY_STOP,
                        approval_mode=ApprovalMode.BLOCKED,reason="synthetic offline stop")
    after=client.get(base,headers={"Authorization":"Bearer admin"})
    assert after.status_code==200
    assert after.json()["offline_gate"]["system_state"]=="emergency_stop"
    assert after.json()["execution_authorized"] is False
