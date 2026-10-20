"""The standalone operator process must initialize its credential store."""
from pathlib import Path
from types import SimpleNamespace

import pytest


def reconcile_script():
    source = (Path(__file__).resolve().parents[3] / "scripts/sterlingctl").read_text()
    return source.split("  reconcile)\n", 1)[1].split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]


@pytest.mark.parametrize("available", [True, False])
def test_reconcile_initializes_store_before_resolving_account(monkeypatch, capsys, available):
    from app.services import db, snapback_reconciliation
    from app.services.exchanges.kite import accounts
    events = []
    def initialize():
        events.append("init")
        return available
    async def reconcile():
        assert events == ["init", "accounts"]
        events.append("reconcile")
        return SimpleNamespace(account_id="bound-account", observed_at="now",
                               broker_positions=(), sterling_positions=(), clean=True)
    monkeypatch.setattr(db, "init", initialize)
    monkeypatch.setattr(accounts, "bootstrap", lambda: events.append("accounts"))
    monkeypatch.setattr(snapback_reconciliation, "reconcile_family_account", reconcile)
    with pytest.raises(SystemExit) as result:
        exec(compile(reconcile_script(), "sterlingctl reconcile", "exec"), {})
    assert result.value.code == (0 if available else 2)
    assert events == (["init", "accounts", "reconcile"] if available else ["init"])
    output = capsys.readouterr().out
    assert ("CLEAN: broker and Sterling agree." in output) is available
