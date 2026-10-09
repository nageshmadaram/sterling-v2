"""Shared helpers for the tenancy tests (no network, no broker).

``app.services.simulation.sim_client`` is written by another package; these tests only
need its contract (``get_sim_client(owner, real_client_provider)`` returning a client that
declares ``_is_paper is True``), so a fake stands in for it.
"""
from __future__ import annotations

import sys
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
