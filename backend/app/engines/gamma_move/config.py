"""Gamma-Unwind client 0.8.2 / S1.6.0 configuration.

Signal-rule defaults mirror the user-supplied reference package's
config/strategy.yaml. Sterling-only fields are explicitly grouped at the top.
"""
from __future__ import annotations
from dataclasses import dataclass, fields

CLIENT_TOP_N_OI = 1
SPEC_VERSION = "S1.6.0"
REFERENCE_VERSION = "0.8.2"
STOP_MODES = frozenset({"monitor", "broker", "both"})
DIRECTION_MODES = frozenset({"any", "all"})


@dataclass(frozen=True)
class GammaMoveConfig:
    # Sterling integration (not Gamma-Unwind signal rules).
    enabled: bool = True
    auto_execute: bool = False
    scan_stocks: tuple[str, ...] = ()
    scan_all_stocks: bool = True
    stock_contracts: bool = True
    execution_lots: int = 1
    stop_mode: str = "monitor"

    # strategy.yaml: zones
    zone_pivot_len: int = 10
    zone_atr_len: int = 50
    zone_depth: float = 2.5
    zone_guard_mult: float = 2.0
    zone_memory: int = 20
    zone_break_on_wick: bool = False
    zone_daily_bars: int = 500

    # strategy.yaml: proximity / client watchlist
    stock_band_pct: float = 2.5
    strike_band_pct: float = 2.5
    top_n_oi: int = 2
    min_premium_ticks: float = 0.0
    min_oi_lots: float = 50.0
    min_prev_turnover_rs: float = 100000.0

    # strategy.yaml: expiry + schedule (IST)
    expiry_dte_threshold: int = 7
    mark_levels_times: tuple[str, ...] = ("09:20", "16:00")
    build_watchlists_times: tuple[str, ...] = ("09:20", "13:15")
    token_poll_minutes: int = 10

    # strategy.yaml: watcher
    base_tf_min: int = 1
    signal_tf_min: int = 5
    display_tf_min: int = 15
    oi_drop_alert: float = 0.08
    oi_drop_tolerance: float = 0.01
    oi_drop_strong: float = 0.12
    oi_rearm_extra: float = 0.04
    vol_sma_len: int = 20
    vol_build_min_of_last3: int = 2
    consol_bars: int = 6
    consol_atr_len: int = 14
    consol_range_atr: float = 1.0
    consol_drift_range_atr: float = 1.5
    h_min_range_ticks: float = 2.0
    h_max_zero_vol_slots: int = 1

    # strategy.yaml: alerts
    alert_group_rate_per_min: int = 20
    alert_per_chat_rate_per_sec: float = 1.0
    late_day_warn_ist: str = "14:30"
    expiry_warn_dte: int = 7

    # strategy.yaml: hold
    swing_days: int = 5

    # strategy.yaml: four gates
    gates_enabled: bool = True
    gates_all_lists: bool = True
    zone_max_gap_pct: float = 1.0
    liq_sma_bars: int = 20
    liq_min_avg_lots: float = 5.0
    liq_max_zero_vol_bars: int = 5
    direction_enabled: bool = True
    unwind_enabled: bool = True
    unwind_min_ticks: float = 1.0
    v1_direction_mode: str = "any"
    v1_zone_atr_k: float = 0.5
    v1_zone_max_pct: float = 2.5

    @property
    def effective_top_n_oi(self) -> int:
        # CLIENT edition is hard-pinned to one contract (HOI-1), even though
        # strategy.yaml retains the owner's top_n_oi=2 value.
        return CLIENT_TOP_N_OI

    @property
    def warmup_bars(self) -> int:
        return max(self.vol_sma_len, self.consol_atr_len + 1,
                   self.consol_bars, 3)

    def validate(self) -> "GammaMoveConfig":
        for name in (
            "zone_pivot_len", "zone_atr_len", "zone_memory", "zone_daily_bars",
            "signal_tf_min", "display_tf_min", "vol_sma_len", "consol_bars",
            "consol_atr_len", "liq_sma_bars", "swing_days", "execution_lots",
            "alert_group_rate_per_min", "token_poll_minutes",
        ):
            if int(getattr(self, name)) < 1:
                raise ValueError(f"{name} must be >= 1")
        if self.base_tf_min != 1:
            raise ValueError("base_tf_min is fixed at 1 by GAMMA-UNWIND S1.6.0")
        if self.signal_tf_min != 5:
            raise ValueError("signal_tf_min is fixed at 5 by GAMMA-UNWIND S1.6.0")
        if self.display_tf_min != 15:
            raise ValueError("display_tf_min is fixed at 15 by GAMMA-UNWIND S1.6.0")
        for name in (
            "zone_depth", "zone_guard_mult", "stock_band_pct", "strike_band_pct",
            "oi_drop_alert", "oi_drop_strong", "consol_range_atr",
            "consol_drift_range_atr", "h_min_range_ticks", "zone_max_gap_pct",
            "liq_min_avg_lots", "unwind_min_ticks", "v1_zone_max_pct",
        ):
            if float(getattr(self, name)) < 0:
                raise ValueError(f"{name} cannot be negative")
        if not 0 <= self.oi_drop_tolerance <= self.oi_drop_alert:
            raise ValueError("oi_drop_tolerance must be between 0 and oi_drop_alert")
        if self.oi_drop_strong < self.oi_drop_alert:
            raise ValueError("oi_drop_strong must be >= oi_drop_alert")
        if self.oi_rearm_extra < 0:
            raise ValueError("oi_rearm_extra cannot be negative")
        if not 1 <= self.vol_build_min_of_last3 <= 3:
            raise ValueError("vol_build_min_of_last3 must be between 1 and 3")
        if not 0 <= self.h_max_zero_vol_slots <= self.consol_bars:
            raise ValueError("h_max_zero_vol_slots must be between 0 and consol_bars")
        if self.expiry_dte_threshold < 0:
            raise ValueError("expiry_dte_threshold cannot be negative")
        if self.min_oi_lots < 0 or self.min_prev_turnover_rs < 0 or self.min_premium_ticks < 0:
            raise ValueError("liquidity floor thresholds cannot be negative")
        if self.liq_max_zero_vol_bars < 0:
            raise ValueError("liq_max_zero_vol_bars cannot be negative")
        if self.v1_direction_mode not in DIRECTION_MODES:
            raise ValueError(f"v1_direction_mode must be one of {sorted(DIRECTION_MODES)}")
        if self.stop_mode not in STOP_MODES:
            raise ValueError(f"stop_mode must be one of {sorted(STOP_MODES)}")
        if self.v1_zone_atr_k < 0 or self.v1_zone_max_pct < self.zone_max_gap_pct:
            raise ValueError("V1 dynamic zone bounds are invalid")
        if self.top_n_oi < 1:
            raise ValueError("top_n_oi must be >= 1")
        for name in ("mark_levels_times", "build_watchlists_times"):
            for value in getattr(self, name):
                parts = str(value).split(":")
                if len(parts) != 2:
                    raise ValueError(f"{name} entries must be HH:MM")
                hh, mm = int(parts[0]), int(parts[1])
                if not (0 <= hh <= 23 and 0 <= mm <= 59):
                    raise ValueError(f"{name} entries must be valid HH:MM")
        return self

    def as_dict(self) -> dict:
        out = {}
        for f in fields(self):
            value = getattr(self, f.name)
            out[f.name] = list(value) if isinstance(value, tuple) else value
        return out

    @classmethod
    def field_names(cls) -> frozenset[str]:
        return frozenset(f.name for f in fields(cls))
