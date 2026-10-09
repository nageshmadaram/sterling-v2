"""The virtual tenant's open positions never count as the owner's real exposure."""
from __future__ import annotations

import pytest
from tenancy_support import OWNER, SIM_UID, unprotected_position, wipe_tenants

from app.services import exposure_snapshot, live_runtime
from app.services.kite_engine import positions


@pytest.fixture(autouse=True)
def _clean_books():
    wipe_tenants()           # the registry persists: a leftover row would block later tests' account migration
    yield
    wipe_tenants()


def test_a_sim_position_is_not_a_known_local_uid():
    unprotected_position(SIM_UID)
    assert SIM_UID in positions.known_uids()            # the registry itself still holds it
    assert SIM_UID not in exposure_snapshot._known_local_uids()


def test_a_sim_position_is_not_real_exposure():
    unprotected_position(SIM_UID)
    snap = exposure_snapshot.exposure_snapshot(include_broker=False)
    assert snap.open_positions == 0 and snap.held == () and SIM_UID not in snap.uids


def test_a_real_position_is_still_counted_beside_a_sim_one():
    unprotected_position(SIM_UID)
    unprotected_position(OWNER)
    snap = exposure_snapshot.exposure_snapshot(include_broker=False)
    assert snap.open_positions == 1 and OWNER in snap.uids and SIM_UID not in snap.uids


def _awaiting_stop(uid):
    unprotected_position(uid)
    for p in positions.open_positions(uid):
        p.protection_pending = True
        positions.register(p)


def test_a_sim_position_awaiting_its_virtual_stop_does_not_dirty_the_boot_check():
    _awaiting_stop(SIM_UID)
    check = live_runtime.provably_clean()
    assert not any(SIM_UID in b or "sim:" in b for b in check.blockers), check.blockers


def test_a_real_unprotected_position_still_blocks_the_boot_check():
    _awaiting_stop(OWNER)
    check = live_runtime.provably_clean()
    assert check.clean is False and any(OWNER in b or "protect" in b for b in check.blockers)


def test_a_sim_position_is_never_attributed_to_a_real_broker_account():
    from app.services import external_positions

    unprotected_position(SIM_UID)
    for p in positions.open_positions(SIM_UID):
        p.account_id = "SIM-ABCDEF0123"
        positions.register(p)
    owned = external_positions._scan_local_ownership("KITE-REAL")
    assert owned is not None and ("NFO", "NRML", "NIFTY24JUN24000CE") not in owned.identities
    assert not any("NIFTY24JUN24000CE" in str(i) for i in owned.identities)
