from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from app.api.v1.endpoints.ui_preferences import router
from app.core.auth import get_current_user, UserContext
from app.services import db

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'_DB_PATH',str(tmp_path/'preferences.db'))
    monkeypatch.setattr(db,'_available',True)
    app=FastAPI();app.include_router(router)
    app.dependency_overrides[get_current_user]=lambda:UserContext('alice')
    return TestClient(app),app

def test_isolation_restart_and_patch(client):
    c,app=client
    assert c.patch('/preferences',json={'values':{'sterling_theme':'dark','kite-settings':'{}'}}).status_code==200
    assert c.patch('/preferences',json={'values':{'sterling_theme':'light'}}).json()['values']['kite-settings']=='{}'
    assert c.get('/preferences').json()['values']['sterling_theme']=='light'
    app.dependency_overrides[get_current_user]=lambda:UserContext('bob')
    assert c.get('/preferences').json()['values']=={}
    app.dependency_overrides[get_current_user]=lambda:UserContext('alice')
    assert c.get('/preferences').json()['values']['sterling_theme']=='light'

def test_initialize_does_not_overwrite_and_tombstone(client):
    c,_=client
    c.patch('/preferences',json={'values':{'sterling_theme':'dark'},'initialize_only':True})
    assert c.patch('/preferences',json={'values':{'sterling_theme':'light'},'initialize_only':True}).json()['values']['sterling_theme']=='dark'
    c.patch('/preferences',json={'values':{'sterling_theme':None}})
    assert c.get('/preferences').json()['initialized'] is True
    assert c.get('/preferences').json()['values']['sterling_theme'] is None

def test_reject_credentials_limits_and_db_failure(client,monkeypatch):
    c,_=client
    assert c.patch('/preferences',json={'values':{'sterling.auth.access':'secret'}}).status_code==422
    assert c.patch('/preferences',json={'values':{'sterling_theme':'x'*200001}}).status_code==422
    monkeypatch.setattr(db,'_available',False)
    assert c.get('/preferences').status_code==503

def test_simulation_uses_owner(client):
    c,app=client
    c.patch('/preferences',json={'values':{'sterling_theme':'dark'}})
    app.dependency_overrides[get_current_user]=lambda:UserContext('sim:alice',view='simulation',owner_uid='alice')
    assert c.get('/preferences').json()['values']['sterling_theme']=='dark'
