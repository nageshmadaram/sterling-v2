"""Phase 9 disposable offline alert certification, no transport network."""
from datetime import datetime, timezone
from time import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.test_offline_scheduler_phase5 import lab,schedule,policy
from app.api.v1.endpoints.offline_alerts import router
from app.persistence.session import make_session_factory
from app.persistence.offline_alert_service import (
    OfflineAlertService,Category,Channel,Transport,MAX_ATTEMPTS,
)
from app.persistence import strategy_registry_service as registry


@pytest.fixture
def svc(lab):
    return OfflineAlertService(make_session_factory(lab.engine))


@pytest.fixture
def source(lab):
    spec=schedule(lab)
    event=lab.service.schedule(access_token="admin",spec=spec,risk_policy=policy(),
                               reason="synthetic source schedule")
    return event


def created(svc,lab,source,category=Category.SCHEDULING_FAILURE,**kwargs):
    return svc.create(access_token="admin",strategy_id=lab.strategy,
                      source_type="queue",source_ref=source,
                      source_system="offline_scheduler",message="Offline schedule skipped",
                      category=category,reason="verified offline evidence alert",**kwargs)


def test_create_severity_source_signature_and_replay(lab,svc,source):
    event=created(svc,lab,source)
    assert event["duplicate_suppressed"] is False
    assert created(svc,lab,source)=={"alert_id":event["alert_id"],"duplicate_suppressed":True}
    items=svc.list(access_token="admin",strategy_id=lab.strategy)
    assert len(items)==1
    assert items[0]["priority"]=="warning" and items[0]["state"]=="active"
    assert items[0]["source_sha256"] and len(items[0]["audit_ref"])==64
    assert items[0]["history"][0]["kind"]=="created"
    assert items[0]["execution_authorized"] is False
    with pytest.raises(PermissionError):
        svc.list(access_token="outside",strategy_id=lab.strategy)


def test_priority_info_critical_and_dedupe(lab,svc,source):
    for cat,expected in [(Category.INFORMATIONAL_UPDATE,"info"),
                         (Category.AUDIT_EXCEPTION,"critical"),
                         (Category.INVALID_DECISION_TRAIL,"critical")]:
        created(svc,lab,source,category=cat)
        alerts=svc.list(access_token="admin",strategy_id=lab.strategy)
        assert any(a["category"]==cat.value and a["priority"]==expected for a in alerts)
    assert len(svc.list(access_token="admin",strategy_id=lab.strategy))==3


def test_missing_unverified_source_fails_closed(lab,svc):
    with pytest.raises(PermissionError):
        created(svc,lab,"unknown-event")
    assert svc.list(access_token="admin",strategy_id=lab.strategy)==()


def test_ack_is_metadata_only_idempotent_and_audited(lab,svc,source):
    aid=created(svc,lab,source)["alert_id"]
    first=svc.change_state(access_token="operator",strategy_id=lab.strategy,alert_id=aid,
                           action="acknowledged",reason="Reviewed offline scheduler evidence")
    assert first["changed"] and first["state"]=="acknowledged"
    again=svc.change_state(access_token="operator",strategy_id=lab.strategy,alert_id=aid,
                           action="acknowledged",reason="Duplicate review")
    assert again["changed"] is False
    rows=svc.list(access_token="admin",strategy_id=lab.strategy)
    assert rows[0]["state"]=="acknowledged"
    assert rows[0]["history"][-1]["actor"]=="owner"
    assert rows[0]["history"][-1]["kind"]=="acknowledged"
    with pytest.raises(PermissionError):
        svc.change_state(access_token="operator",strategy_id=lab.strategy,alert_id=aid,
                         action="resolved",reason="Cannot resolve as operator")


