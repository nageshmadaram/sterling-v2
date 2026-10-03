from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services import gamma_move_history

IST = timezone(timedelta(hours=5, minutes=30))


def _store(monkeypatch):
    from app.services import db

    values: dict[str, str] = {}
    monkeypatch.setattr(db, "get_config", lambda key, default=None: values.get(key, default))
    monkeypatch.setattr(db, "set_config", lambda key, value: values.__setitem__(key, value))
    return values


def test_failed_scan_preserves_last_good_watch_state(monkeypatch):
    _store(monkeypatch)
    watch = {"id": "watch:ABC", "state": "watching"}

    gamma_move_history.record_scan(
        "u1",
        "2026-10-03",
        watchlist=[watch],
        scan={"last_run_ms": 100, "stage_b": {"candidates": 1}},
        issue="",
    )
    gamma_move_history.record_scan(
        "u1",
        "2026-10-03",
        watchlist=None,
        scan={"last_run_ms": 200, "issue": "broker down"},
        issue="broker down",
    )

    row = gamma_move_history.read_day("u1", "2026-10-03")
    assert row is not None
    assert row["watchlist"] == [watch]
    assert row["scan"]["last_run_ms"] == 200
    assert row["issue"] == "broker down"


def test_signals_are_archived_by_event_day_and_deduped(monkeypatch):
    _store(monkeypatch)
    day1 = datetime(2026, 10, 2, 10, 0, tzinfo=IST)
    day2 = datetime(2026, 10, 3, 11, 0, tzinfo=IST)

    gamma_move_history.append_signals(
        "u1",
        [
            {"id": "s1", "at_ms": int(day1.timestamp() * 1000), "tier": "ENTRY"},
            {"id": "s2", "at_ms": int(day2.timestamp() * 1000), "tier": "WATCHLIST"},
        ],
    )
    gamma_move_history.append_signals(
        "u1",
        [
            {"id": "s1", "at_ms": int(day1.timestamp() * 1000), "tier": "STRONG_WATCHLIST"},
        ],
    )

    row1 = gamma_move_history.read_day("u1", "2026-10-02")
    row2 = gamma_move_history.read_day("u1", "2026-10-03")
    assert row1 is not None and len(row1["signals"]) == 1
    assert row1["signals"][0]["tier"] == "STRONG_WATCHLIST"
    assert row2 is not None and [s["id"] for s in row2["signals"]] == ["s2"]

    days = gamma_move_history.list_days("u1")
    assert [row["day"] for row in days] == ["2026-10-03", "2026-10-02"]
    assert days[0]["signals"] == 1
    assert days[1]["signals"] == 1


def test_explicit_zero_watch_scan_is_recorded_not_treated_as_missing(monkeypatch):
    _store(monkeypatch)
    gamma_move_history.record_scan(
        "u1",
        "2026-10-03",
        watchlist=[],
        scan={"last_run_ms": 300, "stage_b": {"candidates": 0}},
        issue="",
    )

    row = gamma_move_history.read_day("u1", "2026-10-03")
    assert row is not None
    assert row["recorded"] is True
    assert row["watchlist"] == []
    assert row["scan"]["stage_b"]["candidates"] == 0
