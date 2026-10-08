from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_supertrend_auto_scan_uses_only_active_connected_account(monkeypatch):
    from app.services.kite_engine import service
    from app.services.exchanges.kite import accounts

    active = SimpleNamespace(
        id="LIVE-ACTIVE", user_id="u1", connected=True, is_active=True
    )
    inactive = SimpleNamespace(
        id="LIVE-INACTIVE", user_id="u1", connected=True, is_active=False
    )
    disconnected = SimpleNamespace(
        id="LIVE-DOWN", user_id="u2", connected=False, is_active=True
    )

    monkeypatch.setattr(
        accounts, "_load_from_db", lambda: [active, inactive, disconnected]
    )

    clients = {}

    async def acquire(acct):
        client = SimpleNamespace(get_profile=AsyncMock(return_value={"ok": True}))
        clients[acct.id] = client
        return client

    monkeypatch.setattr(accounts, "acquire_client", acquire)

    scans = []

    async def scan_user(client, uid, *, interval_s=service.SCAN_INTERVAL_S):
        scans.append((uid, client))
        return 0

    monkeypatch.setattr(service, "scan_user", scan_user)
    service._first_scan_done = False
    service._last_scan_wall_s = 0.0

    await service._scan_all_connected_once()

    assert [uid for uid, _ in scans] == ["u1"]
    assert scans[0][1] is clients["LIVE-ACTIVE"]
    assert "LIVE-INACTIVE" not in clients
    assert "LIVE-DOWN" not in clients


@pytest.mark.asyncio
async def test_supertrend_startup_reconcile_uses_only_active_connected_account(monkeypatch):
    from app.services.kite_engine import service
    from app.services.exchanges.kite import accounts

    active = SimpleNamespace(
        id="LIVE-ACTIVE", user_id="u1", connected=True, is_active=True
    )
    inactive = SimpleNamespace(
        id="LIVE-INACTIVE", user_id="u1", connected=True, is_active=False
    )

    monkeypatch.setattr(accounts, "_load_from_db", lambda: [active, inactive])

    clients = {}

    async def acquire(acct):
        client = SimpleNamespace()
        clients[acct.id] = client
        return client

    monkeypatch.setattr(accounts, "acquire_client", acquire)

    reconciled = []

    async def reconcile(client, uid):
        reconciled.append((uid, client))

    monkeypatch.setattr(service, "reconcile_user_auto_open", reconcile)

    await service.reconcile_all_auto_open()

    assert [uid for uid, _ in reconciled] == ["u1"]
    assert reconciled[0][1] is clients["LIVE-ACTIVE"]
    assert "LIVE-INACTIVE" not in clients