def test_resolution_terminal_and_expiry(lab,svc,source):
    aid=created(svc,lab,source)["alert_id"]
    resolved=svc.change_state(access_token="admin",strategy_id=lab.strategy,alert_id=aid,
                              action="resolved",reason="Reviewed offline condition")
    assert resolved["state"]=="resolved"
    assert svc.list(access_token="admin",strategy_id=lab.strategy,unresolved_only=True)==()
    with pytest.raises(PermissionError):
        svc.change_state(access_token="admin",strategy_id=lab.strategy,alert_id=aid,
                         action="acknowledged",reason="Late review")
    a2=created(svc,lab,source,category=Category.STALE_MARKET_DATA,
               expires_at_ms=int(time()*1000)+20_000)["alert_id"]
    assert svc.expire_due(access_token="admin",strategy_id=lab.strategy,at_ms=int(time()*1000)+21_000)==1
    items={x["alert_id"]:x for x in svc.list(access_token="admin",strategy_id=lab.strategy)}
    assert items[a2]["state"]=="expired"
    assert items[aid]["state"]=="resolved"


def test_audit_immutability_and_forced_insert_rollback(lab,svc,source):
    aid=created(svc,lab,source)["alert_id"]
    for stmt in ("UPDATE platform_offline_alert_events SET reason='forge'",
                 "DELETE FROM platform_offline_alert_events",
                 "UPDATE platform_offline_alert_events SET execution_authorized=1"):
        with pytest.raises(DBAPIError):
            with lab.engine.begin() as db: db.execute(text(stmt))
    with lab.engine.begin() as db:
        db.execute(text("CREATE TRIGGER phase9_outage BEFORE INSERT ON platform_offline_alert_events "
                        "BEGIN SELECT RAISE(ABORT,'synthetic journal outage'); END"))
    with pytest.raises(DBAPIError):
        svc.change_state(access_token="admin",strategy_id=lab.strategy,alert_id=aid,
                         action="acknowledged",reason="Should not persist")
    assert svc.list(access_token="admin",strategy_id=lab.strategy)[0]["state"]=="active"
    with lab.engine.begin() as db: db.execute(text("DROP TRIGGER phase9_outage"))
    assert svc.change_state(access_token="admin",strategy_id=lab.strategy,alert_id=aid,
                            action="acknowledged",reason="Audit restored")["changed"]


def test_dashboard_sent_idempotence_and_receipt_confirmation(lab,svc,source):
    aid=created(svc,lab,source)["alert_id"]
    sent=svc.delivery(access_token="admin",strategy_id=lab.strategy,
                      alert_id=aid,channel=Channel.DASHBOARD)
    assert sent["state"]=="sent"
    assert svc.delivery(access_token="admin",strategy_id=lab.strategy,
                        alert_id=aid,channel=Channel.DASHBOARD)["changed"] is False
    assert svc.confirm_receipt(access_token="admin",strategy_id=lab.strategy,
                               alert_id=aid,channel=Channel.DASHBOARD,
                               reason="Operator saw dashboard alert")
    row=svc.list(access_token="admin",strategy_id=lab.strategy)[0]
    assert row["delivery"]["dashboard"]["state"]=="acknowledged"
    assert [x["kind"] for x in row["history"]]==[
        "created","delivery_pending","delivery_sent","delivery_acknowledged"]


def test_external_disabled_transport_fails_closed(lab,svc,source):
    aid=created(svc,lab,source)["alert_id"]
    with pytest.raises(PermissionError):
        svc.delivery(access_token="admin",strategy_id=lab.strategy,
                     alert_id=aid,channel=Channel.TELEGRAM)
    with pytest.raises(PermissionError):
        svc.delivery(access_token="operator",strategy_id=lab.strategy,
                     alert_id=aid,channel=Channel.WHATSAPP)
    assert svc.list(access_token="admin",strategy_id=lab.strategy)[0]["delivery"]=={}


