"""Today-only Snapback presentation reads never change strategy/order authority."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from app.services import snapback

IST = timezone(timedelta(hours=5, minutes=30))


def stamp(day: int, hour: int = 10) -> int:
    return int(datetime(2026, 10, day, hour, tzinfo=IST).timestamp() * 1000)


def test_today_priority_row_includes_today_and_next_open_swing_candidate():
    today = date(2026, 10, 28)
    fresh = {"signal_id": "today", "timestamp_ms": stamp(28), "state": "watching",
             "trading_mode": "scalp"}
    last_close = {"signal_id": "next-open", "timestamp_ms": stamp(27, 15),
                  "state": "armed", "trading_mode": "swing"}
    old = {"signal_id": "old", "timestamp_ms": stamp(22), "state": "ended",
           "trading_mode": "swing"}
    old_minute = {"signal_id": "old-minute", "timestamp_ms": stamp(27),
                  "state": "armed", "trading_mode": "scalp"}
    restored = {**last_close, "restored": True}
    historical = {**last_close, "historical": True}
    old_armed = {**last_close, "timestamp_ms": stamp(22)}
    assert snapback._today_priority_row(fresh, day=today)
    assert snapback._today_priority_row(last_close, day=today)
    assert not snapback._today_priority_row(old, day=today)
    assert not snapback._today_priority_row(old_armed, day=today)
    assert not snapback._today_priority_row(old_minute, day=today)
    assert not snapback._today_priority_row(restored, day=today)
    assert not snapback._today_priority_row(historical, day=today)
    assert not snapback._today_priority_row({"timestamp_ms": "unreadable"}, day=today)


@pytest.mark.asyncio
async def test_today_priority_snapshot_filters_before_worker_and_preserves_scan_state(monkeypatch):
    uid = "today-only-snapshot-unit"
    state = snapback.ScanState(
        rows=[
            {"signal_id": "current", "timestamp_ms": stamp(28), "state": "watching",
             "trading_mode": "intraday"},
            {"signal_id": "old", "timestamp_ms": stamp(22), "state": "ended",
             "trading_mode": "swing"},
            {"signal_id": "next-open", "timestamp_ms": stamp(27), "state": "armed",
             "trading_mode": "swing"},
        ],
        preview_rows=[
            {"signal_id": "preview-today", "timestamp_ms": stamp(28),
             "state": "watching", "trading_mode": "scalp"},
            {"signal_id": "preview-old", "timestamp_ms": stamp(22),
             "state": "watching", "trading_mode": "scalp"},
        ],
    )
    monkeypatch.setitem(snapback._state, uid, state)
    monkeypatch.setattr(snapback, "_wall_now",
                        lambda: datetime(2026, 10, 28, 13, tzinfo=IST))
    monkeypatch.setattr(snapback, "get_effective_config",
                        lambda _: SimpleNamespace())
    def probe(_uid, _cfg, view, *, today_only=False):
        assert _uid == uid
        assert view is not state and view.rows is not state.rows
        return {"today_only": today_only, "rows": view.rows,
                "preview_rows": view.preview_rows}
    monkeypatch.setattr(snapback, "_snapshot_readonly", probe)
    today = await snapback.snapshot_async(uid, today_only=True)
    assert [r["signal_id"] for r in today["rows"]] == ["current", "next-open"]
    assert [r["signal_id"] for r in today["preview_rows"]] == ["preview-today"]
    assert today["today_only"] is True
    full = await snapback.snapshot_async(uid)
    assert [r["signal_id"] for r in full["rows"]] == ["current", "old", "next-open"]
    assert len(full["preview_rows"]) == 2
    assert full["today_only"] is False
    assert len(state.rows) == 3
    assert len(state.preview_rows) == 2
