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


def run_audit(tmp_path,monkeypatch,capsys,log_text):
    import json,sys
    log=tmp_path/'backend.log';log.write_text(log_text)
    probes=tmp_path/'probes.jsonl';probes.write_text('\n'.join(json.dumps(s) for s in samples())+'\n')
    monkeypatch.setattr(sys,'argv',['session-audit','--log',str(log),'--day','2026-10-23','--probes',str(probes)])
    audit.main()
    return json.loads(capsys.readouterr().out)


def test_healthy_probes_do_not_make_an_empty_or_truncated_log_a_stall_pass(tmp_path,monkeypatch,capsys):
    for content in ['', '2026-10-23 10:00:00 [INFO] truncated start\n2026-10-23 15:31:00 [INFO] alive\n']:
        result=run_audit(tmp_path,monkeypatch,capsys,content)
        assert result['session_coverage']['verified']
        assert result['market_load_accepted'] is False
        assert result['coverage']=='unverified'


def test_complete_log_and_probe_coverage_can_accept_only_the_market_load_criterion(tmp_path,monkeypatch,capsys):
    result=run_audit(tmp_path,monkeypatch,capsys,
        '2026-10-23 09:14:00 [INFO] ready\n2026-10-23 15:31:00 [INFO] ready\n')
    assert result['market_load_accepted'] is True
    assert result['log_coverage']['covers_market_session']
    assert result['full_session_accepted'] is False


def test_unparseable_stall_cannot_be_silently_counted_as_no_stalls(tmp_path,monkeypatch,capsys):
    result=run_audit(tmp_path,monkeypatch,capsys,
        '2026-10-23 09:14:00 [INFO] ready\nEvent loop stalled 9.0s; locations=a\n2026-10-23 15:31:00 [INFO] ready\n')
    assert result['market_load_accepted'] is False
    assert result['unparsed_stall_lines']==1


def test_invalid_stall_duration_does_not_crash_or_pass(tmp_path,monkeypatch,capsys):
    for duration in ['..', 'NaN', 'inf', '-3']:
        result=run_audit(tmp_path,monkeypatch,capsys,
            f'2026-10-23 09:14:00 [INFO] ready\n2026-10-23 10:00:00 Event loop stalled {duration}s; locations=a\n2026-10-23 15:31:00 [INFO] ready\n')
        assert result['market_load_accepted'] is False
        assert result['unparsed_stall_lines']==1
