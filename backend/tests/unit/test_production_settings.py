from types import SimpleNamespace
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.services.production_settings import ProductionSettings, read_settings, save_settings


def _complete_risk_limits() -> str:
    from app.core.horizon import LaneState
    from app.core.lane_registry import all_lanes

    lanes = [
        lane for lane in all_lanes()
        if lane.state in (LaneState.LIVE_MINIMUM, LaneState.LIVE_SCALED)
        and lane.rules_defined
    ]
    return json.dumps({
        "global": {"": 50000},
        "strategy": {lane.strategy_id: 20000 for lane in lanes},
        "mode": {lane.lane_key: 10000 for lane in lanes},
        "position": {lane.lane_key: 2000 for lane in lanes},
    })


def test_roundtrip_preserves_secrets_and_does_not_change_runtime(tmp_path, monkeypatch):
    path = tmp_path / '.env'
    path.write_text('# existing\nUNRELATED_SECRET=keep-this\nSTERLING_LIVE_EXECUTION=false\n')
    monkeypatch.setenv('STERLING_LIVE_EXECUTION', 'false')
    cfg = ProductionSettings(allocation_capital_inr=50000, risk_limits='{"global":{"":50000}}')
    save_settings(cfg, path)
    assert read_settings(path) == cfg
    assert 'UNRELATED_SECRET=keep-this' in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600
    import os
    assert os.environ['STERLING_LIVE_EXECUTION'] == 'false'


@pytest.mark.parametrize('change', [
    {'allocation_capital_inr': -1}, {'allocation_capital_inr': float('inf')},
    {'experiment_id': 'bad\nINJECT=value'}, {'dataset_start': '2026-10-19'},
    {'risk_limits': '{}'}, {'risk_limits': '{"global":{"":NaN}}'},
    {'risk_limits': '{"global":{"":-5}}'}, {'risk_limits': '{"global":{"":null}}'},
    {'unknown_secret': 'value'},
])
def test_rejects_invalid_settings(change):
    with pytest.raises(ValidationError):
        ProductionSettings(**change)


@pytest.fixture
def client(monkeypatch, tmp_path):
    from app.api.v1.endpoints import ops_live
    from app.core.auth import require_admin
    from app.services import live_posture, live_runtime, production_settings
    path = tmp_path / '.env'
    monkeypatch.setattr(production_settings, 'settings_path', lambda: path)
    monkeypatch.setattr(live_posture, 'posture_status', lambda **kw: {'ready': False, 'missing': ['static IP not verified']})
    monkeypatch.setattr(live_runtime, '_safe_mode_service', lambda: SimpleNamespace(read=lambda: SimpleNamespace(active=False)))
    app = FastAPI()
    app.include_router(ops_live.router)
    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(user_id='operator')
    return TestClient(app), path


def test_enable_refused_without_confirmation_or_broker_proof(client):
    api, path = client
    settings = ProductionSettings(live_execution=True, allocation_capital_inr=50000,
                                  risk_limits='{"global":{"":50000}}').model_dump()
    assert api.put('/ops/production-settings', json={'settings': settings}).status_code == 400
    result = api.put('/ops/production-settings', json={'settings': settings, 'confirm': 'ENABLE LIVE ENTRIES'})
    assert result.status_code == 409
    assert 'static IP' in result.json()['detail']
    assert not path.exists()


def test_saves_disabled_setup_while_blockers_remain(client):
    api, path = client
    result = api.put('/ops/production-settings', json={'settings': ProductionSettings(allocation_capital_inr=50000).model_dump()})
    assert result.status_code == 200
    assert result.json()['restart_required'] is True
    assert read_settings(path).allocation_capital_inr == 50000


def test_enable_refused_while_safe_mode_is_active(client, monkeypatch):
    from app.services import live_posture, live_runtime
    api, path = client
    monkeypatch.setattr(live_posture, 'posture_status', lambda **kw: {'ready': True, 'missing': []})
    monkeypatch.setattr(live_runtime, '_safe_mode_service', lambda: SimpleNamespace(read=lambda: SimpleNamespace(active=True)))
    response = api.put('/ops/production-settings', json={
        'settings': ProductionSettings(live_execution=True, allocation_capital_inr=50000,
            risk_limits='{"global":{"":50000}}').model_dump(),
        'confirm': 'ENABLE LIVE ENTRIES',
    })
    assert response.status_code == 409
    assert 'safe mode' in response.json()['detail']
    assert not path.exists()


