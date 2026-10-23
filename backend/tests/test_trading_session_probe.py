import importlib.util
import io
import json
from pathlib import Path

spec=importlib.util.spec_from_file_location('session_probe',Path(__file__).resolve().parents[2]/'scripts/trading_session_probe.py')
probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)


def test_probe_uses_only_local_liveness_and_never_serializes_credentials(monkeypatch):
    sha='a'*40;calls=[]
    def output(args,**kwargs):
        if args[0]=='ss':return 'LISTEN users:(("uvicorn",pid=123,fd=31))'
        return sha+'\n' if 'rev-parse' in args else ''
    monkeypatch.setattr(probe.subprocess,'check_output',output)
    def read(path):
        if path.name=='cmdline':return b'python\0uvicorn\0main:app\0'
        return b'SECRET_TOKEN=do-not-export\0STERLING_RUNTIME_SHA='+sha.encode()+b'\0'
    monkeypatch.setattr(Path,'read_bytes',read)
    def fetch(url,**kwargs):calls.append(url);return io.BytesIO(b'{"alive":true}')
    monkeypatch.setattr(probe,'urlopen',fetch)
    result=probe.sample()
    assert result['backend_ok'] is True and result['pid']==123
    assert calls==['http://127.0.0.1:8000/api/v1/health/live']
    assert 'SECRET_TOKEN' not in json.dumps(result) and 'do-not-export' not in json.dumps(result)


def test_probe_failure_is_recorded_as_unhealthy_without_error_body(monkeypatch):
    def fail(*args,**kwargs):raise TimeoutError('sensitive body')
    monkeypatch.setattr(probe.subprocess,'check_output',fail)
    result=probe.sample()
    assert result['backend_ok'] is False and result['error_type']=='TimeoutError'
    assert 'sensitive body' not in json.dumps(result)


def test_probe_appends_private_jsonl_evidence(tmp_path):
    path=tmp_path/'evidence'/'samples.jsonl'
    probe.append(path,{'backend_ok':False})
    probe.append(path,{'backend_ok':True})
    assert [json.loads(x) for x in path.read_text().splitlines()]==[{'backend_ok':False},{'backend_ok':True}]
    assert path.stat().st_mode & 0o777==0o600
