"""Startup invariant: the prospective runner may only start when the evidence
database is writable, the frozen manifest verifies, the Snapback config loads from
a reachable store, and its frozen config hash matches. Anything else is
RECOVERY_REQUIRED / HALTED — never a silent 'defaults OFF' runtime.
"""

from __future__ import annotations

import pytest

from app.services.snapback_startup import PreflightResult, run_startup_preflight


class _Cfg:
    enabled = True


def _ok_kwargs(**over):
    base = dict(
        db_writable_fn=lambda: True,
        manifest_fn=lambda: (True, []),
        config_store_available_fn=lambda: True,
        config_fn=lambda: _Cfg(),
        config_hash_fn=lambda cfg: "abc123",
        frozen_config_hash="abc123",
    )
    base.update(over)
    return base


def test_all_invariants_satisfied_allows_runner_to_start():
    result = run_startup_preflight(**_ok_kwargs())

    assert isinstance(result, PreflightResult)
    assert result.status == "READY"
    assert result.may_start_runner is True
    assert result.errors == []


def test_unwritable_evidence_database_blocks_the_runner():
    result = run_startup_preflight(**_ok_kwargs(db_writable_fn=lambda: False))

    assert result.status == "RECOVERY_REQUIRED"
    assert result.may_start_runner is False
    assert "evidence_db_not_writable" in result.errors


def test_invalid_manifest_halts():
    result = run_startup_preflight(
        **_ok_kwargs(manifest_fn=lambda: (False, ["rule_hash drift"]))
    )

    assert result.status == "HALTED"
    assert result.may_start_runner is False
    assert "manifest_invalid" in result.errors


def test_unreachable_config_store_is_never_a_silent_default():
    result = run_startup_preflight(
        **_ok_kwargs(config_store_available_fn=lambda: False)
    )

    assert result.status == "HALTED"
    assert result.may_start_runner is False
    assert "config_store_unavailable" in result.errors


def test_config_hash_drift_halts():
    result = run_startup_preflight(
        **_ok_kwargs(config_hash_fn=lambda cfg: "different")
    )

    assert result.status == "HALTED"
    assert result.may_start_runner is False
    assert "config_hash_mismatch" in result.errors


def test_disabled_strategy_is_reported_not_hidden():
    class Off:
        enabled = False

    result = run_startup_preflight(**_ok_kwargs(config_fn=lambda: Off()))

    assert result.may_start_runner is False
    assert "snapback_disabled" in result.errors


def test_config_load_exception_halts_rather_than_defaulting():
    def broken():
        raise RuntimeError("store exploded")

    result = run_startup_preflight(**_ok_kwargs(config_fn=broken))

    assert result.status == "HALTED"
    assert result.may_start_runner is False
    assert any(e.startswith("config_load_failed") for e in result.errors)


def test_repeated_halt_logs_once_per_ist_day_and_reason(monkeypatch, caplog):
    import logging
    from app.services import snapback_startup as startup
    day = ['2026-10-23']
    monkeypatch.setattr(startup, '_halt_log_days', {}, raising=False)
    monkeypatch.setattr(startup, '_preflight_log_day', lambda: day[0], raising=False)
    with caplog.at_level(logging.ERROR, logger=startup.__name__):
        for _ in range(3):
            result = startup.run_startup_preflight(**_ok_kwargs(config_hash_fn=lambda cfg: 'different'))
            assert result.status == 'HALTED' and not result.may_start_runner
        day[0] = '2026-10-26'
        startup.run_startup_preflight(**_ok_kwargs(config_hash_fn=lambda cfg: 'different'))
        startup.run_startup_preflight(**_ok_kwargs(config_store_available_fn=lambda: False))
    errors = [r for r in caplog.records if 'preflight FAILED' in r.message]
    assert len(errors) == 3
