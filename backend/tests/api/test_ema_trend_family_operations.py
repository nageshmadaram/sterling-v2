"""Paper operations are authenticated inventory, never real broker positions."""
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from app.core.auth import require_admin
from app.api.v1.endpoints.family_operations import router


@pytest.mark.parametrize("authenticated",["alice","sim:alice"])
def test_family_paper_section_uses_authenticated_owner_and_preserves_real_broker(monkeypatch,authenticated):
    from app.services import family_operations_v2 as aggregate
    from app.services.ema_trend import snapshot
    from app.services.simulation.identity import sim_uid
    uid=sim_uid("alice") if authenticated.startswith("sim:") else authenticated
    broker={"managed_positions":[{"instrument":"REAL_POSITION"}]}
    monkeypatch.setattr(aggregate,"family_operations_v2",lambda:{"generated_at":"2026-10-17T00:00:00Z","broker":broker})
    calls=[]
    def paper(owner):
        calls.append(owner)
        return {"status":"AVAILABLE","campaigns":[{"id":"virtual-only"}],"execution_mode":"PAPER","live_eligible":False}
    monkeypatch.setattr(snapshot,"operations",paper)
    app=FastAPI();app.include_router(router,prefix="/api/v1")
    app.dependency_overrides[require_admin]=lambda:SimpleNamespace(user_id=uid)
    response=TestClient(app).get("/api/v1/operations/family?uid=bob")
    assert response.status_code==200
    payload=response.json()
    assert calls==["alice"]
    assert payload["paper_strategies"]["ema_trend"]["live_eligible"] is False
    assert payload["broker"]["managed_positions"]==[{"instrument":"REAL_POSITION","managed_by_sterling":True}]
    assert response.headers["cache-control"]=="no-store"


def test_unreadable_paper_state_is_unknown_not_empty_inventory(monkeypatch):
    from app.services import family_operations_v2 as aggregate
    from app.services.ema_trend import snapshot
    monkeypatch.setattr(aggregate,"family_operations_v2",lambda:{"generated_at":"2026-10-17T00:00:00Z"})
    def down(owner):raise RuntimeError("store unavailable")
    monkeypatch.setattr(snapshot,"operations",down)
    app=FastAPI();app.include_router(router,prefix="/api/v1")
    app.dependency_overrides[require_admin]=lambda:SimpleNamespace(user_id="alice")
    response=TestClient(app).get("/api/v1/operations/family")
    assert response.status_code==200
    paper=response.json()["paper_strategies"]["ema_trend"]
    assert paper["status"]=="UNAVAILABLE" and paper["campaigns"] is None
    assert paper["supervision"] is None and paper["live_eligible"] is False
