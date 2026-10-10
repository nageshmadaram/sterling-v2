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
