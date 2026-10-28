"""Board evidence comes from scoped durable fills, not scanner estimates."""
from types import SimpleNamespace

from app.services import snapback, snapback_live_positions, snapback_readiness
from app.services.snapback import SnapbackConfig
from app.services.snapback_live_positions import LivePosition, LivePositionStore, OPEN, CLOSED


def position(uid, key, status=OPEN):
    return LivePosition(position_id=key, uid=uid, account_id="account", signal_id=key,
                        lane="scalp", underlying="NIFTY", side="fade_down", option_type="CE",
                        option_exchange="NFO", option_symbol="NIFTY26OCT25000CE",
                        option_qty=75, lot_size=75, option_entry_price=101.5, status=status)


def prepare(monkeypatch, store):
    monkeypatch.setattr(snapback, "get_config", lambda uid: SnapbackConfig(enabled=True))
    monkeypatch.setattr(snapback, "status", lambda uid: SimpleNamespace(
        rows=[], scanned=0, scan_total=0, scanning=False, last_scan_ms=0, last_error=None, failures=[]))
    monkeypatch.setattr(snapback_readiness, "live_conditions",
                        lambda *a, **kw: snapback_readiness.LiveConditions(blockers=["kill_switch"]))
    monkeypatch.setattr(snapback_live_positions, "default_store", lambda: store)


def test_snapshot_restores_scoped_owned_positions_when_scanner_has_no_rows(monkeypatch, tmp_path):
    store = LivePositionStore(tmp_path / "positions.db")
    store.put(position("operator", "owned"))
    store.put(position("other", "foreign"))
    prepare(monkeypatch, store)
    result = snapback.snapshot("operator")
    assert result["rows"] == []
    assert [p["position_id"] for p in result["positions"]] == ["owned"]
    assert result["positions"][0]["option_entry_price"] == 101.5
    assert result["positions_error"] is None
    assert not result["capabilities"]["manual_execution"]["single_leg"]


def test_snapshot_keeps_every_open_position_and_bounds_closed_history(monkeypatch, tmp_path):
    store = LivePositionStore(tmp_path / "positions.db")
    for index in range(105):
        store.put(position("operator", f"closed-{index}", CLOSED))
    for index in range(3):
        store.put(position("operator", f"open-{index}"))
    prepare(monkeypatch, store)
    result = snapback.snapshot("operator")
    assert len(result["positions"]) == 103
    assert sum(p["status"] == OPEN for p in result["positions"]) == 3


def test_board_snapshot_never_fetches_full_closed_history(monkeypatch, tmp_path):
    store = LivePositionStore(tmp_path / "positions.db")
    for index in range(140):
        store.put(position("operator", f"closed-{index:03d}", CLOSED))
    store.put(position("operator", "still-open", OPEN))
    prepare(monkeypatch, store)

    # The board path must use SQL-bounded reads even as the closed ledger grows.
    # A regression to store.list(uid) would load and decode every old row.
    def forbid_unbounded_list(*_args, **_kwargs):
        raise AssertionError("board snapshot must not load full position history")
    monkeypatch.setattr(store, "list", forbid_unbounded_list)

    result = snapback.snapshot("operator")
    assert result["positions_error"] is None
    assert len(result["positions"]) == 101
    assert [p["position_id"] for p in result["positions"] if p["status"] == OPEN] == ["still-open"]
    assert sum(p["status"] == CLOSED for p in result["positions"]) == 100


def test_registry_failure_is_explicit_and_never_fabricates_flat_inventory(monkeypatch):
    class Unreadable:
        def board_positions(self, uid, *, recent_closed=100):
            raise OSError("unreadable")
    prepare(monkeypatch, Unreadable())
    result = snapback.snapshot("operator")
    assert result["positions"] == []
    assert result["positions_error"] == "Owned Snapback positions unavailable: OSError"
