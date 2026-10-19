import asyncio
import logging
import threading
import time
import pytest
from app.core.event_loop_watchdog import stack_locations,watch_event_loop


def test_stack_diagnostics_exclude_locals_and_source_text():
    private_value="secret-that-must-never-be-logged"
    result=stack_locations(threading.get_ident())
    assert "test_stack_diagnostics_exclude_locals_and_source_text" in result
    assert private_value not in result
    assert "private_value" not in result

@pytest.mark.asyncio
async def test_watchdog_observes_blocked_loop_and_stops(caplog, monkeypatch):
    observed=threading.Event()
    emit=caplog.handler.emit
    def recording(record):
        emit(record)
        if "Event loop stalled" in record.getMessage():observed.set()
    monkeypatch.setattr(caplog.handler,"emit",recording)
    with caplog.at_level(logging.WARNING, logger="app.core.event_loop_watchdog"):
        async with watch_event_loop(threshold=.05,check_interval=.01):
            # Block the loop until the monitor actually reports, rather than
            # relying on a 120ms OS scheduling window on a loaded host.
            assert observed.wait(3), "monitor did not observe the blocked loop"
    messages=[r.message for r in caplog.records]
    assert any("Event loop stalled" in m for m in messages)
    count=len(messages)
    await asyncio.sleep(.08)
    assert len(caplog.records)==count
