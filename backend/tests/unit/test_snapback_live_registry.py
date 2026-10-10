"""Snapback's live positions are Sterling's own, to every shared reader.

The broker-exposure observer, the exposure snapshot, the boot safety check and the
account-detach guard each enumerate the strategy registries. A registry they do not
know about makes its positions look like FOREIGN exposure, and foreign exposure
blocks every strategy's entries. These tests pin that Snapback's registry is read
by all of them, and that the position record behaves as a registry entry.
"""
from __future__ import annotations

import pytest

from app.services.snapback_live_positions import (
    CLOSED, ENTERING, EXITING, OPEN, LivePosition, LivePositionStore,
)


def _position(**over) -> LivePosition:
    base = dict(
        position_id="SBP-REG-1", uid="reguser", account_id="KITE-REG", signal_id="sig-reg",
        lane="swing", underlying="NIFTY", side="fade_up", option_type="PE",
        option_exchange="NFO", option_symbol="NIFTY26NOV25000PE", option_qty=75,
        option_entry_price=300.0, option_entry_order_id="ORD-OPT", hedge_exchange="NFO",
        hedge_symbol="NIFTY26NOVFUT", hedge_side="BUY", hedge_qty=75,
        hedge_entry_price=25001.0, hedge_entry_order_id="ORD-FUT", status=OPEN,
        protection_state="ACTIVE", gtt_id=7001, gtt_stop=210.0, gtt_qty=75,
        product="NRML",
    )
    base.update(over)
    return LivePosition(**base)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("STERLING_SNAPBACK_LIVE_DB", str(tmp_path / "live.db"))
    return LivePositionStore(tmp_path / "live.db")


def test_the_record_round_trips_and_lists_by_user(store):
    store.put(_position())

    got = store.get("SBP-REG-1")
    assert got.option_symbol == "NIFTY26NOV25000PE" and got.hedge_qty == 75
    assert [p.position_id for p in store.open_positions("reguser")] == ["SBP-REG-1"]
    assert store.open_positions("someone-else") == []
    assert store.known_uids() == ["reguser"]


def test_a_closed_position_is_not_open(store):
    store.put(_position(status=CLOSED))

    assert store.open_positions("reguser") == []
    assert store.get("SBP-REG-1").is_open is False


def test_protection_pending_requires_complete_durable_gtt_evidence():
    assert _position().protection_pending is False
    assert _position(protection_state="NONE").protection_pending is True
    assert _position(protection_state="FAILED", status=ENTERING).protection_pending is True

    # ACTIVE is only a label. Missing or inconsistent broker-stop evidence must
    # still fail closed across restart.
    assert _position(gtt_id=0).protection_pending is True
    assert _position(gtt_stop=0.0).protection_pending is True
    assert _position(gtt_qty=50).protection_pending is True
    assert _position(gtt_qty=100).protection_pending is True

    # Partial exits must leave an exactly-sized stop over the remaining holding.
    assert _position(option_exit_qty=25, gtt_qty=50).protection_pending is False
    assert _position(option_exit_qty=25, gtt_qty=75).protection_pending is True

    # An exit in flight deliberately released the stop; that is not a new-entry
    # protection question because the position is already being flattened.
    assert _position(protection_state="NONE", status=EXITING).protection_pending is False
    assert _position(protection_state="NONE", status=CLOSED).protection_pending is False


def test_mutate_is_read_modify_write_and_can_abandon(store):
    store.put(_position(protection_state="NONE"))

    def raise_stop(pos):
        pos.desired_stop = 210.0

    assert store.mutate("SBP-REG-1", raise_stop).desired_stop == 210.0
    assert store.mutate("SBP-REG-1", lambda pos: False).desired_stop == 210.0
    assert store.mutate("NO-SUCH", raise_stop) is None


