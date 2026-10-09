"""The live dispatcher keeps the bound account's reconciliation fresh.

With ``STERLING_FAMILY_USER_ID`` / ``STERLING_FAMILY_ACCOUNT_ID`` set (the documented account
binding), readiness requires a clean, fresh reconciliation of that account before Snapback may
open anything. Its only producers were the optional prospective evidence runner, the CLI start
sequence and a report route, so on a live-only server the snapshot never existed and every live
entry was refused as ``broker_not_reconciled`` for ever.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import snapback_family_account as family
from app.services import snapback_reconciliation as recon
from app.services.snapback_dispatcher import (
    BOUND_RECONCILIATION_REFRESH_S, SnapbackDispatcher,
)

UID, ACCOUNT = "family-user", "KITE-FAMILY"


@pytest.fixture()
def calls(monkeypatch):
    seen = []

    async def reconcile_family_account(warehouse=None):
        seen.append("reconciled")
        return SimpleNamespace(clean=True)

    monkeypatch.setattr(recon, "reconcile_family_account", reconcile_family_account)
    monkeypatch.setattr(recon, "_BY_ACCOUNT", {})
    monkeypatch.setattr(family, "binding_configured", lambda: True)
    monkeypatch.setattr(family, "configured_binding",
                        lambda: family.FamilyAccountBinding(user_id=UID, account_id=ACCOUNT))
    return seen


def _snapshot(age_s: float):
    observed = (datetime.now(timezone.utc) - timedelta(seconds=age_s)).isoformat()
    return recon.ReconciliationSnapshot(clean=True, account_id=ACCOUNT, observed_at=observed)


def _refresh(uid=UID, account=ACCOUNT):
    asyncio.run(SnapbackDispatcher()._refresh_bound_reconciliation(uid, account))


def test_a_missing_snapshot_for_the_bound_account_is_produced(calls):
    _refresh()
    assert calls == ["reconciled"]


def test_a_snapshot_older_than_the_refresh_interval_is_replaced(calls):
    recon._BY_ACCOUNT[ACCOUNT] = _snapshot(BOUND_RECONCILIATION_REFRESH_S + 5)
    _refresh()
    assert calls == ["reconciled"]


def test_a_fresh_snapshot_is_not_re_read_on_every_cycle(calls):
    recon._BY_ACCOUNT[ACCOUNT] = _snapshot(2)
    _refresh()
    assert calls == []


def test_the_refresh_interval_stays_inside_the_readiness_freshness_window():
    assert 0 < BOUND_RECONCILIATION_REFRESH_S < recon.RECONCILIATION_TTL_SECONDS


def test_no_binding_means_nothing_to_reconcile(calls, monkeypatch):
    monkeypatch.setattr(family, "binding_configured", lambda: False)
    _refresh()
    assert calls == []


@pytest.mark.parametrize("uid,account", [("someone-else", ACCOUNT), (UID, "KITE-OTHER")])
def test_a_cycle_for_a_different_user_or_account_does_not_reconcile_the_bound_one(calls, uid, account):
    _refresh(uid, account)
    assert calls == []


def test_a_failing_refresh_never_breaks_the_cycle(monkeypatch):
    async def boom(warehouse=None):
        raise RuntimeError("broker unreachable")

    monkeypatch.setattr(recon, "reconcile_family_account", boom)
    monkeypatch.setattr(recon, "_BY_ACCOUNT", {})
    monkeypatch.setattr(family, "binding_configured", lambda: True)
    monkeypatch.setattr(family, "configured_binding",
                        lambda: family.FamilyAccountBinding(user_id=UID, account_id=ACCOUNT))

    _refresh()          # must not raise; the gate simply stays unknown (closed)


# ---------------------------------------------------------- where in the cycle it runs

def _cycle_with(monkeypatch, *, enabled: bool, auto: bool):
    """One dispatcher cycle with every collaborator stubbed; returns what the refresh saw."""
    from app.services import snapback, snapback_dispatcher as module

    refreshed = []

    class _Positions:
        def open_positions(self, uid, account_id=None):
            return []

    class _Exits:
        async def tick(self, uid, client):
            return {"managed": 0}

    dispatcher = SnapbackDispatcher(
        positions=_Positions(), exit_manager=_Exits(), plan_store=SimpleNamespace(
            list_in_flight=lambda **kw: []),
        client_factory=lambda uid: SimpleNamespace(_account_id=ACCOUNT),
        rows_provider=lambda uid: [], scanner=None)

    async def refresh(uid, account_id):
        refreshed.append((uid, account_id))

    dispatcher._refresh_bound_reconciliation = refresh
    monkeypatch.setattr(snapback, "get_config",
                        lambda uid=None: SimpleNamespace(enabled=enabled, auto_execute=auto))
    monkeypatch.setattr(module, "auto_execute", lambda uid, cfg=None: auto)
    monkeypatch.setattr(module, "origination_blockers", lambda *a, **k: ["session_closed"])
    asyncio.run(dispatcher.cycle(UID))
    return refreshed


def test_a_manual_arm_needs_it_so_it_runs_even_when_auto_is_off(monkeypatch):
    assert _cycle_with(monkeypatch, enabled=True, auto=False) == [(UID, ACCOUNT)]


def test_it_runs_when_auto_is_on(monkeypatch):
    assert _cycle_with(monkeypatch, enabled=True, auto=True) == [(UID, ACCOUNT)]


def test_a_disabled_engine_does_not_spend_broker_reads_on_it(monkeypatch):
    assert _cycle_with(monkeypatch, enabled=False, auto=False) == []