def test_enable_requires_allocation_and_limits(client, monkeypatch):
    from app.services import live_posture
    api, path = client
    monkeypatch.setattr(live_posture, 'posture_status', lambda **kw: {'ready': True, 'missing': []})
    response = api.put('/ops/production-settings', json={
        'settings': {'live_execution': True}, 'confirm': 'ENABLE LIVE ENTRIES',
    })
    assert response.status_code == 409
    assert not path.exists()


def test_non_admin_cannot_read_or_change_global_settings():
    from app.api.v1.endpoints.ops_live import router
    from app.core.auth import UserContext, get_current_user
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: UserContext(user_id='reader', username='reader', role='user')
    api = TestClient(app)
    assert api.get('/ops/production-settings').status_code == 403
    assert api.put('/ops/production-settings', json={'settings': {}}).status_code == 403


def test_admin_snapshot_is_allowlisted_and_includes_checks(client, monkeypatch):
    from app.services import live_runtime, snapback_preflight
    from app.core import operator_report
    api, path = client
    path.write_text('STERLING_SECRET_KEY=never-return-this\nSTERLING_ALLOCATION_CAPITAL_INR=50000\n')
    monkeypatch.setattr(live_runtime, 'live_execution_state', lambda: {'enabled': False})
    monkeypatch.setattr(live_runtime, '_safe_mode_service', lambda: SimpleNamespace(
        read=lambda: SimpleNamespace(as_dict=lambda: {'state': 'NORMAL'})))
    monkeypatch.setattr(snapback_preflight, 'run_preflight', lambda: SimpleNamespace(checks=[]))
    monkeypatch.setattr(operator_report, 'lane_doctor_checks', lambda: [])
    response = api.get('/ops/production-settings')
    assert response.status_code == 200
    assert response.json()['settings']['allocation_capital_inr'] == 50000
    assert response.json()['checks'] == []
    assert 'never-return-this' not in response.text
    assert 'STERLING_SECRET_KEY' not in response.text


def test_enable_rejects_global_only_risk_cap_even_with_ready_broker(client, monkeypatch):
    from app.services import live_posture

    api, path = client
    monkeypatch.setattr(live_posture, 'posture_status', lambda **kw: {'ready': True, 'missing': []})
    response = api.put('/ops/production-settings', json={
        'settings': ProductionSettings(
            live_execution=True, allocation_capital_inr=50000,
            risk_limits='{"global":{"":50000}}',
        ).model_dump(),
        'confirm': 'ENABLE LIVE ENTRIES',
    })
    assert response.status_code == 409
    assert 'strategy:' in response.json()['detail']
    assert 'position:' in response.json()['detail']
    assert not path.exists()


def test_enable_rejects_one_missing_position_cap(client, monkeypatch):
    from app.services import live_posture

    api, path = client
    monkeypatch.setattr(live_posture, 'posture_status', lambda **kw: {'ready': True, 'missing': []})
    limits = json.loads(_complete_risk_limits())
    del limits['position']['supertrend:intraday']
    response = api.put('/ops/production-settings', json={
        'settings': ProductionSettings(
            live_execution=True, allocation_capital_inr=50000,
            risk_limits=json.dumps(limits),
        ).model_dump(),
        'confirm': 'ENABLE LIVE ENTRIES',
    })
    assert response.status_code == 409
    assert 'position:supertrend:intraday' in response.json()['detail']
    assert not path.exists()


def test_verified_enable_is_saved_for_restart_only(client, monkeypatch):
    import os
    from app.services import live_posture
    api, path = client
    monkeypatch.setattr(live_posture, 'posture_status', lambda **kw: {'ready': True, 'missing': []})
    monkeypatch.setenv('STERLING_LIVE_EXECUTION', 'false')
    response = api.put('/ops/production-settings', json={
        'settings': ProductionSettings(live_execution=True, allocation_capital_inr=50000,
            risk_limits=_complete_risk_limits()).model_dump(),
        'confirm': 'ENABLE LIVE ENTRIES',
    })
    assert response.status_code == 200
    assert read_settings(path).live_execution is True
    assert os.environ['STERLING_LIVE_EXECUTION'] == 'false'


def test_settings_require_authentication(monkeypatch):
    monkeypatch.setenv('ENVIRONMENT', 'production')
    from app.api.v1.endpoints.ops_live import router
    app = FastAPI()
    app.include_router(router)
    api = TestClient(app)
    assert api.get('/ops/production-settings').status_code in (401, 403)
    assert api.put('/ops/production-settings', json={'settings': {}}).status_code in (401, 403)