def test_every_order_the_position_placed_is_attributable():
    pos = _position(option_exit_order_id="ORD-EXIT", option_exit_requested=75)

    assert pos.order_id == "ORD-OPT"
    assert set(pos.qty_by_order) == {"ORD-OPT", "ORD-FUT", "ORD-EXIT"}


def test_the_exposure_observer_attributes_both_legs_and_the_stop_to_sterling(store):
    from app.services import external_positions

    store.put(_position())

    owned = external_positions._scan_local_ownership("KITE-REG")

    assert ("NFO", "NRML", "NIFTY26NOV25000PE") in owned.identities
    assert ("NFO", "NRML", "NIFTY26NOVFUT") in owned.identities
    assert "7001" in owned.gtt_ids
    assert {"ORD-OPT", "ORD-FUT"} <= set(owned.order_ids)


def test_another_accounts_snapback_position_is_not_claimed(store):
    from app.services import external_positions

    store.put(_position(account_id="KITE-OTHER"))

    owned = external_positions._scan_local_ownership("KITE-REG")

    assert ("NFO", "NRML", "NIFTY26NOV25000PE") not in owned.identities


def test_a_snapback_option_is_not_foreign_exposure():
    from app.services.external_positions import classify_broker_positions

    exposure = classify_broker_positions(
        [{"tradingsymbol": "NIFTY26NOV25000PE", "exchange": "NFO", "product": "NRML",
          "quantity": 75},
         {"tradingsymbol": "SOMETHINGELSE", "exchange": "NFO", "product": "NRML",
          "quantity": 10}],
        account="KITE-REG",
        known_symbols={("NFO", "NRML", "NIFTY26NOV25000PE")},
    )

    assert [p.instrument for p in exposure.positions] == ["SOMETHINGELSE"]


def test_the_exposure_snapshot_counts_snapback_positions(store, monkeypatch):
    from app.services import exposure_snapshot

    store.put(_position())

    assert "reguser" in exposure_snapshot._known_local_uids()
    snap = exposure_snapshot.exposure_snapshot(include_broker=False)
    assert "NIFTY26NOV25000PE" in snap.held


def test_the_boot_check_names_an_unprotected_snapback_position(store):
    from app.services import live_runtime

    store.put(_position(protection_state="NONE"))

    check = live_runtime.provably_clean()

    assert check.clean is False
    assert any(
        "without confirmed protection" in b and "NIFTY26NOV25000PE" in b
        for b in check.blockers
    )


def test_boot_check_rejects_active_snapback_label_without_gtt_evidence(store):
    from app.services import live_runtime

    store.put(_position(protection_state="ACTIVE", gtt_id=0))

    check = live_runtime.provably_clean()

    assert check.clean is False
    assert any(
        "without confirmed protection" in b and "NIFTY26NOV25000PE" in b
        for b in check.blockers
    )


def test_family_reconciliation_sees_snapback_positions_and_their_stop(store):
    from app.services import snapback_reconciliation as recon

    store.put(_position())

    rows = recon._canonical_live_positions("reguser", "KITE-REG")
    symbols = {r["tradingsymbol"]: r for r in rows}
    assert symbols["NIFTY26NOV25000PE"]["quantity"] == 75
    assert symbols["NIFTY26NOVFUT"]["quantity"] == 75
    assert symbols["NIFTY26NOVFUT"]["vehicle"] == "futures"
    assert recon._protection_state("reguser", "NIFTY26NOV25000PE")["state"] == "ACTIVE"
    store.mutate("SBP-REG-1", lambda p: setattr(p, "protection_state", "NONE"))
    assert recon._protection_state("reguser", "NIFTY26NOV25000PE")["state"] == "NONE"


def test_a_short_hedge_is_reconciled_as_a_negative_position(store):
    from app.services import snapback_reconciliation as recon

    store.put(_position(hedge_side="SELL", option_type="CE"))

    rows = {r["tradingsymbol"]: r for r in recon._canonical_live_positions("reguser", "KITE-REG")}
    assert rows["NIFTY26NOVFUT"]["quantity"] == -75
