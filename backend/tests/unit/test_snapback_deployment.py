"""Deployment shape a live Sterling machine must have, before it is ever started.

Sterling ships three strategies as a live, real-money app and every loop runs.
Family Mode, which used to silence all but one of them, is retired: main.py starts
every strategy loop whatever STERLING_FAMILY_MODE says, retired strategies stay
maintenance-only, and the broker-write capability guard stays on. The rest of the
contract is unchanged: a closed bind address and a recorded shutdown.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
MAIN = REPO / "backend" / "main.py"


def _main_source() -> str:
    return MAIN.read_text(encoding="utf-8")


# ------------------------------------------------- family mode is retired


def test_snapback_board_refresh_is_not_conditional():
    source = _main_source()

    board_create = "snapback_board_task = asyncio.create_task(_snapback_board_scan(interval=900))"
    assert board_create in source
    board_index = source.index(board_create)
    assert "if " not in source[max(0, board_index - 120):board_index].splitlines()[-1]


def test_main_no_longer_reads_family_mode():
    """The variable is retired as a gate: main.py must not branch on it."""
    source = _main_source()

    assert "_family_mode" not in source
    assert "family_mode_enabled" not in source


@pytest.mark.parametrize("runner", [
    "gamma_move_runner",
    "adaptive_edge_runner",
    "intraday_runner",
    "kite_engine",
])
def test_every_strategy_runner_is_always_running(runner):
    from app.services.snapback_deployment import always_running_runners

    assert runner in always_running_runners()


def test_no_runner_is_suppressed_any_more():
    import app.services.snapback_deployment as deployment

    assert not hasattr(deployment, "suppressed_runners_in_family_mode")


def test_only_retired_strategies_are_maintenance_only():
    from app.services.snapback_deployment import (
        always_running_runners,
        maintenance_only_runners,
    )

    assert maintenance_only_runners() == {"adaptive_edge_runner", "intraday_runner"}
    assert maintenance_only_runners() <= always_running_runners()
    # The three release strategies originate: none of them is maintenance-only.
    assert "gamma_move_runner" not in maintenance_only_runners()
    assert "kite_engine" not in maintenance_only_runners()


# Each runner's task variable in main.py.
_RUNNER_TASK_VARS = {
    "gamma_move_runner": "gamma_move_task",
    "adaptive_edge_runner": "adaptive_edge_task",
    "intraday_runner": "intraday_task",
    "kite_engine": "kite_engine_task",
}


def test_the_task_table_covers_every_runner():
    from app.services.snapback_deployment import always_running_runners

    assert set(_RUNNER_TASK_VARS) == set(always_running_runners())


@pytest.mark.parametrize("runner,task_var", sorted(_RUNNER_TASK_VARS.items()))
def test_main_starts_every_runner_unconditionally(runner, task_var, monkeypatch):
    # Even with the retired variable set, the loop is created at the top level of
    # the lifespan, not inside any ``if``.
    monkeypatch.setenv("STERLING_FAMILY_MODE", "true")
    source = _main_source()

    creations = list(
        re.finditer(rf"^(?P<indent>\s*){task_var}\s*=\s*asyncio\.create_task\(", source, re.M)
    )
    assert len(creations) == 1, f"{task_var} must be created exactly once in main.py"
    assert creations[0].group("indent") == "    ", (
        f"{runner} is created inside a conditional block"
    )


@pytest.mark.parametrize("module,starter", [
    ("adaptive_edge_runner", "_adaptive_edge_scan"),
    ("intraday_runner", "_intraday_scan"),
])
def test_retired_strategy_loops_start_maintenance_only(module, starter):
    source = _main_source()

    call = re.search(rf"{starter}\(\s*interval=\d+,\s*manage_only=True\s*\)", source)
    assert call, f"{module} must start with manage_only=True"


def test_the_originating_loops_do_not_start_maintenance_only():
    source = _main_source()

    assert "_gamma_move_scan(interval=300)" in source
    assert "manage_only" not in re.search(
        r"gamma_move_task = asyncio\.create_task\([^\n]*\)", source
    ).group(0)


def test_the_live_runtime_starts_before_the_strategy_loops():
    source = _main_source()

    assert "start_live_runtime()" in source
    assert source.index("start_live_runtime()") < source.index("gamma_move_task = asyncio")
    assert source.index("start_live_runtime()") < source.index("kite_engine_task = asyncio")


@pytest.mark.parametrize("operation", ["place_order", "modify_order"])
def test_the_broker_write_guard_is_on_without_family_mode(operation, monkeypatch):
    """B10: a direct entry write is refused with or without the retired variable."""
    from app.services.snapback_family_mode import guard_broker_write

    monkeypatch.delenv("STERLING_FAMILY_MODE", raising=False)

    with pytest.raises(PermissionError):
        guard_broker_write(operation)


def test_the_broker_write_guard_still_refuses_with_the_retired_variable(monkeypatch):
    from app.services.snapback_family_mode import guard_broker_write

    monkeypatch.setenv("STERLING_FAMILY_MODE", "true")

    with pytest.raises(PermissionError):
        guard_broker_write("place_order")


# ---------------------------------------------------------------- bind host


def test_the_dev_server_does_not_bind_every_interface():
    """0.0.0.0 on a home network exposes an authenticated broker session to every
    device on that network, including whatever else is on the Wi-Fi."""
    source = _main_source()

    assert '"0.0.0.0"' not in source
    # The default lives in one place, so there is a single thing to audit.
    assert "bind_host()" in source


def test_the_bind_host_is_overridable_but_defaults_closed():
    from app.services.snapback_deployment import bind_host

    import os
    os.environ.pop("STERLING_BIND_HOST", None)
    assert bind_host() == "127.0.0.1"

    os.environ["STERLING_BIND_HOST"] = "0.0.0.0"
    try:
        assert bind_host() == "0.0.0.0"
    finally:
        os.environ.pop("STERLING_BIND_HOST", None)


# ------------------------------------------------------- systemd templates


@pytest.mark.parametrize("unit", ["sterling-backend.service", "sterling-backend.watchdog.service"])
def test_a_systemd_unit_is_committed(unit):
    assert (REPO / "deploy" / "systemd" / unit).exists()


def test_the_service_unit_restarts_and_does_not_carry_secrets():
    text = (REPO / "deploy" / "systemd" / "sterling-backend.service").read_text()

    assert "Restart=always" in text
    # Secrets come from a file the unit references, never from the unit itself,
    # because the unit is committed and the environment file is not.
    assert "EnvironmentFile=" in text
    for forbidden in ("KITE_API_SECRET=", "TELEGRAM_BOT_TOKEN=", "ACCESS_TOKEN="):
        assert forbidden not in text


def test_the_watchdog_unit_probes_liveness_not_readiness():
    """Readiness is allowed to be false — a non-trading day, a pending login.
    Restarting on it would loop the service all weekend."""
    text = (REPO / "deploy" / "systemd" / "sterling-backend.watchdog.service").read_text()

    assert "/health/live" in text
    assert "/health/ready" not in text


# ------------------------------------------------------ graceful shutdown


def test_shutdown_waits_for_the_snapback_runner():
    source = _main_source()

    # The runner writes evidence. Cancelling without awaiting can tear the
    # process down between an insert and its commit.
    assert "snapback_runner_task.cancel()" in source
    assert "await snapback_runner_task" in source


def test_shutdown_drains_the_alert_outbox_worker():
    source = _main_source()

    assert "snapback_ops_task.cancel()" in source
    assert "await snapback_ops_task" in source


def test_a_shutdown_marks_the_runtime_as_stopped():
    """A process that vanished and a process that stopped cleanly look identical
    from the evidence afterwards unless one of them says so."""
    from app.services.snapback_deployment import record_shutdown

    assert callable(record_shutdown)
    assert "record_shutdown" in _main_source()
