"""Supertrend pre-launch audit: regression tests for defects found end to end.

Each test names the defect it pins. They are written against the real functions
rather than against mocks of them, so a change that reintroduces the defect fails
here and not only in production.
"""
from __future__ import annotations

import time
from datetime import datetime, time as dtime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from app.domain.models import Candle
from app.engines.sterling_kite_engine.config import SterlingKiteEngineConfig


_IST = timezone(timedelta(hours=5, minutes=30))
_SLOTS = [dtime(9, 15), dtime(10, 15), dtime(11, 15), dtime(12, 15),
          dtime(13, 15), dtime(14, 15), dtime(15, 15)]


def _session_starts(last_start: datetime, n: int) -> list[int]:
    """``n`` real NSE-session 1H bar starts (09:15 … 15:15, weekdays), ending at
    ``last_start`` -- the shape Kite returns, which the CAS-aware ``drop_forming``
    filter depends on."""
    day, slot = last_start.date(), _SLOTS.index(last_start.timetz().replace(tzinfo=None))
    out: list[int] = []
    while len(out) < n:
        out.append(int(datetime.combine(day, _SLOTS[slot], tzinfo=_IST).timestamp() * 1000))
        slot -= 1
        if slot < 0:
            slot = len(_SLOTS) - 1
            day -= timedelta(days=1)
            while day.weekday() >= 5:
                day -= timedelta(days=1)
    return sorted(out)


