from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest

from app.services import gamma_move_history

IST = timezone(timedelta(hours=5, minutes=30))


def _store(monkeypatch):
    from app.services import db

    values: dict[str, str] = {}
    monkeypatch.setattr(db, "get_config_strict", lambda key, default=None: values.get(key, default))
    monkeypatch.setattr(db, "set_config_strict", lambda key, value: values.__setitem__(key, value))
    return values



def test_signal_ledger_marks_incomplete_historical_replay_as_partial():
    from app.services import gamma_move_history

    gamma_move_history.record_replay('u-ledger-partial', {
        'day': '2026-10-01',
        'source': 'historical_replay',
        'resolution': '1minute_ohlcv_proxy',
        'watchlist': [],
        'signals': [{'id': 'partial-e1', 'at_ms': 1790820000000, 'tier': 'ENTRY'}],
        'scan': {},
        'coverage': ['RELIANCE: temporary option-history outage'],
        'issue': 'historical replay incomplete: temporary option-history outage',
        'complete': False,
    })

    ledger = gamma_move_history.signal_ledger('u-ledger-partial')

    assert ledger['count'] == 1
    assert ledger['signals'][0]['history_complete'] is False
    assert ledger['signals'][0]['source'] == 'historical_replay'
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
        "resolution": "1minute_ohlcv_proxy",
        "watchlist": [{"id": "watch:r"}],
        "signals": [{"id": "r1", "at_ms": 1}],
        "scan": {"replay_resolution": "1minute_ohlcv_proxy"},
        "coverage": ["current chain coverage"],
        "issue": "",
        "complete": True,
    })
    assert replay["source"] == "historical_replay"
    assert replay["replay"] == {
        "resolution": "1minute_ohlcv_proxy",
        "complete": True,
    }
    assert gamma_move_history.list_days("u1")[0]["resolution"] == "1minute_ohlcv_proxy"

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


def test_incomplete_or_failed_history_surfaces_as_available_replay(monkeypatch):
    _store(monkeypatch)

    gamma_move_history.record_scan(
        "u1",
        "2026-10-02",
        watchlist=None,
        scan={"last_run_ms": 1, "issue": "no Kite session"},
        issue="no Kite session",
    )
    gamma_move_history.record_replay("u1", {
        "day": "2026-10-01",
        "source": "historical_replay",
        "resolution": "1minute_ohlcv_proxy",
        "watchlist": [],
        "signals": [],
        "scan": {},
        "coverage": ["broker history outage"],
        "issue": "all selected Gamma-Unwind contracts failed historical replay",
        "complete": False,
    })

    by_day = {row["day"]: row for row in gamma_move_history.list_days("u1")}

    assert by_day["2026-10-02"]["source"] == "available_replay"
    assert by_day["2026-10-01"]["source"] == "available_replay"


def test_day_index_is_served_from_one_read_not_one_blob_per_day(monkeypatch):
    from app.services import db

    values = _store(monkeypatch)
    for n in range(1, 6):
        gamma_move_history.record_scan(
            "u-index", f"2026-09-{n:02d}",
            watchlist=[{"id": f"w{n}", "instrument": {"tradingsymbol": "X"}, "list_name": "A"}],
            scan={"last_run_ms": n},
        )
    reads: list[str] = []
    real_get = db.get_config_strict

    def counting_get(key, default=None):
        reads.append(key)
        return real_get(key, default)

    monkeypatch.setattr(db, "get_config_strict", counting_get)

    days = gamma_move_history.list_days("u-index")

    assert [row["day"] for row in days] == [f"2026-09-0{n}" for n in (5, 4, 3, 2, 1)]
    assert all(row["watches"] == 1 for row in days)
    assert reads == [gamma_move_history._index_key("u-index")]
    assert values  # nothing was mutated by a read


def test_legacy_day_only_index_is_read_through_and_upgraded(monkeypatch):
    values = _store(monkeypatch)
    gamma_move_history.record_scan(
        "u-legacy", "2026-09-01", watchlist=[], scan={"last_run_ms": 1},
    )
    # Pre-summary index: a bare list of day strings.
    values[gamma_move_history._index_key("u-legacy")] = '["2026-09-01"]'

    first = gamma_move_history.list_days("u-legacy")
    assert first and first[0]["day"] == "2026-09-01"

    gamma_move_history.record_scan(
        "u-legacy", "2026-09-02", watchlist=[], scan={"last_run_ms": 2},
    )
    assert [r["day"] for r in gamma_move_history.list_days("u-legacy")] == [
        "2026-09-02", "2026-09-01",
    ]


def test_history_read_failure_is_not_an_empty_ledger(monkeypatch):
    import pytest
    from app.services import db
    def failed_read(*args):
        raise db.ControlPlaneUnavailableError("database unavailable")
    monkeypatch.setattr(db, "get_config_strict", failed_read)
    with pytest.raises(db.ControlPlaneUnavailableError):
        gamma_move_history.signal_ledger("operator")


def test_history_write_failure_is_not_success(monkeypatch):
    import pytest
    from app.services import db
    _store(monkeypatch)
    def failed_write(*args):
        raise db.ControlPlaneUnavailableError("database unavailable")
    monkeypatch.setattr(db, "set_config_strict", failed_write)
    with pytest.raises(db.ControlPlaneUnavailableError):
        gamma_move_history.record_scan("operator", "2026-10-10", watchlist=[], scan={}, issue="")


def test_corrupt_history_cannot_be_overwritten_as_an_empty_day(monkeypatch):
    import pytest
    values = _store(monkeypatch)
    key = "gamma_unwind_history:operator:2026-10-10"
    values[key] = "{broken"
    with pytest.raises(gamma_move_history.HistoryUnavailableError):
        gamma_move_history.record_scan("operator", "2026-10-10", watchlist=[], scan={}, issue="")
    assert values[key] == "{broken"


def test_wrong_history_shapes_are_unavailable(monkeypatch):
    import pytest
    values = _store(monkeypatch)
    values["gamma_unwind_history_days:operator"] = "{}"
    with pytest.raises(gamma_move_history.HistoryUnavailableError):
        gamma_move_history.list_days("operator")
    values["gamma_unwind_history:operator:2026-10-10"] = "[]"
    with pytest.raises(gamma_move_history.HistoryUnavailableError):
        gamma_move_history.read_day("operator", "2026-10-10")


@pytest.mark.parametrize("raw", ["null", '{"signals": [NaN]}'])
def test_null_and_nonfinite_stored_history_are_unavailable(monkeypatch, raw):
    values = _store(monkeypatch)
    values["gamma_unwind_history:operator:2026-10-10"] = raw
    with pytest.raises(gamma_move_history.HistoryUnavailableError):
        gamma_move_history.read_day("operator", "2026-10-10")
