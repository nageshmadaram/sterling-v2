import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('ema_scan_audit', Path(__file__).resolve().parents[2]/'scripts/ema_scan_audit.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def test_exact_previously_failed_pairs_require_later_completed_success():
    pair={'symbol':'NSE:TEST','mode':'swing'}
    failed={'id':'old','updated_ms':1,'status':'FAILED','errors':[{**pair,'reason':'HISTORICAL_BAR_RESTATED_RECONCILIATION_REQUIRED'}]}
    for status in ['RUNNING','FAILED','CANCELLED']:
        assert audit.compare([failed,{'updated_ms':2,'status':status,'results':[pair]}])['status']=='PENDING'
    result=audit.compare([failed,{'id':'new','updated_ms':3,'status':'COMPLETED','results':[pair],'errors':[]}])
    assert result['status']=='RESOLVED' and result['historical_pair_count']==1
    assert result['resolved_pairs']==[pair] and result['unresolved_pairs']==[]


def test_latest_completed_job_cannot_hide_a_regression_behind_an_older_pass():
    pair={'symbol':'NSE:TEST','mode':'swing'}
    scans=[{'updated_ms':1,'errors':[{**pair,'reason':'HISTORICAL_BAR_RESTATED_RECONCILIATION_REQUIRED'}]},
           {'updated_ms':2,'status':'COMPLETED','results':[pair]},
           {'updated_ms':3,'status':'COMPLETED','results':[],'errors':[pair]}]
    result=audit.compare(scans)
    assert result['status']=='PENDING' and result['unresolved_pairs']==[pair]


def test_absent_historical_evidence_is_unavailable():
    assert audit.compare([])['status']=='UNAVAILABLE'


def test_real_audit_opens_existing_database_without_mutation(tmp_path):
    import sqlite3,hashlib
    path=tmp_path/'scans.sqlite'
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE ema_trend_documents(owner_uid TEXT,kind TEXT,updated_ms INTEGER,data_json TEXT)')
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    result=audit.audit(path)
    assert result['database_read_only'] and result['status']=='UNAVAILABLE'
    assert before==hashlib.sha256(path.read_bytes()).hexdigest()


def test_success_from_a_different_owner_never_resolves_the_failed_owner():
    pair={'symbol':'NSE:TEST','mode':'swing'}
    scans=[{'owner_uid':'alice','updated_ms':1,'status':'FAILED','errors':[{**pair,'reason':'HISTORICAL_BAR_RESTATED_RECONCILIATION_REQUIRED'}]},
           {'owner_uid':'bob','updated_ms':2,'status':'COMPLETED','results':[pair]}]
    result=audit.compare(scans)
    assert result['status']=='PENDING' and result['unresolved_pairs']==[pair]


def test_newer_failed_terminal_scan_does_not_inherit_an_older_completed_pass():
    pair={'symbol':'NSE:TEST','mode':'swing'}
    scans=[{'id':'historical','updated_ms':1,'status':'FAILED','errors':[{**pair,'reason':'HISTORICAL_BAR_RESTATED_RECONCILIATION_REQUIRED'}]},
           {'id':'passed','updated_ms':2,'status':'COMPLETED','results':[pair]},
           {'id':'failed-later','updated_ms':3,'status':'FAILED','results':[], 'errors':[{**pair,'reason':'PROVIDER_UNAVAILABLE'}]}]
    result=audit.compare(scans)
    assert result['status']=='PENDING' and result['unresolved_pairs']==[pair]
    assert result['latest_terminal_job']=='failed-later' and result['latest_terminal_status']=='FAILED'


def test_database_owner_filter_and_embedded_owner_cannot_be_spoofed(tmp_path):
    import json,sqlite3,hashlib
    pair={'symbol':'NSE:TEST','mode':'swing'}
    path=tmp_path/'scans.sqlite'
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE ema_trend_documents(owner_uid TEXT,kind TEXT,updated_ms INTEGER,data_json TEXT)')
        c.executemany('INSERT INTO ema_trend_documents VALUES (?, ?, ?, ?)', [
            ('alice','scan',1,json.dumps({'owner_uid':'bob','id':'old','status':'FAILED','errors':[{**pair,'reason':'HISTORICAL_BAR_RESTATED_RECONCILIATION_REQUIRED'}]})),
            ('bob','scan',2,json.dumps({'owner_uid':'alice','status':'COMPLETED','results':[pair]}))])
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    assert audit.audit(path,owner='alice')['status']=='PENDING'
    assert audit.audit(path,owner='bob')['status']=='UNAVAILABLE'
    assert audit.audit(path)['status']=='UNAVAILABLE'
    assert before==hashlib.sha256(path.read_bytes()).hexdigest()
