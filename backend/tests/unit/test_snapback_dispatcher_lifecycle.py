"""The Snapback live dispatcher is part of the process, not an optional extra.

Exit management, protection retry, plan recovery and AUTO origination all run inside
``SnapbackDispatcher.cycle``. Before the lifespan started it, a manual arm placed the
real orders and then nothing managed the position. These tests pin that the loop is
started with the application and that arming is refused while it is not running.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

MAIN = Path(__file__).resolve().parents[2] / "main.py"


def test_the_lifespan_starts_and_stops_the_dispatcher_loop():
    from fastapi.testclient import TestClient

    from app.services import snapback_dispatcher
    from main import create_app

    snapback_dispatcher.stop()
    app = create_app()
    with TestClient(app):
        task = snapback_dispatcher.get_dispatcher()._task
        assert task is not None and not task.done()
        assert task.get_name() == "snapback-live-dispatcher"
        assert snapback_dispatcher.is_running()
    assert not snapback_dispatcher.is_running()


def test_main_starts_the_dispatcher_after_the_live_runtime_and_before_the_strategy_loops():
    source = MAIN.read_text(encoding="utf-8")

    assert source.count("snapback_dispatcher") >= 2     # start and stop
    start = source.index("_snapback_dispatcher.start()")
    assert source.index("start_live_runtime()") < start
    assert start < source.index("gamma_move_task = asyncio")
    assert re.search(r"^\s+snapback_dispatcher_task\s*=\s*_snapback_dispatcher\.start\(\)", source, re.M)


def test_arming_is_refused_while_the_dispatcher_is_not_running(monkeypatch):
    from app.api.v1.endpoints import snapback_plans
    from app.services import snapback_dispatcher

    snapback_dispatcher.stop()
    verdict = snapback_plans._admission(mode="LIVE", account_id="KITE-1", uid="u-dispatch")

    assert verdict["allowed"] is False
    assert "dispatcher_not_running" in verdict["reasons"]


@pytest.mark.asyncio
async def test_is_running_reflects_the_task_state():
    from app.services import snapback_dispatcher

    snapback_dispatcher.stop()
    assert not snapback_dispatcher.is_running()
    task = snapback_dispatcher.start(interval=3600.0)
    try:
        assert snapback_dispatcher.is_running()
        assert task.get_name() == "snapback-live-dispatcher"
    finally:
        snapback_dispatcher.stop()
    assert not snapback_dispatcher.is_running()
