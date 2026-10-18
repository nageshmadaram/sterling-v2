"""Fail-closed persistence contracts for capital-critical position state."""
from __future__ import annotations

import pytest

from app.services import (
    adaptive_edge_positions,
    db,
    gamma_move_positions,
    intraday_positions,
)
from app.services.kite_engine import positions as kite_positions


@pytest.mark.parametrize(
    ("module", "cache_name", "uid"),
    [
        (intraday_positions, "_cache", "u-intraday"),
        (adaptive_edge_positions, "_cache", "u-adaptive"),
        (gamma_move_positions, "_cache", "u-gamma"),
    ],
)
def test_legacy_position_registry_corruption_is_not_flat(
    monkeypatch, module, cache_name, uid
):
    getattr(module, cache_name).pop(uid, None)
    monkeypatch.setattr(db, "get_config_strict", lambda _key: '{"not":"a list"}')

    with pytest.raises(ValueError):
        module.open_positions(uid)


def test_kite_position_registry_db_failure_is_not_flat(monkeypatch):
    uid = "u-kite-db-failure"
    kite_positions._positions.pop(uid, None)

    def unreadable(_key):
        raise db.ControlPlaneUnavailableError("database unavailable")

    monkeypatch.setattr(db, "get_config_strict", unreadable)

    with pytest.raises(kite_positions.RegistryUnreadable):
        kite_positions.open_positions(uid)


@pytest.mark.parametrize(
    ("module", "uid"),
    [
        (intraday_positions, "u-intraday-write"),
        (adaptive_edge_positions, "u-adaptive-write"),
        (gamma_move_positions, "u-gamma-write"),
    ],
)
def test_legacy_position_persistence_failure_is_visible(monkeypatch, module, uid):
    # Seed the in-process cache so this test isolates the write contract rather
    # than needing a strategy-specific position constructor.
    module._cache[uid] = {}

    def fail_write(_key, _value):
        raise db.ControlPlaneUnavailableError("disk unavailable")

    monkeypatch.setattr(db, "set_config_strict", fail_write)

    with pytest.raises(db.ControlPlaneUnavailableError):
        module.persist(uid)


def test_strict_config_read_refuses_unavailable_database(monkeypatch):
    monkeypatch.setattr(db, "_available", False)

    with pytest.raises(db.ControlPlaneUnavailableError):
        db.get_config_strict("capital-critical")


def test_strict_config_write_refuses_unavailable_database(monkeypatch):
    monkeypatch.setattr(db, "_available", False)

    with pytest.raises(db.ControlPlaneUnavailableError):
        db.set_config_strict("capital-critical", "[]")


def test_unreadable_legacy_gamma_archive_never_means_flat(monkeypatch):
    monkeypatch.setattr(db, "get_config_strict", lambda _: "{invalid JSON")
    with pytest.raises(RuntimeError, match="legacy_gamma_archive_unreadable"):
        gamma_move_positions.legacy_open_rows("legacy-owner")


def test_legacy_gamma_migration_does_not_drop_active_row_when_archive_write_fails(
    monkeypatch,
):
    import json

    uid = "legacy-migration-fail"
    old = {
        "signal_id": "old-gamma",
        "instrument": {"tradingsymbol": "OPT"},
        "entry": 100.0,
        "stop": 90.0,
        "quantity": 40,
        "lots": 1,
        "entered_ms": 1000,
        "entry_day": "2026-10-01",
        "status": "open",
    }
    records = {
        gamma_move_positions._key(uid): json.dumps([old]),
        gamma_move_positions._legacy_key(uid): "[]",
    }
    gamma_move_positions._cache.pop(uid, None)
    monkeypatch.setattr(
        db, "get_config_strict", lambda key, default="": records.get(key, default)
    )

    def fail_legacy_write(key, value):
        if key == gamma_move_positions._legacy_key(uid):
            raise db.ControlPlaneUnavailableError("archive disk full")
        records[key] = value

    monkeypatch.setattr(db, "set_config_strict", fail_legacy_write)
    with pytest.raises(db.ControlPlaneUnavailableError, match="archive disk full"):
        gamma_move_positions.load(uid)

    assert json.loads(records[gamma_move_positions._key(uid)]) == [old]
    assert records[gamma_move_positions._legacy_key(uid)] == "[]"
    gamma_move_positions._cache.pop(uid, None)


def test_unreconciled_legacy_gamma_positions_block_all_new_entries(monkeypatch):
    from app.services import protection_health
    monkeypatch.setattr(
        "app.services.kite_engine.positions.unreadable_reason",
        lambda _: "",
    )
    monkeypatch.setattr(
        gamma_move_positions,
        "legacy_open_rows",
        lambda _: [{"status": "open", "instrument": {"tradingsymbol": "OPT"}}],
    )
    assert protection_health.admission_blocker("u", "account-a")[0] == "protection_integrity"
    assert protection_health.admission_blocker("u", "account-b")[0] == "protection_integrity"


def test_unreadable_legacy_gamma_archive_blocks_admission(monkeypatch):
    from app.services import protection_health
    monkeypatch.setattr(
        "app.services.kite_engine.positions.unreadable_reason",
        lambda _: "",
    )
    monkeypatch.setattr(
        gamma_move_positions,
        "legacy_open_rows",
        lambda _: (_ for _ in ()).throw(RuntimeError("archive read failed")),
    )
    assert protection_health.admission_blocker("u", "account-a")[0] == "protection_unreadable"


def test_active_unmigrated_legacy_gamma_exposure_refuses_new_account_risk(monkeypatch):
    import json
    from app.services import protection_health

    uid = "legacy-before-first-load"
    legacy = {
        "signal_id": "prior", "instrument": {"tradingsymbol": "OPT"},
        "entry": 100.0, "stop": 90.0, "quantity": 40, "lots": 1,
        "entered_ms": 1000, "entry_day": "2026-10-01", "status": "open",
    }
    records = {
        gamma_move_positions._key(uid): json.dumps([legacy]),
        gamma_move_positions._legacy_key(uid): "[]",
    }
    gamma_move_positions._cache.pop(uid, None)
    monkeypatch.setattr(
        db, "get_config_strict",
        lambda key, default="": records.get(key, default),
    )
    monkeypatch.setattr(
        db, "set_config_strict",
        lambda key, value: records.__setitem__(key, value),
    )
    monkeypatch.setattr(kite_positions, "unreadable_reason", lambda _: "")

    blocker, reason = protection_health.admission_blocker(uid, "live-account")

    assert blocker == "protection_integrity"
    assert "legacy Gamma" in reason
    assert json.loads(records[gamma_move_positions._key(uid)]) == []
    assert json.loads(records[gamma_move_positions._legacy_key(uid)]) == [legacy]
    gamma_move_positions._cache.pop(uid, None)
