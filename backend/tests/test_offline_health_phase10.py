"""Phase 10 manually sampled health ledger; no broker/worker calls."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.persistence.offline_health_service import OfflineHealthRecorder
from app.persistence.session import make_session_factory
from app.offline_security_profile import create_app
from tests.test_offline_scheduler_phase5 import lab


def test_offline_health_snapshots_only_report_known_sources(lab):
    recorder=OfflineHealthRecorder(make_session_factory(lab.engine))
    with pytest.raises(PermissionError):
        recorder.sample_once(access_token="operator",strategy_id=lab.strategy)
    event=recorder.sample_once(access_token="admin",strategy_id=lab.strategy)
    result=recorder.read(access_token="operator",strategy_id=lab.strategy)
    assert result["execution_authorized"] is False and len(result["snapshots"])==1
    snapshot=result["snapshots"][0]
    assert snapshot["event_id"]==event and len(snapshot["audit_ref"])==64
    status=snapshot["status"]
    assert status["broker"]=="not_connected"
    assert status["broker_exposure"]=="unknown"
    assert status["market_data"]=="not_independently_probed"
    assert status["worker"]=="disabled"
    assert status["production_ready"] is False
    assert status["queue"]=="verified_offline"
    assert status["alerts"]=="verified_offline"
    assert recorder.read(access_token="admin",strategy_id=lab.strategy,limit=2)==result
    with pytest.raises(PermissionError):
        recorder.read(access_token="outside",strategy_id=lab.strategy)


def test_offline_health_immutable_and_prevent_authority(lab):
    recorder=OfflineHealthRecorder(make_session_factory(lab.engine))
    recorder.sample_once(access_token="admin",strategy_id=lab.strategy)
    for stmt in ("UPDATE platform_offline_health_events SET report_json='{}'",
                 "DELETE FROM platform_offline_health_events",
                 "UPDATE platform_offline_health_events SET execution_authorized=1"):
        with pytest.raises(DBAPIError):
            with lab.engine.begin() as conn: conn.execute(text(stmt))
    assert len(recorder.read(access_token="admin",strategy_id=lab.strategy)["snapshots"])==1


def test_offline_health_insert_outage_rolls_back(lab):
    recorder=OfflineHealthRecorder(make_session_factory(lab.engine))
    with lab.engine.begin() as conn:
        conn.execute(text("CREATE TRIGGER phase10_health_down BEFORE INSERT ON platform_offline_health_events "
                          "BEGIN SELECT RAISE(ABORT,'offline health outage'); END"))
    with pytest.raises(DBAPIError):
        recorder.sample_once(access_token="admin",strategy_id=lab.strategy)
    assert recorder.read(access_token="admin",strategy_id=lab.strategy)["snapshots"]==[]
    with lab.engine.begin() as conn: conn.execute(text("DROP TRIGGER phase10_health_down"))
    assert recorder.sample_once(access_token="admin",strategy_id=lab.strategy)


def test_health_history_signed_owner_scoped(lab):
    recorder=OfflineHealthRecorder(make_session_factory(lab.engine))
    recorder.sample_once(access_token="admin",strategy_id=lab.strategy)
    client=TestClient(create_app())
    url=f"/api/v1/offline-validation/strategies/{lab.strategy}/health-history"
    assert client.get(url,headers={"Authorization":"Bearer operator"}).status_code==200
    assert client.get(url,headers={"Authorization":"Bearer outside"}).status_code==403
    assert client.post(url,headers={"Authorization":"Bearer operator"}).status_code==405
    assert client.get(url+"?limit=501",headers={"Authorization":"Bearer operator"}).status_code==422
