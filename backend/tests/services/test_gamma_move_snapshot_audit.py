"""Board payload: live price provenance and one-source entry eligibility."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.engines.gamma_move import (
    Candle, ContractEngine, GammaMoveConfig, InstrumentRef, WatchContract,
)
from app.services import gamma_move as service
from app.services import gamma_move_runner as runner
from app.services import gamma_move_scanner as scanner

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 10, 5, 11, 0, tzinfo=IST)
NOW_MS = int(NOW.timestamp() * 1000)


def _gate(dte, *, stop_mode="both", paper=False, tier="ENTRY", age_s=5,
          live_enabled=True, now=NOW):
    cfg = GammaMoveConfig(stop_mode=stop_mode).validate()
    return runner.entry_gate(
        cfg, tier=tier, dte=dte,
        at_ms=int(now.timestamp() * 1000) - age_s * 1000,
        paper=paper, live_enabled=live_enabled, now=now,
    )


def test_live_intraday_with_broker_stop_is_eligible():
    gate = _gate(1)
    assert gate["eligible"] is True
    assert gate["hold"] == "intraday"


def test_live_swing_states_the_reason_concisely():
    gate = _gate(5)
    assert gate["eligible"] is False
    assert gate["code"] == "reference_swing_protection_unavailable"
    assert gate["hold"] == "swing"
    assert "intraday contracts only" in gate["reason"]
    assert len(gate["reason"]) < 200


def test_live_monitor_only_stop_mode_is_not_eligible():
    gate = _gate(1, stop_mode="monitor")
    assert gate["code"] == "live_intraday_broker_protection_required"


def test_stale_and_cutoff_and_floor_and_unknown_mode_and_tier():
    assert _gate(1, age_s=400)["code"] == "signal_stale"
    late = datetime(2026, 10, 5, 15, 21, tzinfo=IST)
    assert _gate(1, age_s=5, now=late)["code"] == "intraday_entry_cutoff"
    assert _gate(1, live_enabled=False)["code"] == "live_execution_disabled"
    assert _gate(1, paper=None)["code"] == "broker_mode_unknown"
    assert _gate(1, tier="STRONG_WATCHLIST")["code"] == "tier_alert_only"


def test_non_live_mode_can_take_a_swing_entry():
    assert _gate(5, paper=True)["eligible"] is True


def _bar(i, close):
    base = datetime(2026, 10, 5, 9, 15, tzinfo=IST)
    return Candle(int((base + timedelta(minutes=5 * i)).timestamp() * 1000),
                  close, close + 1, close - 1, close, 100.0, 1000.0)


def _row(symbol, *, with_gates):
    return {
        "id": f"id:{symbol}", "tier": "ENTRY" if with_gates else "WATCHLIST",
        "at_ms": NOW_MS - 10_000,
        "instrument": {"tradingsymbol": symbol},
        "watch": {"dte": 1},
        "gates": {"passes": []} if with_gates else None,
        "levels": {"ltp": 99.0, "entry": 99.0, "stop": 90.0, "t1": 118.8, "t2": 138.6},
    }


@pytest.fixture(autouse=True)
def _clean():
    scanner.clear_runtime()
    yield
    scanner.clear_runtime()


def test_row_ltp_is_the_live_tick_not_the_frozen_signal_print(monkeypatch):
    monkeypatch.setattr(service, "_global_live_execution_enabled", lambda: True)
    cfg = GammaMoveConfig(stop_mode="both").validate()
    inst = InstrumentRef("222", "AAA26OCT100CE", "CE", 100, "2026-10-29", 10, 0.05)
    w = WatchContract("AAA", 1, "BREAKOUT-CE", inst, "HOI-1", 1, 1, 99.0, 100.0, 2.0, dte=1)
    rt = scanner.RuntimeWatch(
        contract=w, engine=ContractEngine(w, cfg),
        closed_bars=[_bar(0, 99.0)], underlying_bars=[], spot=99.0,
        forming=_bar(1, 104.5), last_tick_ms=NOW_MS - 2_000,
    )
    scanner._runtime["u1"] = {222: rt}

    rows = service._decorate_live_rows(
        "u1", cfg, [_row("AAA26OCT100CE", with_gates=True), _row("ZZZ26OCT100CE", with_gates=True)],
        paper=False, held=set(),
    )

    live, orphan = rows
    assert live["levels"]["ltp"] == pytest.approx(104.5)
    assert live["levels"]["ltp_age_s"] is not None and live["levels"]["ltp_age_s"] >= 0
    assert live["levels"]["entry"] == pytest.approx(99.0)  # plan price untouched
    assert orphan["levels"]["ltp"] is None  # no runtime: unknown, not a stale print
    assert "entry_gate" in live and "entry_gate" in orphan


def test_watch_rows_carry_no_entry_gate_and_held_contracts_are_blocked(monkeypatch):
    monkeypatch.setattr(service, "_global_live_execution_enabled", lambda: True)
    cfg = GammaMoveConfig(stop_mode="both").validate()
    rows = service._decorate_live_rows(
        "u1", cfg,
        [_row("AAA26OCT100CE", with_gates=False), _row("BBB26OCT100CE", with_gates=True)],
        paper=True, held={"BBB26OCT100CE"},
    )
    assert "entry_gate" not in rows[0]
    assert rows[1]["entry_gate"]["code"] in {"already_holding", "signal_stale"}
