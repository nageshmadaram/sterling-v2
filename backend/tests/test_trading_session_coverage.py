import importlib.util
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

spec=importlib.util.spec_from_file_location('session_audit',Path(__file__).resolve().parents[2]/'scripts/trading_session_audit.py')
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)


def samples():
    start=datetime(2026,10,23,9,14,30,tzinfo=ZoneInfo('Asia/Kolkata'))
    return [{'at':(start+timedelta(seconds=i*30)).isoformat(),'backend_ok':True,'pid':10,'release':'a'*40} for i in range(753)]


def test_full_session_requires_release_and_continuous_healthy_coverage():
    result=audit.session_coverage(samples(),'2026-10-23')
    assert result['verified'] and result['calendar_verified']
    assert result['releases']==['a'*40]
    rows=samples();rows[300]['backend_ok']=False
    assert not audit.session_coverage(rows,'2026-10-23')['verified']
    assert not audit.session_coverage(samples()[50:],'2026-10-23')['verified']
    rows=samples();del rows[100:110]
    assert not audit.session_coverage(rows,'2026-10-23')['verified']


def test_missing_or_unknown_calendar_and_mixed_releases_do_not_pass():
    assert not audit.session_coverage([],'2026-10-23')['verified']
    rows=samples();rows[100]['release']='b'*40
    assert not audit.session_coverage(rows,'2026-10-23')['verified']
    assert not audit.session_coverage([], '2027-01-04')['calendar_verified']
    assert not audit.session_coverage(samples(), '2026-10-24')['calendar_verified']


def test_pid_restart_is_recorded_but_does_not_manufacture_gamma_recovery():
    rows=samples()
    for row in rows[200:]:row['pid']=11
    result=audit.session_coverage(rows,'2026-10-23')
    assert result['verified'] and result['pids']==[10,11]
    assert 'gamma_recovery_proven' not in result
