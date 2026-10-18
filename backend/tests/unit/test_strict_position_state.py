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
    monkeypatch.setattr(gamma_move_positions, "load", lambda _: {})
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
    monkeypatch.setattr(gamma_move_positions, "load", lambda _: {})
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


def test_gamma_trade_ledger_read_error_is_not_zero_pnl(monkeypatch):
    uid = "gamma-ledger-db-down"
    gamma_move_positions._record_cache.pop(uid, None)

    def unreadable(_key):
        raise db.ControlPlaneUnavailableError("ledger unavailable")

    monkeypatch.setattr(db, "get_config_strict", unreadable)
    with pytest.raises(db.ControlPlaneUnavailableError):
        gamma_move_positions.load_record(uid)
    assert uid not in gamma_move_positions._record_cache


@pytest.mark.parametrize("raw", [
    '{"day_realised_inr": "not-a-number"}',
    '{"day_realised_inr": 1e999}',
    '{"realised_inr": null}',
    '{"trades": "bad", "wins": 0, "losses": 0}',
    '{"trades": 1, "wins": 2, "losses": 0}',
    '{"trades": 2, "consecutive_losses": -1}',
    '[]',
    '{invalid-json',
])
def test_gamma_malformed_realised_ledger_never_defaults_to_zero(
    monkeypatch, raw
):
    uid = "gamma-ledger-corrupt"
    gamma_move_positions._record_cache.pop(uid, None)
    monkeypatch.setattr(db, "get_config_strict", lambda _key: raw)

    with pytest.raises(ValueError, match="gamma_unwind_trade_ledger_unreadable"):
        gamma_move_positions.load_record(uid)
    assert uid not in gamma_move_positions._record_cache


def test_gamma_trade_ledger_write_failure_engages_halt_and_does_not_cache(
    monkeypatch,
):
    from app.engines.gamma_move import TradeRecord
    from app.services import live_safety

    uid = "gamma-ledger-disk-full"
    gamma_move_positions._record_cache.pop(uid, None)
    halts = []

    def fail_write(_key, _value):
        raise db.ControlPlaneUnavailableError("disk full")

    monkeypatch.setattr(db, "set_config_strict", fail_write)
    monkeypatch.setattr(
        live_safety, "set_kill_switch",
        lambda enabled, reason="", *, uid="default", reason_code="kill_switch":
        halts.append((enabled, reason, uid, reason_code)),
    )

    record = TradeRecord()
    record.add_realised(-1500.0, "2026-10-18")
    with pytest.raises(db.ControlPlaneUnavailableError, match="disk full"):
        gamma_move_positions.save_record(uid, record)

    assert uid not in gamma_move_positions._record_cache
    assert halts and halts[-1][0] is True
    assert "P&L write failed" in halts[-1][1]
    assert halts[-1][2] == uid
    assert halts[-1][3] == "GAMMA_PNL_PERSISTENCE_FAILED"


def test_gamma_loss_ledger_failure_blocks_other_strategies_new_risk(monkeypatch):
    from app.services import (
        protection_health, snapback_live_positions, price_rules_store,
    )

    monkeypatch.setattr(kite_positions, "unreadable_reason", lambda _: "")
    monkeypatch.setattr(kite_positions, "open_positions", lambda _: [])
    monkeypatch.setattr(gamma_move_positions, "load", lambda _: {})
    monkeypatch.setattr(gamma_move_positions, "legacy_open_rows", lambda _: [])
    monkeypatch.setattr(gamma_move_positions, "open_positions", lambda _: [])
    monkeypatch.setattr(snapback_live_positions, "open_positions", lambda _: [])
    monkeypatch.setattr(price_rules_store, "open_positions", lambda _: [])
    monkeypatch.setattr(
        gamma_move_positions, "load_record",
        lambda _: (_ for _ in ()).throw(ValueError("loss ledger corrupt")),
    )

    code, detail = protection_health.admission_blocker("operator", "kite-account")
    assert code == "protection_unreadable"
    assert "ValueError" in detail



def test_gamma_failed_position_write_evicts_speculative_cache(monkeypatch):
    from types import SimpleNamespace

    uid = "gamma-position-write-failed"
    state = SimpleNamespace(status="open", quantity=40)
    gamma_move_positions._cache[uid] = {"OPT": state}
    monkeypatch.setattr(
        gamma_move_positions, "_to_dict",
        lambda position: {"status": position.status, "quantity": position.quantity},
    )

    def refuse_write(_key, _value):
        raise db.ControlPlaneUnavailableError("SQLite write refused")

    monkeypatch.setattr(db, "set_config_strict", refuse_write)
    state.quantity = 20  # simulate a partially filled exit before persistence

    with pytest.raises(db.ControlPlaneUnavailableError, match="SQLite write refused"):
        gamma_move_positions.persist(uid)

    assert uid not in gamma_move_positions._cache


def test_gamma_archive_preserves_distinct_exposure_with_same_legacy_identity(
    monkeypatch,
):
    import json

    uid = "gamma-same-timestamp-different-exposure"
    closed = {
        "instrument": {"tradingsymbol": "OLDOPT"},
        "order_id": "OID1", "entered_ms": 1000,
        "status": "closed", "quantity": 40,
    }
    still_open = {**closed, "status": "open", "quantity": 20}
    stored = {gamma_move_positions._legacy_key(uid): json.dumps([closed])}
    monkeypatch.setattr(
        db, "get_config_strict",
        lambda key, default="": stored.get(key, default),
    )
    monkeypatch.setattr(
        db, "set_config_strict",
        lambda key, payload: stored.__setitem__(key, payload),
    )

    gamma_move_positions._archive_legacy_rows(uid, [still_open, still_open])

    archived = json.loads(stored[gamma_move_positions._legacy_key(uid)])
    assert archived == [closed, still_open]
    assert gamma_move_positions.legacy_open_rows(uid) == [still_open]
