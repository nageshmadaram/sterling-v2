"""Regression: offline lifespan captures must not leak unawaited coroutines.

The production workers are never scheduled by this harness. Their coroutine
objects must be closed while the fake boot records the task names.
"""

from tests.e2e_live.machine import _CapturedTask


def test_captured_background_coroutine_is_closed_without_running() -> None:
    ran: list[bool] = []

    async def worker() -> None:
        ran.append(True)

    captured = _CapturedTask(worker(), "test-worker")

    assert captured.get_name() == "test-worker"
    # A closed coroutine has no suspended Python frame and cannot emit the
    # "coroutine was never awaited" warning when a restart drops the task.
    assert captured.coro.cr_frame is None
    assert ran == []

    assert captured.cancel()
    assert captured.done()
    assert captured.cancelled()