def test_external_failed_attempt_backoff_and_retries(lab,svc,source,monkeypatch):
    class TestSink(Transport):
        def send(self,*,alert_id,message,idempotency_key):
            raise ConnectionError("synthetic offline channel outage")
    aid=created(svc,lab,source)["alert_id"]
    sink=TestSink(Channel.TELEGRAM,True,True,True)
    assert svc.delivery(access_token="admin",strategy_id=lab.strategy,alert_id=aid,
                        channel=Channel.TELEGRAM,transport=sink)["state"]=="failed"
    row=svc.list(access_token="admin",strategy_id=lab.strategy)[0]
    assert row["delivery"]["telegram"]["state"]=="failed"
    assert row["delivery"]["telegram"]["attempt"]==1
    with pytest.raises(PermissionError):
        svc.delivery(access_token="admin",strategy_id=lab.strategy,alert_id=aid,
                     channel=Channel.TELEGRAM,transport=sink)
    # Synthetic time advance, no background scheduler and no real messages.
    future=row["delivery"]["telegram"]["retry_after_ms"]+1
    from app.persistence import offline_alert_service
    monkeypatch.setattr(offline_alert_service.time,"time",lambda:future/1000)
    assert svc.delivery(access_token="admin",strategy_id=lab.strategy,alert_id=aid,
                        channel=Channel.TELEGRAM,transport=sink)["state"]=="failed"
    assert svc.list(access_token="admin",strategy_id=lab.strategy)[0]["delivery"]["telegram"]["attempt"]==2


def test_ambiguous_pending_not_blind_retried(lab,svc,source):
    from app.persistence.offline_alert_service import _events,_append
    from app.persistence.session import session_scope
    aid=created(svc,lab,source)["alert_id"]
    with session_scope(make_session_factory(lab.engine)) as db:
        rows=_events(db,lab.strategy)
        _append(db,rows,actor=registry._identity("admin"),alert_id=aid,
                strategy_id=lab.strategy,kind="delivery_pending",
                reason="simulate transport accepted but DB crashed",payload={"channel":"email","attempt":1})
    row=svc.list(access_token="admin",strategy_id=lab.strategy)[0]
    assert row["delivery"]["email"]["state"]=="pending"
    class TestSink(Transport):
        def send(self,**kwargs): return "accepted"
    with pytest.raises(PermissionError):
        svc.delivery(access_token="admin",strategy_id=lab.strategy,alert_id=aid,
                     channel=Channel.EMAIL,transport=TestSink(Channel.EMAIL,True,True,True))


def test_explicit_escalation_is_information_only_and_one_time(lab,svc,source):
    aid=created(svc,lab,source,category=Category.EMERGENCY_STOP)["alert_id"]
    now=int(time()*1000)
    assert svc.escalate_due(access_token="admin",strategy_id=lab.strategy,
                            at_ms=now+400000,critical_after_ms=300000)==1
    assert svc.escalate_due(access_token="admin",strategy_id=lab.strategy,
                            at_ms=now+500000,critical_after_ms=300000)==0
    row=svc.list(access_token="admin",strategy_id=lab.strategy)[0]
    assert row["escalated"] is True and row["execution_authorized"] is False


def test_http_signed_reads_owner_ack_admin_resolve_and_no_dispatch_route(lab,svc,source):
    aid=created(svc,lab,source)["alert_id"]
    client=TestClient(FastAPI())
    client.app.include_router(router,prefix="/api/v1")
    root=f"/api/v1/offline-validation/strategies/{lab.strategy}/alerts"
    assert client.get(root).status_code==401
    assert client.get(root,headers={"Authorization":"Bearer outside"}).status_code==403
    response=client.get(root+"?unresolved_only=true",headers={"Authorization":"Bearer operator"})
    assert response.status_code==200,response.text
    assert response.json()["execution_authorized"] is False
    assert response.json()["items"][0]["alert_id"]==aid
    assert client.post(root+"/"+aid+"/acknowledge",json={"reason":"viewed"},
                       headers={"Authorization":"Bearer operator"}).status_code==200
    denied=client.post(root+"/"+aid+"/resolve",json={"reason":"operator resolving"},
                       headers={"Authorization":"Bearer operator"})
    assert denied.status_code==403
    ok=client.post(root+"/"+aid+"/resolve",json={"reason":"admin reviewed alert"},
                   headers={"Authorization":"Bearer admin"})
    assert ok.status_code==200 and ok.json()["execution_authorized"] is False
    assert client.post(root,headers={"Authorization":"Bearer admin"}).status_code==405
    assert all(set(r.methods) in ({"GET"},{"POST"}) for r in router.routes)
    assert len([r for r in router.routes if "POST" in r.methods])==2
