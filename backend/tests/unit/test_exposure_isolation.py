"""One tenant's missing broker session must not gate another tenant's entries."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services import external_positions as ep
from app.services import live_runtime as lr


@pytest.fixture(autouse=True)
def _fresh_runtime():
    lr.reset_for_tests()
    yield
    lr.reset_for_tests()


def _account(account_id, uid, *, active=True, connected=True):
    return SimpleNamespace(
        id=account_id, user_id=uid, is_paper=False, is_active=active,
        connected=connected, token_expires_at=None, token_is_live=True,
        kite_user_id=f"K-{account_id}",
    )


class _Registry:
    def __init__(self, accounts):
        self._accounts = list(accounts)

    def bootstrap(self):
        return None

    def all_accounts(self):
        return list(self._accounts)

    async def acquire_client(self, account):
        return SimpleNamespace(_account_id=account.id)


class _CleanService:
    def __init__(self):
        self.calls = []

    async def startup_recovery(self, uid, account_id, broker_client):
        self.calls.append(account_id)
        return {"status": "success", "recovery_state": lr.CLEAN, "unresolved_count": 0}


# ------------------------------------------------------ recovery (finding 30, part 1)
def test_inactive_live_accounts_are_not_live_accounts_for_the_runtime():
    registry = _Registry([
        _account("acct-1", "u1"),
        _account("acct-2", "u2", active=False, connected=False),
    ])

    assert [a.id for a in lr._live_accounts(registry)] == ["acct-1"]


@pytest.mark.asyncio
async def test_a_never_logged_in_inactive_account_does_not_hold_the_root_scope(monkeypatch):
    registry = _Registry([
        _account("acct-1", "u1"),
        _account("acct-2", "u2", active=False, connected=False),
    ])
    service = _CleanService()
    settled = []
    monkeypatch.setattr(lr, "_settle_root_scope", lambda entries: settled.append(
        [(e.account_id, e.state) for e in entries]) or "root scope set CLEAN")

    results = await lr.run_recovery_cycle(service=service, kite_accounts=registry)

    assert [(r.account_id, r.state) for r in results] == [("acct-1", lr.CLEAN)]
    assert settled == [[("acct-1", lr.CLEAN)]]
    assert service.calls == ["acct-1"]
