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


# ------------------------------------------------------ exposure (finding 30, part 2)
class _BrokerClient:
    def __init__(self, account_id, positions=()):
        self._account_id = account_id
        self._positions = list(positions)

    async def get_positions_raw(self):
        return {"net": list(self._positions)}

    async def get_orders(self):
        return []

    async def get_gtts(self):
        return []

    async def get_mf_orders(self):
        return []

    async def get_mf_sips(self):
        return []

    async def get_alerts(self):
        return []


def _row(symbol, qty=75):
    return {"tradingsymbol": symbol, "exchange": "NFO", "product": "NRML",
            "quantity": qty, "average_price": 100.0, "last_price": 101.0}


@pytest.fixture
def two_tenants(monkeypatch, tmp_path):
    """acct-ok is connected; acct-expired is ACTIVE but its token has expired."""
    from app.services.exchanges.kite.errors import KiteTokenError

    accounts = [
        SimpleNamespace(id="acct-ok", kite_user_id="K-OK", is_paper=False, is_active=True),
        SimpleNamespace(id="acct-expired", kite_user_id="K-EXP", is_paper=False, is_active=True),
    ]
    clients = {"acct-ok": _BrokerClient("acct-ok")}

    async def acquire(account):
        if account.id not in clients:
            raise KiteTokenError("no access_token")
        return clients[account.id]

    monkeypatch.setattr("app.services.exchanges.kite.accounts.bootstrap", lambda: None)
    monkeypatch.setattr("app.services.exchanges.kite.accounts.all_accounts", lambda: accounts)
    monkeypatch.setattr("app.services.exchanges.kite.accounts.acquire_client", acquire)
    monkeypatch.setattr(ep, "_known_symbols", lambda _account: set())
    monkeypatch.setattr(ep, "_live_account_configured", lambda: True)
    monkeypatch.setattr(ep, "live_account_ids", lambda: ("acct-expired", "acct-ok"))
    monkeypatch.setenv("STERLING_EXTERNAL_EXPOSURE_FILE", str(tmp_path / "exposure.json"))
    return SimpleNamespace(accounts=accounts, clients=clients)


@pytest.mark.asyncio
async def test_a_tenant_without_a_session_does_not_gate_the_healthy_tenant(two_tenants):
    exposure = await ep.external_exposure()
    ep.record_observation(exposure)

    # The combined answer still says "could not ask everyone": nothing is hidden.
    assert exposure.readable is False
    assert ep.admission_blocker()[0] == "external_exposure_unreadable"
    # But each account is admitted on what the broker said about THAT account.
    assert ep.admission_blocker("acct-ok") == ("", "")
    code, reason = ep.admission_blocker("acct-expired")
    assert code == "external_exposure_unreadable" and "no access_token" in reason


@pytest.mark.asyncio
async def test_external_position_blocks_only_the_account_that_holds_it(two_tenants):
    two_tenants.clients["acct-ok"] = _BrokerClient("acct-ok", [_row("NIFTY26OCT24000CE")])
    two_tenants.clients["acct-expired"] = _BrokerClient("acct-expired")

    exposure = await ep.external_exposure()
    ep.record_observation(exposure)

    assert exposure.readable is True
    code, reason = ep.admission_blocker("acct-ok")
    assert code == "external_broker_exposure" and "NIFTY26OCT24000CE" in reason
    assert ep.admission_blocker("acct-expired") == ("", "")
    assert ep.admission_blocker()[0] == "external_broker_exposure"     # unscoped: unchanged


@pytest.mark.asyncio
async def test_an_account_missing_from_the_record_is_not_admitted(two_tenants):
    exposure = await ep.external_exposure()
    ep.record_observation(exposure)

    code, reason = ep.admission_blocker("acct-never-seen")

    assert code == "external_exposure_unreadable"
    assert "acct-never-seen" in reason


@pytest.mark.asyncio
async def test_a_stale_record_blocks_every_account(two_tenants):
    exposure = await ep.external_exposure()
    ep.record_observation(exposure)
    path = ep._observation_path()
    record = json.loads(path.read_text())
    record["observed_at"] = datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat()
    path.write_text(json.dumps(record))

    assert ep.admission_blocker("acct-ok")[0] == "external_exposure_stale"


def test_a_record_without_per_account_data_keeps_the_combined_rules(two_tenants):
    path = ep._observation_path()
    path.write_text(json.dumps({
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "readable": False, "count": None, "accounts": [], "detail": "ConnectTimeout",
    }))

    assert ep.admission_blocker("acct-ok")[0] == "external_exposure_unreadable"
