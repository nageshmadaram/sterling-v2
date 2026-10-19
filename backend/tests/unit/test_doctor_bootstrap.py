"""Doctor must inspect persisted state, rather than uninitialized caches."""
from pathlib import Path
from types import SimpleNamespace
import pytest


def _body():
    source=(Path(__file__).resolve().parents[3]/'scripts/sterlingctl').read_text()
    return source.split("cmd_doctor() {\n  run_py <<'PY'\n",1)[1].split('\nPY\n}',1)[0]


def test_doctor_initializes_database_and_accounts_before_checks(monkeypatch):
    from app.services import db,snapback_preflight
    from app.services.exchanges.kite import accounts
    from app.core import operator_report
    calls=[]
    monkeypatch.setattr(db,'is_available',lambda:False)
    monkeypatch.setattr(db,'init',lambda:calls.append('database') or True)
    monkeypatch.setattr(accounts,'bootstrap',lambda:calls.append('accounts'))
    def preflight():
        assert calls==['database','accounts']
        calls.append('preflight')
        return SimpleNamespace(checks=[])
    monkeypatch.setattr(snapback_preflight,'run_preflight',preflight)
    monkeypatch.setattr(operator_report,'lane_doctor_checks',lambda:[])
    with pytest.raises(SystemExit):exec(_body(),{})
    assert calls==['database','accounts','preflight']


def test_doctor_refuses_when_database_cannot_initialize(monkeypatch,capsys):
    from app.services import db
    from app.services.exchanges.kite import accounts
    monkeypatch.setattr(db,'is_available',lambda:False)
    monkeypatch.setattr(db,'init',lambda:False)
    def forbidden():raise AssertionError('must not inspect empty account cache')
    monkeypatch.setattr(accounts,'bootstrap',forbidden)
    with pytest.raises(SystemExit) as error:exec(_body(),{})
    assert error.value.code==2
    assert 'Database unavailable' in capsys.readouterr().out
