"""Fixtures for the offline end-to-end admission dry run (see ``machine.py``)."""
from __future__ import annotations

import pytest_asyncio

from tests.e2e_live.machine import NOW, LiveMachine


@pytest_asyncio.fixture
async def machine(tmp_path, monkeypatch):
    """A machine with a frozen clock, throwaway state and the fake broker wired in.

    Application modules are NOT imported here: the root conftest seeds a healthy control
    plane into whichever database is loaded right before the test body runs, which would
    paint over exactly the fresh-install state this suite exists to examine. The test
    calls ``machine.fresh_process()`` / ``boot()`` itself.
    """
    live = LiveMachine(tmp_path, monkeypatch, now=NOW)
    live.install()
    try:
        yield live
    finally:
        await live.shutdown()
        live.teardown()
        assert not live.network_attempts, f"network access attempted: {live.network_attempts}"
