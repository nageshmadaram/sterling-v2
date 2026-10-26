"""GAMMA-UNWIND client E2/E1 watch engine (S1.6.0).

Client edition keeps E2 internal. A+B+C+H on a CLOSED 5m bar arms the
contract; E1 is a LIVE breakout tick with forming-bar volume above SMA20.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from functools import lru_cache
from typing import Sequence

import numpy as np

from .config import GammaMoveConfig
from .levels import atr_series
from .models import (
    Candle, Evaluation, GammaSignal, TIER_ENTRY, TIER_NEAR_ENTRY,
    TIER_STRONG_WATCHLIST, TIER_WATCH_ONLY, WatchContract,
)
from .regime import build_gates, signal_warnings

IST = timezone(timedelta(hours=5, minutes=30))
EPS = 1e-9
TF_MS = 5 * 60_000
SESSION_OPEN_MIN = 9 * 60 + 15


# Only immutable clock conversions are memoized, never account or market state.
@lru_cache(maxsize=8192)
def ist_date(ts_ms: int) -> date:
    return datetime.fromtimestamp(ts_ms / 1000, IST).date()


def oi_drop(oi_now: float, oi_peak: float) -> float | None:
    if not oi_peak or oi_peak <= 0 or np.isnan(oi_peak) or np.isnan(oi_now):
        return None
    return (oi_peak - oi_now) / oi_peak


def a_holds(drop: float, cfg: GammaMoveConfig) -> bool:
    # Supplied source: 8% line minus 1 percentage point tolerance = 7%.
    return drop + EPS >= cfg.oi_drop_alert - cfg.oi_drop_tolerance


def vol_sma(bars: Sequence[Candle], length: int) -> float | None:
    # Client S1.6.0 uses the inclusive 20-bar mean for the closed-bar B test.
    window = list(bars)[-length:]
    return sum(b.volume for b in window) / length if len(window) >= length else None


def atr_last(bars: Sequence[Candle], length: int) -> float | None:
    if len(bars) < length + 1:
        return None
    h = np.asarray([b.high for b in bars], dtype=float)
    l = np.asarray([b.low for b in bars], dtype=float)
    c = np.asarray([b.close for b in bars], dtype=float)
    value = float(atr_series(h, l, c, length)[-1])
    return None if np.isnan(value) else value


@lru_cache(maxsize=512)
def _session_open_ms(day: date) -> int:
    base = datetime(day.year, day.month, day.day, tzinfo=IST)
    return int(base.timestamp() * 1000) + SESSION_OPEN_MIN * 60_000


def zero_volume_slots(bars: Sequence[Candle], n_slots: int,
                      tf_min: int = 5) -> int:
    """Reference H rule: missing 5m clock slots count as zero volume."""
    if not bars or n_slots <= 0:
        return max(n_slots, 0)
    tf_ms = tf_min * 60_000
    by_date: dict[date, list[Candle]] = {}
    for b in bars:
        by_date.setdefault(ist_date(b.ts_ms), []).append(b)
    dates = sorted(by_date)
    opens = {d: _session_open_ms(d) for d in dates}
    start: dict[date, int] = {dates[-1]: 0}
    for k in range(len(dates) - 2, -1, -1):
        d = dates[k]
        last_slot = max(
            (b.ts_ms - opens[d]) // tf_ms for b in by_date[d]
        )
        start[d] = start[dates[k + 1]] - 1 - last_slot

    def pos(b: Candle) -> int:
        d = ist_date(b.ts_ms)
        return start[d] + (b.ts_ms - opens[d]) // tf_ms

    end = pos(bars[-1])
    first = end - n_slots + 1
    traded: set[int] = set()
    for b in reversed(bars):
        p = pos(b)
        if p < first:
            break
        if p <= end and b.volume > 0:
            traded.add(p)
    return n_slots - len(traded)


def oi_peak_bar(bars: Sequence[Candle], peak: float) -> Candle | None:
    if not bars:
        return None
    day = ist_date(bars[-1].ts_ms)
    today = [b for b in bars if ist_date(b.ts_ms) == day]
    for b in today:
        if abs(b.oi - peak) < 0.5:
            return b
    return max((b for b in today if b.oi > 0),
               key=lambda b: b.oi, default=None)


def evaluate(bars: Sequence[Candle], oi_now: float, oi_peak_value: float,
             cfg: GammaMoveConfig, tick_size: float) -> Evaluation:
    last = bars[-1]
    ev = Evaluation(ts_ms=last.ts_ms)
    drop = oi_drop(oi_now, oi_peak_value)
    if drop is not None:
        ev = Evaluation(
            **{**ev.__dict__, "oi_drop": drop, "a": a_holds(drop, cfg),
               "strong": drop >= cfg.oi_drop_strong}
        )
    sma = vol_sma(bars, cfg.vol_sma_len)
    if sma is not None and sma > 0:
        recent = bars[-3:]
        count = sum(1 for b in recent if b.volume > sma)
        ev = Evaluation(
            **{**ev.__dict__, "sma": sma, "vol_ratio": last.volume / sma,
               "b_count": count,
               "b": last.volume > sma and count >= cfg.vol_build_min_of_last3}
        )
    atr_v = atr_last(bars, cfg.consol_atr_len)
    window = list(bars)[-cfg.consol_bars:]
    if atr_v is not None and atr_v > 0 and len(window) == cfg.consol_bars:
        lo = min(b.low for b in window)
        hi = max(b.high for b in window)
        rng = hi - lo
        drift = window[-1].close - window[0].close
        c_ok = (
            rng <= cfg.consol_range_atr * atr_v
            or (drift <= 0 and rng <= cfg.consol_drift_range_atr * atr_v)
        )
        zero = zero_volume_slots(bars, cfg.consol_bars, cfg.signal_tf_min)
        h_ok = (
            rng >= cfg.h_min_range_ticks * tick_size - EPS
            and zero <= cfg.h_max_zero_vol_slots
        )
        ev = Evaluation(
            **{**ev.__dict__, "atr": atr_v, "range": rng, "drift": drift,
               "c": c_ok, "consol_lo": lo, "consol_hi": hi,
               "h": h_ok, "zero_vol_slots": zero, "h_tick": tick_size}
        )
    return ev


class ContractState(str, Enum):
    IDLE = "IDLE"
    ARMED = "ARMED"
    DONE = "DONE"


@dataclass
class ContractEngine:
    contract: WatchContract
    cfg: GammaMoveConfig
    state: ContractState = ContractState.IDLE
    consol_lo: float | None = None
    consol_hi: float | None = None
    oi_at_alert: float | None = None
    cycle: dict = field(default_factory=dict)
    last_eval: Evaluation | None = None

    def reset_day(self) -> None:
        self.state = ContractState.IDLE
        self.consol_lo = self.consol_hi = self.oi_at_alert = None
        self.cycle = {}
        self.last_eval = None

    def on_bar_close(
        self,
        bars: Sequence[Candle],
        *,
        oi_now: float | None = None,
        oi_peak_value: float | None = None,
    ) -> None:
        """Evaluate one closed 5m bar using the reference client's OI inputs.

        Live callers pass the tick-level OI at close and the intraday tick peak
        tracked from 09:15. Historical replay may omit them and falls back to
        bar-close OI because intrabar OI extrema are not present in Kite 5m
        candles.
        """
        if len(bars) < self.cfg.warmup_bars:
            return
        day = ist_date(bars[-1].ts_ms)
        daybars = [b for b in bars if ist_date(b.ts_ms) == day]
        if not daybars:
            return
        if oi_now is None:
            oi_now = daybars[-1].oi
        peak = (
            float(oi_peak_value)
            if oi_peak_value is not None and float(oi_peak_value) > 0
            else max((b.oi for b in daybars if b.oi > 0), default=0.0)
        )

        if (self.state in (ContractState.ARMED, ContractState.DONE)
                and self.oi_at_alert
                and oi_now <= self.oi_at_alert * (1.0 - self.cfg.oi_rearm_extra)):
            self.state = ContractState.IDLE
            self.consol_lo = self.consol_hi = None
            self.cycle = {}

        ev = evaluate(bars, float(oi_now), peak, self.cfg,
                      self.contract.instrument.tick_size)
        self.last_eval = ev
        if self.state is not ContractState.IDLE:
            return
        if not (ev.a and ev.b and ev.c and ev.h):
            return
        if ev.consol_lo is None or ev.consol_hi is None:
            return
        peak_bar = oi_peak_bar(bars, peak)
        self.state = ContractState.ARMED
        self.consol_lo = ev.consol_lo
        self.consol_hi = ev.consol_hi
        self.oi_at_alert = float(oi_now)
        self.cycle = {
            "cycle_e2_ts_ms": bars[-1].ts_ms,
            "cycle_e2_close": bars[-1].close,
            "oi_peak_bar_ts_ms": peak_bar.ts_ms if peak_bar else None,
            "oi_peak_bar_close": peak_bar.close if peak_bar else None,
            "oi_peak_bar_oi": peak_bar.oi if peak_bar else None,
        }

    def closed_bar_signal(self, closed: Sequence[Candle], spot: float,
                          underlying_bars: Sequence[Candle], *,
                          oi_peak_value: float | None = None,
                          entry_ltp: float | None = None) -> GammaSignal | None:
        """Sterling experimental close-only mode; NOT the supplied client rule.

        Uses the actual candle close and preserves all four final gates. The
        source-backed default remains the separate live-breakout path.
        """
        if self.cfg.entry_trigger != "closed_5m" or not closed or self.state is ContractState.DONE:
            return None
        ev = self.last_eval
        bar = closed[-1]
        if ev is None or ev.ts_ms != bar.ts_ms or not (ev.a and ev.b and ev.c and ev.h):
            return None
        peak = float(oi_peak_value or max((b.oi for b in closed if ist_date(b.ts_ms) == ist_date(bar.ts_ms)), default=0))
        drop = oi_drop(float(bar.oi), peak)
        if drop is None or not a_holds(drop, self.cfg) or bar.close <= 0:
            return None
        peak_bar = oi_peak_bar(closed, peak)
        at_ms = bar.ts_ms + self.cfg.signal_tf_min * 60_000
        snapshot = {**ev.as_dict(), "cycle_e2_ts_ms":bar.ts_ms,
            "cycle_e2_close":bar.close,"bar_ts_ms":bar.ts_ms,
            "signal_ts_ms":at_ms,"signal_price":bar.close,
            "oi_now":bar.oi,"oi_peak":peak,"tick_size":self.contract.instrument.tick_size,
            "oi_peak_bar_close":peak_bar.close if peak_bar else None,
            "oi_peak_bar_oi":peak_bar.oi if peak_bar else None,
            "entry_trigger":"closed_5m", "candle_close":bar.close}
        gates = build_gates(self.contract,spot=spot,option_bars=closed,
            underlying_bars=underlying_bars,snapshot=snapshot,cfg=self.cfg)
        tier = TIER_NEAR_ENTRY if self.contract.carried and gates.tier == TIER_ENTRY else gates.tier
        observed_entry = float(entry_ltp) if entry_ltp is not None and entry_ltp > 0 else float(bar.close)
        snapshot["signal_price"] = observed_entry
        snapshot["entry_price_source"] = "observed_tick" if entry_ltp is not None and entry_ltp > 0 else "closed_candle_proxy"
        signal = GammaSignal(id=f"{self.contract.instrument.tradingsymbol}:{self.contract.list_name}:{bar.ts_ms}",
            at_ms=at_ms,contract=self.contract,ltp=observed_entry,oi_drop_pct=drop*100,
            vol_ratio=float(ev.vol_ratio or 0),strong=drop>=self.cfg.oi_drop_strong,
            base_low=float(ev.consol_lo),base_high=float(ev.consol_hi),
            reason=f"5m close: OI drop {drop*100:.1f}%, volume {ev.vol_ratio:.2f}x SMA{self.cfg.vol_sma_len}",
            snapshot=snapshot,gates=gates,tier=tier,warnings=signal_warnings(at_ms,self.contract.dte,self.cfg))
        self.oi_at_alert=float(bar.oi)
        self.state=ContractState.DONE
        return signal

    def restore_recorded_e1(self, signal: GammaSignal) -> None:
        """Silently apply an already-recorded E1 during restart/rebuild replay.

        The reference client replays today's bars and recorded E1s in timestamp
        order. Applying the E1 closes exactly that arming cycle (DONE) and sets
        the re-arm OI reference to the signal-time OI; later replayed closed bars
        may then legitimately reset it after the configured extra OI drop.
        """
        if signal.contract.instrument.tradingsymbol != self.contract.instrument.tradingsymbol:
            return
        snap = signal.snapshot or {}
        cycle_ts = snap.get("cycle_e2_ts_ms")
        if cycle_ts is not None:
            self.cycle["cycle_e2_ts_ms"] = int(cycle_ts)
        for key in (
            "cycle_e2_close",
            "oi_peak_bar_ts_ms",
            "oi_peak_bar_close",
            "oi_peak_bar_oi",
        ):
            if snap.get(key) is not None:
                self.cycle[key] = snap.get(key)
        self.consol_lo = float(signal.base_low)
        self.consol_hi = float(signal.base_high)
        try:
            self.oi_at_alert = float(snap.get("oi_now"))
        except (TypeError, ValueError):
            # A damaged recorded E1 must not create a DONE state that can never
            # re-arm. Keep the engine's E2 reference when one exists.
            if self.oi_at_alert is None:
                return
        self.state = ContractState.DONE

    def breakout(self, closed: Sequence[Candle], forming: Candle, ltp: float,
                 spot: float | None, underlying_bars: Sequence[Candle],
                 *, tick_ts_ms: int, oi_now: float | None = None,
                 oi_peak_value: float | None = None) -> GammaSignal | None:
        if (self.state is not ContractState.ARMED
                or self.consol_hi is None or self.consol_lo is None):
            return None
        window = list(closed)[-self.cfg.vol_sma_len:]
        if len(window) < self.cfg.vol_sma_len:
            return None
        sma = sum(b.volume for b in window) / self.cfg.vol_sma_len
        if sma <= 0 or ltp <= self.consol_hi or forming.volume <= sma:
            return None

        day = ist_date(forming.ts_ms)
        daybars = [
            b for b in [*closed, forming] if ist_date(b.ts_ms) == day
        ]
        current_oi = float(forming.oi if oi_now is None else oi_now)
        peak = (
            float(oi_peak_value)
            if oi_peak_value is not None and float(oi_peak_value) > 0
            else max((b.oi for b in daybars if b.oi > 0), default=0.0)
        )
        drop = oi_drop(current_oi, peak) or 0.0
        # A is an E2 arming condition in the original source, not a second
        # veto at E1. A rebound in OI does not cancel a still-armed cycle.
        ratio = forming.volume / sma
        snapshot = {
            **(self.last_eval.as_dict() if self.last_eval else {}),
            **self.cycle,
            "bar_ts_ms": forming.ts_ms,
            "signal_ts_ms": int(tick_ts_ms),
            "signal_price": ltp,
            "oi_now": current_oi,
            "oi_peak": peak,
            "tick_size": self.contract.instrument.tick_size,
        }
        gates = build_gates(
            self.contract,
            spot=spot,
            option_bars=closed,
            underlying_bars=underlying_bars,
            snapshot=snapshot,
            cfg=self.cfg,
        )
        tier = gates.tier
        # Client reference: carried contracts are watch-only and cannot be ENTRY.
        if self.contract.carried and tier == TIER_ENTRY:
            tier = TIER_NEAR_ENTRY
        signal = GammaSignal(
            id=(f"{self.contract.instrument.tradingsymbol}:"
                f"{self.contract.list_name}:{forming.ts_ms}"),
            at_ms=int(tick_ts_ms),
            contract=self.contract,
            ltp=float(ltp),
            oi_drop_pct=drop * 100.0,
            vol_ratio=ratio,
            strong=drop >= self.cfg.oi_drop_strong,
            base_low=self.consol_lo,
            base_high=self.consol_hi,
            reason=(f"E1 breakout: tick {ltp:g} > consol_hi {self.consol_hi:g}, "
                    f"forming vol {ratio:.2f}x SMA{self.cfg.vol_sma_len}"),
            snapshot=snapshot,
            gates=gates,
            tier=tier,
            warnings=signal_warnings(
                int(tick_ts_ms), self.contract.dte, self.cfg
            ),
        )
        self.oi_at_alert = current_oi
        self.state = ContractState.DONE
        return signal
