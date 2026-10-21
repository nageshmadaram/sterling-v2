"""Closed-bar cross -> frozen retest or preceding-box breakout setups."""
from __future__ import annotations
from decimal import Decimal, ROUND_FLOOR
from collections.abc import Sequence
from .config import PROFILES, canonical_mode, stable_id
from .contracts import Feature, Pivot, Setup
from .features import continuous


def context_ok(f: Feature, profile) -> bool:
    return not profile.require_green or (f.st_direction == "UP" and f.context_st == "UP"
        and f.context_ema10 is not None and f.context_ema21 is not None
        and f.context_ema10 > f.context_ema21)


def evaluate_setups(symbol: str, mode: str, features: Sequence[Feature],
                    config_revision: int = 0, tick: Decimal = Decimal(".05")) -> list[Setup]:
    mode = canonical_mode(mode)
    profile = PROFILES[mode]
    if tick <= 0:
        raise ValueError("tick must be positive")
    cross = None
    touch = None
    lows: list[Pivot] = []
    out = []
    for i, f in enumerate(features):
        previous = features[i-1] if i else None
        # Missing or discontinuous source candles invalidate a cross and its
        # pending setup. Comparing EMAs across an unobserved interval could
        # manufacture a new cross and turn stale evidence into an entry.
        gap = bool(previous and not continuous(previous.bar, f.bar, profile.signal_minutes, symbol.split(":")[0],
                                               across_sessions=profile.mode == "overnight"))
        if gap:
            cross = touch = None
        lows.extend(p for p in f.pivots if p.side == "LOW")
        aligned = f.ema10 is not None and f.ema21 is not None and f.ema10 > f.ema21
        bullish = bool(previous and not gap and previous.ema10 is not None and previous.ema21 is not None
                       and previous.ema10 <= previous.ema21 and aligned)
        bearish = bool(previous and not gap and previous.ema10 is not None and previous.ema21 is not None
                       and previous.ema10 >= previous.ema21 and f.ema10 is not None
                       and f.ema21 is not None and f.ema10 < f.ema21)
        if bullish:
            cross, touch = i, None
        if bearish:
            cross = touch = None
        stop_invalidated = touch is not None and f.bar.low <= float(touch["stop"])
        if stop_invalidated:
            cross = touch = None
        reason = "WARMING_UP" if not f.ready else "MISSING_SIGNAL_BARS_RESET_SETUP" if gap else "WAITING_FOR_BULLISH_CROSS"
        status, path, stop, ceiling = "watching", None, None, None
        if stop_invalidated:
            reason, status = "SETUP_STOP_INVALIDATED", "expired"
        if cross is not None and i-cross > profile.setup_expiry_bars:
            cross = touch = None
            reason, status = "SETUP_EXPIRED", "expired"
        if f.ready and cross is not None:
            reason = "WAITING_FOR_RETEST_OR_BOX"
            a = f.atr10
            if touch is not None and i > touch["index"]+3:
                touch = None
            # Retest stop must be known at the touch. Do not replace a live touch.
            if i > cross and touch is None:
                candidates = [p for p in lows if p.occurred_index >= cross]
                if candidates:
                    pivot = candidates[-1]
                    frozen_stop = Decimal(str(pivot.price))-max(tick, Decimal(str(a))*Decimal('.10'))
                    frozen_stop = (frozen_stop/tick).to_integral_value(rounding=ROUND_FLOOR)*tick
                    for name, level in (("EMA21", f.ema21), ("EMA10", f.ema10)):
                        if f.bar.low <= level+.15*a and f.bar.high >= level-.15*a and f.bar.low > float(frozen_stop):
                            touch = {"index": i, "name": name, "stop": frozen_stop}
                            break
            confirmation = bool(previous and f.bar.close > f.bar.open
                                and f.bar.close > previous.bar.high+float(tick) and aligned)
            if touch and confirmation and f.bar.close > (f.ema21 if touch["name"] == "EMA21" else f.ema10):
                path, stop = "RETEST_"+touch["name"], touch["stop"]
            if path is None and aligned and f.bar.close > f.bar.open:
                for n in (5, 4, 3):
                    if i-n <= cross:
                        continue
                    box = features[i-n:i]
                    prior_atr = previous.atr10 if previous else None
                    if prior_atr and max(x.bar.high for x in box)-min(x.bar.low for x in box) <= 1.25*prior_atr and f.bar.close > max(x.bar.high for x in box)+float(tick) and f.bar.close > f.ema10:
                        piv = [p for p in lows if p.occurred_index >= cross and p.confirmed_index < i]
                        if piv:
                            stop = ((Decimal(str(piv[-1].price))-max(tick, Decimal(str(a))*Decimal('.1')))/tick).to_integral_value(rounding=ROUND_FLOOR)*tick
                            path = f"BOX_{n}"
                            break
            if path:
                distance = f.bar.close-float(stop)
                reason = "CONFIRMED"
                if not profile.min_stop_atr*a <= distance <= profile.max_stop_atr*a:
                    reason = "STOP_DISTANCE_OUT_OF_BOUNDS"
                elif not context_ok(f, profile):
                    reason = "ST_CONTEXT_BLOCKED"
                elif profile.chop_filter and (i < 3 or features[i-3].ema21 is None or f.ema21 <= features[i-3].ema21 or (f.ema10-f.ema21)/a < .1):
                    reason = "CHOP_FILTER_BLOCKED"
                else:
                    status = "armed"
                    ceiling = Decimal(str(f.bar.close))+Decimal(str(profile.max_chase_atr*a))
            elif bullish:
                reason = "CROSS_ARMED_NOT_ENTRY"
        out.append(Setup(id=stable_id("setup", symbol, mode, config_revision, f.bar.close_ms, path),
                         symbol=symbol, mode=mode, index=i, close_ms=f.bar.close_ms, cross_index=cross,
                         status=status, reason=reason, path=path, stop=stop, ceiling=ceiling,
                         feature=f, config_revision=config_revision))
    return out
