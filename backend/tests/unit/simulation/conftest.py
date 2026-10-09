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
