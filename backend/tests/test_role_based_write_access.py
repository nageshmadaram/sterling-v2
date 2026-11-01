"""Persistence-backed role and workspace permission checks; no broker I/O."""
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.v1.endpoints.sterling_users import UserEdit
from app.core import tokens
from app.core.feature_access import enforce_feature_access
from app.services import db
from app.services.auth import account_policy, operator_credentials, user_store


@pytest.fixture
def identities(tmp_path, monkeypatch):
    monkeypatch.setattr(db, '_DB_PATH', str(tmp_path / 'roles.db'))
    assert db.init()
    user_store.clear()
    user_store._init_table()
    monkeypatch.setattr(user_store, '_loaded', True)
    user_store.add('primary', 'Synthetic-admin-password-123', role='admin', user_id='default')
    user_store.add('observer', 'Synthetic-viewer-password-123', role='viewer', user_id='viewer')
    user_store.add('executor', 'Synthetic-operator-password-123', role='operator', user_id='operator')
    for uid in ('viewer', 'operator'):
        account_policy.set_features(
            'default', uid, {'kite': True, 'tv': False, 'radio': False}, 0
        )
    yield
    user_store.clear()


def _client():
    app = FastAPI(dependencies=[Depends(enforce_feature_access)])

    @app.api_route('/api/v1/kite/role-smoke', methods=['GET', 'POST', 'DELETE'])
    async def probe():
        return {'ok': True}

    return TestClient(app)


def _headers(uid):
    person = user_store.get_by_id(uid)
    token = tokens.mint_access(uid, person.username, person.role, person.token_version)
    return {'Authorization': 'Bearer ' + token}


def test_viewer_can_read_but_cannot_mutate_kite_even_with_feature_grant(identities):
    client = _client()
    with client:
        for method in ('GET', 'POST', 'DELETE'):
            r = client.request(method, '/api/v1/kite/role-smoke', headers=_headers('viewer'))
            assert r.status_code == (200 if method == 'GET' else 403)
        assert client.post('/api/v1/kite/role-smoke', headers=_headers('operator')).status_code == 200
    assert account_policy.entry_refusal('viewer') == 'Viewer/unknown role has no order entry authority.'
    assert account_policy.entry_refusal('operator') is None
    assert account_policy.read('viewer').role == 'viewer'


def test_role_change_revokes_old_session_and_preserves_audit(identities):
    old = _headers('viewer')
    changed = operator_credentials.manage(
        'default', 'update', 'observer', '', target_id='viewer', role='operator'
    )
    assert changed.role == 'operator'
    assert account_policy.read('viewer').role == 'operator'
    with _client() as client:
        assert client.post('/api/v1/kite/role-smoke', headers=old).status_code == 401
        assert client.post('/api/v1/kite/role-smoke', headers=_headers('viewer')).status_code == 200
    with db._conn() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM auth_audit_events WHERE action='user.update' AND target_id='viewer'"
        ).fetchone()[0] == 1
    assert user_store.get_by_id('viewer').token_version == 2


def test_viewer_cannot_be_promoted_to_second_admin(identities):
    with pytest.raises(ValueError, match='one designated'):
        operator_credentials.manage('default', 'update', 'observer', '',
                                    target_id='viewer', role='admin')


def test_unknown_role_rejected_by_api_contract_and_store(identities):
    with pytest.raises(ValidationError):
        UserEdit(username='observer', role='superuser')
    with pytest.raises(ValueError, match='Invalid role'):
        user_store.add('arbitrary', 'Synthetic-password-123', role='superuser')


def test_viewer_cannot_use_delegated_broker_configuration_write(identities):
    from app.services.auth import configuration_policy
    grants = [{'action': 'update', 'resource_id': 'own', 'expires_at_ms': None}]
    configuration_policy.replace_section(
        'default', 'viewer', 'brokers', visible=True,
        grants=grants, expected_revision=1
    )
    assert configuration_policy.read('viewer')['sections']['brokers']['visible'] is True
    with pytest.raises(PermissionError, match='Viewer/unknown'):
        configuration_policy.require('viewer', 'brokers', action='update', resource_id='own')

    configuration_policy.replace_section(
        'default', 'operator', 'brokers', visible=True,
        grants=grants, expected_revision=1
    )
    assert configuration_policy.require(
        'operator', 'brokers', action='update', resource_id='own'
    )['active'] is True
