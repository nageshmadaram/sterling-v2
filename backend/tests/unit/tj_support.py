"""Shared fixtures for the Trade Journal unit tests (no broker, no network)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

IST = timezone(timedelta(hours=5, minutes=30))
OWNER = "default"
SIM = "sim:default"


def ms(hour: int, minute: int, day: int = 12, month: int = 10) -> int:
    """Epoch ms of an IST wall-clock instant in 2026."""
    return int(datetime(2026, month, day, hour, minute, tzinfo=IST).timestamp() * 1000)


@pytest.fixture
def journal_db(tmp_path, monkeypatch):
    """The journal pointed at a throwaway application database."""
    from app.services import db, trade_journal

    path = str(tmp_path / "journal.db")
    monkeypatch.setattr(db, "_DB_PATH", path)
    monkeypatch.setattr(db, "_available", True)
    trade_journal._ready_paths.discard(path)
    trade_journal._skip_seen.clear()
    trade_journal._warned.clear()
    yield trade_journal
    trade_journal._skip_seen.clear()


class Clock:
    """The journal's idea of "now": starts where the test says and moves one minute per read, so
    events recorded in call order also sort in call order."""

    def __init__(self) -> None:
        self.t = ms(10, 20)

    def at(self, hour: int, minute: int, day: int = 12) -> None:
        self.t = ms(hour, minute, day)

    def now(self) -> int:
        self.t += 60_000
        return self.t


@pytest.fixture
def clock(journal_db, monkeypatch):
    from app.services import trade_journal_adapters

    c = Clock()
    monkeypatch.setattr(journal_db, "_now_ms", c.now)
    monkeypatch.setattr(trade_journal_adapters, "_now_ms", c.now)
    return c
