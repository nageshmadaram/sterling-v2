"""Shared fixtures for the Simulation virtual-exchange tests (offline, no broker, no network)."""
import socket
import sqlite3
from contextlib import contextmanager

import pytest

from app.services.simulation.ledger import SimLedger


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch):
    """Nothing in this suite may open a connection: a virtual exchange never touches a broker."""
    attempts = []

    def refuse(*args, **kwargs):
        attempts.append(args)
        raise AssertionError(f"network access attempted: {args!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    yield attempts
    assert not attempts


@pytest.fixture
def db_factory(tmp_path):
    """A connection factory over a throwaway database file (same contract as ``db.connection``)."""
    path = tmp_path / "sim.db"

    @contextmanager
    def factory():
        conn = sqlite3.connect(path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    factory.path = path
    return factory


@pytest.fixture
def ledger(db_factory):
    return SimLedger(db_factory)


# ── a virtual exchange over a recording fake of the owner's real client ─────────────────────
from tests.unit.simulation.sim_fakes import FakeReal, ist  # noqa: E402


class Clock:
    """A settable wall clock (IST), starting mid-session on an ordinary 2026 weekday."""

    def __init__(self):
        self.value = ist(10, 16)

    def __call__(self):
        return self.value

    def advance(self, **kw):
        from datetime import timedelta
        self.value = self.value + timedelta(**kw)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def real(clock):
    return FakeReal(owner="default", clock=clock)


@pytest.fixture
def quotes(real):
    from app.services.simulation.quotes import QuoteSource
    return QuoteSource(lambda: real, owner="default", ttl_s=0, use_websocket=False)


@pytest.fixture
def exchange(ledger, quotes, clock):
    from app.services.simulation.virtual_exchange import VirtualExchange
    return VirtualExchange("default", ledger, quotes, clock=clock)
