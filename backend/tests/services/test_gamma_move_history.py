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


def test_later_build_preserves_distinct_morning_watch(monkeypatch):
    _store(monkeypatch)
    gamma_move_history.record_scan(
        "u1",
        "2026-10-01",
        watchlist=[{"id": "watch:morning", "at_ms": 1}],
        scan={"last_run_ms": 1},
    )
    gamma_move_history.record_scan(
        "u1",
        "2026-10-01",
        watchlist=[{"id": "watch:afternoon", "at_ms": 2}],
        scan={"last_run_ms": 2},
    )

    row = gamma_move_history.read_day("u1", "2026-10-01")
    assert row is not None
    assert {item["id"] for item in row["watchlist"]} == {
        "watch:morning", "watch:afternoon",
    }
    assert row["scan"]["last_run_ms"] == 2


def test_same_watch_id_is_updated_not_duplicated(monkeypatch):
    _store(monkeypatch)
    gamma_move_history.record_scan(
        "u1", "2026-10-01",
        watchlist=[{"id": "watch:one", "at_ms": 1, "reason": "WATCHING"}],
    )
    gamma_move_history.record_scan(
        "u1", "2026-10-01",
        watchlist=[{"id": "watch:one", "at_ms": 2, "reason": "ARMED"}],
    )

    row = gamma_move_history.read_day("u1", "2026-10-01")
    assert row is not None
    assert row["watchlist"] == [
        {"id": "watch:one", "at_ms": 2, "reason": "ARMED"}
    ]


def test_scan_coverage_persists_without_becoming_issue(monkeypatch):
    _store(monkeypatch)
    gamma_move_history.record_scan(
        "u1", "2026-10-01",
        watchlist=[],
        scan={"last_run_ms": 1},
        issue="",
        coverage=["using dated SterlingLake instrument master for 2026-10-01"],
    )

    row = gamma_move_history.read_day("u1", "2026-10-01")
    assert row is not None
    assert row["issue"] == ""
    assert row["coverage"] == [
        "using dated SterlingLake instrument master for 2026-10-01"
    ]


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


def test_signal_ledger_returns_multi_day_e1_rows_with_tier_counts(monkeypatch):
    _store(monkeypatch)
    day1 = datetime(2026, 10, 1, 10, 0, tzinfo=IST)
    day2 = datetime(2026, 10, 2, 11, 0, tzinfo=IST)

    gamma_move_history.append_signals("u1", [
        {"id": "entry", "at_ms": int(day2.timestamp() * 1000), "tier": "ENTRY"},
        {
            "id": "strong",
            "at_ms": int(day1.timestamp() * 1000),
            "tier": "STRONG_WATCHLIST",
        },
    ])

    ledger = gamma_move_history.signal_ledger("u1", days=60, limit=50)

    assert ledger["count"] == 2
    assert [row["id"] for row in ledger["signals"]] == ["entry", "strong"]
    assert ledger["signals"][0]["history_day"] == "2026-10-02"
    assert ledger["tier_counts"] == {
        "entry": 1,
        "strong_watchlist": 1,
        "watchlist_e1": 0,
    }
    assert ledger["days"][0]["entry"] == 1
    assert ledger["days"][1]["strong_watchlist"] == 1


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


def test_replay_is_cached_but_never_overwrites_live_recorded_evidence(monkeypatch):
    _store(monkeypatch)
    replay = gamma_move_history.record_replay("u1", {
        "day": "2026-10-01",
        "source": "historical_replay",
        "resolution": "1minute_close",
        "watchlist": [{"id": "watch:r"}],
        "signals": [{"id": "r1", "at_ms": 1}],
        "scan": {"replay_resolution": "1minute_close"},
        "coverage": ["current chain coverage"],
        "issue": "",
        "complete": True,
    })
    assert replay["source"] == "historical_replay"
    assert replay["replay"] == {
        "resolution": "1minute_close",
        "complete": True,
    }
    assert gamma_move_history.list_days("u1")[0]["resolution"] == "1minute_close"

    gamma_move_history.record_scan(
        "u1",
        "2026-10-01",
        watchlist=[{"id": "watch:live"}],
        scan={"last_run_ms": 10},
        issue="",
    )
    live = gamma_move_history.read_day("u1", "2026-10-01")
    assert live is not None
    assert live["source"] == "live_recorded"
    assert live["watchlist"] == [{"id": "watch:live"}]
    assert live["replay"] == {}
    assert live["coverage"] == []

    ignored = gamma_move_history.record_replay("u1", {
        "day": "2026-10-01",
        "watchlist": [{"id": "watch:should-not-win"}],
        "signals": [{"id": "r2", "at_ms": 2}],
        "scan": {},
        "coverage": [],
        "issue": "",
        "complete": True,
    })
    assert ignored["source"] == "live_recorded"
    assert ignored["watchlist"] == [{"id": "watch:live"}]