def _session_candles(closes, *, last_start: datetime) -> list[Candle]:
    c = np.asarray(closes, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    stamps = _session_starts(last_start, len(c))
    return [Candle(timestamp_ms=stamps[i], open=float(o[i]),
                   high=float(max(o[i], c[i]) + 0.5), low=float(max(0.01, min(o[i], c[i]) - 0.5)),
                   close=float(c[i]), volume=1.0) for i in range(len(c))]


# ── held-contract scan must not read the unclosed bar ───────────────────────
class _Diag:
    def __getattr__(self, _name):
        return 0


class _Us:
    def __init__(self):
        self.scanning = False
        self.scanning_label = ""
        self.rows = []
        self.scanned_contract_symbols: set[str] = set()
        self.cancelled = False
        self.generated_ms = 0
        self.diag = _Diag()


class _Scanner:
    def __init__(self, us, candles):
        self._us, self._candles = us, candles

    def snapshot(self, _uid):
        return self._us

    async def _fetch_candles(self, *_a, **_k):
        return self._candles


@pytest.mark.asyncio
async def test_held_contract_red_count_ignores_the_forming_bar(monkeypatch):
    """The held-contract pass used to evaluate the forming bar (allow_forming=True).

    Its rows feed ``current_reds`` which ``_live_red_count`` hands to the tick
    monitor: a transient red on an UNCLOSED Heikin-Ashi bar would market-sell a
    position whose closed-bar trend is intact. Only closed bars may drive an exit.
    """
    from unittest.mock import AsyncMock

    from app.services.kite_engine import held_contract_scan as hcs
    from app.services.kite_engine import state
    from app.services.kite_engine.scanner import drop_forming, live_red_counts

    monkeypatch.setattr(state, "save_signal_cache", lambda *a, **k: None)

    # Wed 7 Oct 2026, 11:25 IST: the 11:15 bar is 10 minutes old and still forming.
    forming_start = datetime(2026, 10, 7, 11, 15, tzinfo=_IST)
    now_ms = int(datetime(2026, 10, 7, 11, 25, tzinfo=_IST).timestamp() * 1000)
    from app.services.kite_engine import scanner as scanner_mod
    monkeypatch.setattr(scanner_mod, "time", SimpleNamespace(
        time=lambda: now_ms / 1000.0, monotonic=lambda: 0.0))
    # 45 bars down, 45 bars up (a long-premium entry transition inside the window),
    # then a violent unclosed bar.
    closes = list(np.linspace(300, 120, 45)) + list(np.linspace(120, 260, 45)) + [150.0]
    candles = _session_candles(closes, last_start=forming_start)
    cfg = SterlingKiteEngineConfig()

    closed = drop_forming(candles, now_ms, exchange="NFO")
    assert len(closed) == len(candles) - 1, "fixture: last bar must be the forming one"
    assert live_red_counts(closed, cfg)["long"] == 0, "fixture: closed trend must be intact"
    assert live_red_counts(candles, cfg)["long"] >= 1, "fixture: the forming bar must flip a line"

    nfo = [{"tradingsymbol": "NIFTY26OCT24000CE", "instrument_type": "CE", "instrument_token": 777,
            "strike": 24000.0, "lot_size": 75, "expiry": "2026-10-27", "name": "NIFTY"}]
    client = AsyncMock()
    client.get_positions_raw = AsyncMock(return_value={"net": [
        {"tradingsymbol": "NIFTY26OCT24000CE", "exchange": "NFO", "quantity": 75}]})

    us = _Us()
    await hcs._append_held_contract_signals(
        _Scanner(us, candles), uid="u1", client=client, cfg=cfg,
        nfo_rows=nfo, bfo_rows=(), log_cb=None,
    )

    assert us.rows, "the held contract's running entry should still be on the board"
    for row in us.rows:
        for leg in row.legs:
            assert leg.current_reds == 0, "an unclosed bar must not move the red counter"
        assert row.current_reds == 0


# ── the setup chart must follow the same direction gate as the scanner ───────
def test_api_config_mapper_carries_allow_short():
    """``build_setup_chart`` marks entries with the endpoint's config. That mapper
    dropped ``allow_short``, so with shorts enabled the scanner took bear entries
    the chart never drew."""
    from app.api.v1.endpoints.kite_engine import _ts_cfg
    from app.engines.sterling_kite_engine.schemas import EngineConfigModel

    assert _ts_cfg(EngineConfigModel(allow_short=True)).allow_short is True
    assert _ts_cfg(EngineConfigModel(allow_short=False)).allow_short is False


# ── persisted signal board: no superseded, aged or colliding rows ───────────
_DAY_MS = 24 * 3_600_000


def _cache_row(underlying, ts, *, source="spot", option_type="CE", direction="long",
               active=False, fresh=False, symbol=""):
    return {
        "underlying": underlying, "token": 1, "exchange": "NFO", "regime": "BULL",
        "alignment": {"fast": 1, "mid": 1, "slow": 1}, "direction": direction,
        "option_type": option_type, "spot": 100.0, "stop_loss": 90.0, "score": 85.0,
        "timestamp_ms": ts, "is_active": active, "is_fresh": fresh, "source": source,
        "legs": [{"moneyness": "ATM", "option_type": option_type, "option_symbol": symbol,
                  "strike": 100.0, "expiry": "2026-10-27"}] if symbol else [],
    }


@pytest.fixture()
def signal_cache(monkeypatch):
    """The DB-backed signal cache, isolated per test."""
    from app.services.kite_engine import state
    store: dict[str, str] = {}
    monkeypatch.setattr(state.db, "get_config", lambda k: store.get(k))
    monkeypatch.setattr(state.db, "set_config", lambda k, v: store.__setitem__(k, v))
    return state


def test_cache_drops_a_superseded_active_row(signal_cache):
    """A row that has been superseded by a newer entry on the same underlying is no
    longer emitted by the scan, but the old merge kept it forever -- still flagged
    ``is_active`` -- and Adaptive Edge's ``load_live_spot_scans`` prefers active rows."""
    now = int(time.time() * 1000)
    old_ts, new_ts = now - 3 * _DAY_MS, now - _DAY_MS
    signal_cache.save_signal_cache("u", [_cache_row("NIFTY 50", old_ts, active=True)], 1)
    signal_cache.save_signal_cache("u", [_cache_row("NIFTY 50", new_ts)], 2)

    rows, _ = signal_cache.load_signal_cache("u")
    assert [r["timestamp_ms"] for r in rows] == [new_ts]


def test_cache_prunes_rows_older_than_the_board_retention(signal_cache):
    now = int(time.time() * 1000)
    signal_cache.save_signal_cache("u", [_cache_row("BANKNIFTY", now - 20 * _DAY_MS)], 1)
    signal_cache.save_signal_cache("u", [_cache_row("NIFTY 50", now - _DAY_MS)], 2)

    rows, _ = signal_cache.load_signal_cache("u")
    assert [r["underlying"] for r in rows] == ["NIFTY 50"]


def test_cache_keeps_ce_and_pe_derivative_parents_that_share_a_timestamp(signal_cache):
    """Rows were keyed (underlying, timestamp_ms): a CE and a PE derivatives parent
    fired on the same bar overwrote each other, so one vanished after a restart."""
    ts = int(time.time() * 1000) - 3_600_000
    ce = _cache_row("NIFTY 50", ts, source="derivatives", option_type="CE", symbol="N24000CE")
    pe = _cache_row("NIFTY 50", ts, source="derivatives", option_type="PE", symbol="N24000PE")
    signal_cache.save_signal_cache("u", [ce, pe], 1)

    rows, _ = signal_cache.load_signal_cache("u")
    assert sorted(r["option_type"] for r in rows) == ["CE", "PE"]


def test_cache_keeps_rows_of_underlyings_the_flush_has_not_reached(signal_cache):
    """A mid-scan flush only knows the instruments it has processed. Everything else
    must survive, or the board blanks while the scan is still running."""
    ts = int(time.time() * 1000) - 3_600_000
    signal_cache.save_signal_cache("u", [_cache_row("NIFTY 50", ts), _cache_row("SENSEX", ts)], 1)
    signal_cache.save_signal_cache("u", [_cache_row("NIFTY 50", ts, fresh=True)], 2)

    rows, _ = signal_cache.load_signal_cache("u")
    by_name = {r["underlying"]: r for r in rows}
    assert set(by_name) == {"NIFTY 50", "SENSEX"}
    assert by_name["NIFTY 50"]["is_fresh"] is True


def test_cache_load_ignores_rows_aged_out_by_an_earlier_unpruned_build(signal_cache):
    """Caches written before pruning existed may already hold ancient rows."""
    import json
    now = int(time.time() * 1000)
    signal_cache.db.set_config("kite_engine_signals_u", json.dumps({
        "rows": [_cache_row("OLD", now - 40 * _DAY_MS, active=True),
                 _cache_row("NIFTY 50", now - _DAY_MS)],
        "generated_ms": 5}))

    rows, gen = signal_cache.load_signal_cache("u")
    assert gen == 5
    assert [r["underlying"] for r in rows] == ["NIFTY 50"]


# ── Telegram lifecycle text: the numbers mean what the board says they mean ──
@pytest.fixture()
def alerts(monkeypatch):
    """Capture every lifecycle alert the SuperTrend path enqueues."""
    sent: list[dict] = []

    def _capture(uid, **kw):
        sent.append({"uid": uid, **kw})
        return "evt"

    import app.services.notifications.telegram_lifecycle as lifecycle
    monkeypatch.setattr(lifecycle, "enqueue_best_effort", _capture)
    return sent


def _open_position(uid, **over):
    from app.services.kite_engine import positions
    positions._positions.pop(uid, None)
    base = dict(uid=uid, symbol="NIFTY26OCT24000CE", exchange="NFO", token=9001,
                qty=150, lot_size=75, entry_premium=100.0, fill_price=100.0,
                stop_premium=90.0, initial_stop_premium=90.0, status=positions.OPEN,
                stop_mode="both", gtt_id=42, order_id="OID1", vehicle="otm_options",
                underlying="NIFTY 50", opened_ms=1_760_000_000_000)
    base.update(over)
    return positions.register(positions.OpenPosition(**base))


def test_close_alert_carries_entry_exit_and_signed_pnl(alerts):
    """An exit alert used to say 'Qty 150 - trail breach - Entry 100': no exit price and
    no P&L, so a profitable TSL exit and a stop-out read the same."""
    from app.services.kite_engine import positions
    uid = "tg-close"
    _open_position(uid, stop_premium=118.0)            # trailed above entry, initial 90
    positions.close(uid, "NIFTY26OCT24000CE", reason="trail breach @ ₹118.00 ≤ ₹118.00",
                    exit_price=117.5)

    (evt,) = [a for a in alerts if a["event_type"] == "POSITION CLOSED"]
    d = evt["detail"]
    assert "Entry ₹100.00" in d and "Exit ₹117.50" in d
    assert "P&L +₹2,625.00" in d                       # (117.5-100) x 150 units
    assert "Qty 150" in d
    assert "TSL" in d                                  # the stop had trailed: not an SL
    assert evt["severity"] == "INFO"                   # a winner is not a warning


def test_close_alert_for_a_stop_out_is_a_warning_labelled_sl(alerts):
    from app.services.kite_engine import positions
    uid = "tg-sl"
    _open_position(uid)                                # stop never moved: initial SL
    positions.close(uid, "NIFTY26OCT24000CE", reason="broker GTT triggered", exit_price=89.0)

    (evt,) = [a for a in alerts if a["event_type"] == "POSITION CLOSED"]
    assert "P&L -₹1,650.00" in evt["detail"]
    assert "SL" in evt["detail"] and "TSL" not in evt["detail"]
    assert evt["severity"] == "WARNING"


def test_a_broker_reduction_is_not_announced_as_an_entry_fill(alerts):
    """Reconciling down to what the broker holds called ``mark_filled`` with the smaller
    quantity, which announced 'ENTRY FILLED Qty 75' and re-stamped the open time."""
    from app.services.kite_engine import positions
    uid = "tg-shrink"
    _open_position(uid)
    positions.mark_filled(uid, "NIFTY26OCT24000CE", 100.0, filled_qty=75)

    assert [a for a in alerts if a["event_type"].startswith("ENTRY")] == []
    assert positions.get(uid, "NIFTY26OCT24000CE").qty == 75


def test_stop_alert_key_is_scoped_to_the_position_not_just_the_level(alerts):
    """Re-entering the same contract with the same stop within the outbox retention
    window produced an identical dedupe key, so the second alert was swallowed."""
    from app.services.kite_engine import positions
    uid = "tg-key"
    _open_position(uid, opened_ms=1_000, stop_premium=90.0)
    positions.update_stop(uid, "NIFTY26OCT24000CE", 95.0)
    first = alerts[-1]["dedupe_key"]
    _open_position(uid, opened_ms=2_000, stop_premium=90.0)
    positions.update_stop(uid, "NIFTY26OCT24000CE", 95.0)
    assert alerts[-1]["dedupe_key"] != first


def _trail_row(symbol, premium_sl):
    from app.engines.sterling_kite_engine.schemas import AlignmentChip, EngineSignalRow, OptionLeg
    return EngineSignalRow(
        underlying="NIFTY 50", token=256265, exchange="NFO", regime="BULL",
        alignment=AlignmentChip(fast=1, mid=1, slow=1), direction="long", option_type="CE",
        legs=[OptionLeg(moneyness="ATM", option_type="CE", option_symbol=symbol, strike=24000,
                        expiry="2026-10-27", premium_sl=premium_sl, token=9001)],
        spot=24100.0, stop_loss=24000.0, score=90.0, timestamp_ms=1_760_000_000_000,
        is_active=True)


async def _run_trail(uid, client, *, new_stop=105.0, stop_mode="both"):
    from app.engines.sterling_kite_engine.schemas import EngineConfigModel
    from app.services.kite_engine import service, state
    from app.services.kite_engine.scanner import scanner
    state.reset(uid)
    state.set_config(uid, EngineConfigModel(auto_execute=True, stop_mode=stop_mode))
    _open_position(uid, product="NRML", account_id="acct")
    scanner.snapshot(uid).rows = [_trail_row("NIFTY26OCT24000CE", new_stop)]
    await service._update_open_position_trails(client, uid)


class _TrailClient:
    _is_paper = True

    def __init__(self, *, modify_ok=True):
        self.modify_ok = modify_ok

    async def get_ltp(self, syms):
        return {s: {"last_price": 120.0} for s in syms}

    async def modify_gtt(self, tid, **kw):
        if not self.modify_ok:
            raise RuntimeError("gtt modify rejected")
        return {"trigger_id": tid}


@pytest.mark.asyncio
async def test_tsl_alert_is_sent_only_after_the_broker_stop_moved(alerts):
    await _run_trail("tg-tsl-ok", _TrailClient())
    (evt,) = [a for a in alerts if a["event_type"] == "TSL UPDATED"]
    assert "TSL ₹90.00 → ₹105.00" in evt["detail"] and "GTT #42" in evt["detail"]


@pytest.mark.asyncio
async def test_failed_broker_trail_alerts_critical_and_does_not_claim_an_update(alerts):
    """The 'TSL UPDATED ... GTT #42' alert used to be queued BEFORE the GTT modify, and a
    failed modify sent nothing: Telegram said ₹105 while Zerodha still held ₹90."""
    await _run_trail("tg-tsl-bad", _TrailClient(modify_ok=False))
    assert [a for a in alerts if a["event_type"] == "TSL UPDATED"] == []
    (evt,) = [a for a in alerts if a["event_type"] == "PROTECTION FAILED"]
    assert evt["severity"] == "CRITICAL"
    assert "₹90.00" in evt["detail"] and "₹105.00" in evt["detail"]



@pytest.mark.asyncio
async def test_unconfirmed_gtt_cancel_defers_the_exit_and_alerts_critical(alerts, monkeypatch):
    """A breach exit held back because the GTT could not be cancelled used to be an
    activity-log line only: the operator was told nothing while the stop sat unverified."""
    from app.services.kite_engine import monitor, protective_stop

    uid = "tg-defer"
    p = _open_position(uid, product="NRML", account_id="acct")

    class _Client:
        _is_paper = False
        _account_id = "acct"
        _account_generation = ""

    async def _held(*a, **k):
        return p.qty

    async def _cancel(*a, **k):
        return protective_stop.UNKNOWN

    async def _status(*a, **k):
        return protective_stop.STOP_UNVERIFIED

    monkeypatch.setattr(monitor, "_broker_holding", _held)
    monkeypatch.setattr(monitor.pstop, "cancel_stop_result", _cancel)
    monkeypatch.setattr(monitor, "_broker_stop_status", _status)
    monkeypatch.setattr(monitor.pos, "persist_strict", lambda uid: None)

    assert await monitor._exit_position(_Client(), uid, p, 85.0, reason="trail breach") is False

    (evt,) = [a for a in alerts if a["event_type"] == "EXIT DEFERRED"]
    assert evt["severity"] == "CRITICAL" and "GTT #42" in evt["detail"]
    # Re-entering the branch on the next tick must not queue a second alert.
    await monitor._exit_position(_Client(), uid, p, 85.0, reason="trail breach")
    keys = {a["dedupe_key"] for a in alerts if a["event_type"] == "EXIT DEFERRED"}
    assert len(keys) == 1


def test_open_positions_api_states_the_initial_stop():
    """The positions table names the stop SL until it trails and TSL after; it can only
    do that if the API says where the stop started."""
    from app.api.v1.endpoints.kite_engine import _position_record
    p = _open_position("api-pos", stop_premium=118.0, initial_stop_premium=90.0)
    rec = _position_record(p)
    assert rec.stop_premium == 118.0 and rec.initial_stop_premium == 90.0


def test_board_never_reports_a_time_decay_exit_the_live_executor_does_not_run():
    """A stock row used to end at bar 18 with 'time decay exit' while the live position
    stayed open: the table described a different trade from the one held."""
    import dataclasses

    from app.engines.sterling_kite_engine import exits

    class _Stalled:
        """Entry at bar 0, then 30 bars that never expand by half an ATR."""
        atr = np.full(40, 10.0)
        basis_close = np.full(40, 100.0)

    default = SterlingKiteEngineConfig()
    assert default.theta_time_stop is False
    assert exits.resolve_time_decay_exit_index(
        _Stalled(), "long", 0, 30, default, is_stock=True) is None

    # Not a vacuous pass: the rule still exists when it is asked for.
    on = dataclasses.replace(default, theta_time_stop=True)
    assert exits.resolve_time_decay_exit_index(
        _Stalled(), "long", 0, 30, on, is_stock=True) == 18


def test_time_stop_counts_session_bars_not_wall_clock_hours():
    """48 'bars' squared a position off after 48 WALL hours -- two calendar days --
    because nights and weekends were counted as bars."""
    from app.services.kite_engine.service import session_bars_held

    def ms(y, m, d, hh, mm):
        return int(datetime(y, m, d, hh, mm, tzinfo=_IST).timestamp() * 1000)

    opened = ms(2026, 10, 5, 10, 20)                      # Monday
    assert session_bars_held(opened, ms(2026, 10, 5, 15, 30)) == 6
    # Overnight adds nothing: Tuesday 09:20 is still six bars, not 23 hours.
    assert session_bars_held(opened, ms(2026, 10, 6, 9, 20)) == 6
    assert session_bars_held(opened, ms(2026, 10, 6, 10, 15)) == 7
    # A whole weekend is also free: the next Monday morning is five sessions later.
    assert session_bars_held(opened, ms(2026, 10, 12, 9, 20)) == 6 + 7 * 4
    assert session_bars_held(opened, opened) == 0
