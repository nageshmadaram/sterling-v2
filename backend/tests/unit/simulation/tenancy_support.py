"""Shared helpers for the tenancy tests (no network, no broker).

``app.services.simulation.sim_client`` is written by another package; these tests only
need its contract (``get_sim_client(owner, real_client_provider)`` returning a client that
declares ``_is_paper is True``), so a fake stands in for it.
"""
from __future__ import annotations

import sys
import time
import types

from app.services.exchanges.kite import accounts as kite_accounts
from app.services.exchanges.kite.models import KiteAccountCreate
from app.services.simulation import identity

OWNER = "default"
SIM_UID = identity.sim_uid(OWNER)
SIM_ACCOUNT_ID = identity.sim_account_id(OWNER)


class FakeSimClient:
    """Stands in for SimulatedKiteClient: declares 'no real money', refuses everything else."""

    _is_paper = True

    def __init__(self, owner, provider):
        self.owner = owner
        self.provider = provider
        self.closed = False

    async def close(self):
        self.closed = True


def install_fake_sim_client(monkeypatch, factory=None):
    """Register a fake ``app.services.simulation.sim_client`` and return the list of builds."""
    built = []

    def get_sim_client(owner_uid, real_client_provider):
        client = (factory or FakeSimClient)(owner_uid, real_client_provider)
        built.append(client)
        return client

    module = types.ModuleType("app.services.simulation.sim_client")
    module.get_sim_client = get_sim_client
    monkeypatch.setitem(sys.modules, "app.services.simulation.sim_client", module)
    return built


def live_account(owner=OWNER, *, connected=True, key="LIVEKEY1", label="Live"):
    """A stored LIVE Kite account for ``owner`` (optionally holding a session)."""
    account = kite_accounts.add(owner, KiteAccountCreate(label=label, api_key=key, api_secret="s3cret"))
    if connected:
        kite_accounts.save_session(owner, account.id, access_token="ACCESS-TOKEN",
                                   kite_user_id="ZZ1234", user_name="Owner")
    return kite_accounts.get(owner, account.id)


def unprotected_position(uid=OWNER):
    """An OPEN position with no stop in ``uid``'s registry: the book the AUTO readiness gate refuses."""
    from app.services.kite_engine import positions

    positions.register(positions.OpenPosition(
        uid=uid, symbol="NIFTY24JUN24000CE", exchange="NFO", token=777, qty=75, entry_premium=120.0,
        stop_premium=0.0, order_id="ENTRY-1", status=positions.OPEN, direction="long",
        red_count_ms=int(time.time() * 1000)))


def wipe_tenants():
    """Everything the mode / API tests leave in the shared test database, for BOTH tenants."""
    from app.services import db
    from app.services import trade_journal as journal
    from app.services.kite_engine import positions
    from app.services.kite_engine import state as engine_state
    from app.services.simulation import mode
    from app.services.simulation.ledger import DEFAULT_CAPITAL, get_ledger

    for uid in (OWNER, SIM_UID):
        mode._purge_config_rows(uid)
        engine_state.reset(uid)
        positions.reset(uid)
    for key in ("gamma_move_config", "snapback_config"):       # the legacy single-user rows
        db.set_config(key, "")
    with db._conn() as c:
        c.execute("DELETE FROM execution_control WHERE uid=?", (SIM_UID,))
        c.execute("DELETE FROM kite_order_intents WHERE uid IN (?, ?)", (SIM_UID, OWNER))
    get_ledger().reset(OWNER, capital=DEFAULT_CAPITAL)
    journal.purge_simulation(SIM_UID)
    with journal._write() as conn:                              # the live tenant's rows from earlier tests
        conn.execute("DELETE FROM trade_journal_events WHERE trade_id IN "
                     "(SELECT trade_id FROM trade_journal_trades WHERE uid=?)", (OWNER,))
        conn.execute("DELETE FROM trade_journal_trades WHERE uid=?", (OWNER,))
        conn.execute("DELETE FROM trade_journal_skips WHERE uid=?", (OWNER,))
