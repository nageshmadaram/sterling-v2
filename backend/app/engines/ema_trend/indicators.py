"""Reference recurrences with explicit unavailable warmup, no legacy mutations."""
from __future__ import annotations

import math
from collections.abc import Sequence
from .contracts import Bar


def ema(values: Sequence[float], period: int) -> list[float | None]:
    if period < 1 or any(not math.isfinite(v) for v in values):
        raise ValueError("EMA requires positive period and finite observations")
    out: list[float | None] = [None]*len(values)
    if len(values) < period:
        return out
    previous = sum(values[:period])/period
    out[period-1] = previous
    alpha = 2/(period+1)
    for i in range(period, len(values)):
        previous = alpha*values[i]+(1-alpha)*previous
        out[i] = previous
    return out


def dema(values: Sequence[float], period: int = 50) -> list[float | None]:
    first = ema(values, period)
    valid = [v for v in first if v is not None]
    second = ema(valid, period)
    out: list[float | None] = [None]*len(values)
    for i, value in enumerate(second):
        if value is not None:
            out[i+period-1] = 2*valid[i]-value
    return out


def atr(bars: Sequence[Bar], period: int = 10) -> list[float | None]:
    if period < 1:
        raise ValueError("ATR period must be positive")
    tr = [max(b.high-b.low, abs(b.high-bars[i-1].close), abs(b.low-bars[i-1].close))
          if i else b.high-b.low for i, b in enumerate(bars)]
    out: list[float | None] = [None]*len(bars)
    if len(bars) >= period:
        value = sum(tr[:period])/period
        out[period-1] = value
        for i in range(period, len(bars)):
            value = ((period-1)*value+tr[i])/period
            out[i] = value
    return out


def supertrend(bars: Sequence[Bar], period: int = 10, factor: float = 3
               ) -> tuple[list[float | None], list[str]]:
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError("Supertrend factor must be finite and positive")
    volatility = atr(bars, period)
    lines: list[float | None] = [None]*len(bars)
    directions = ["UNAVAILABLE"]*len(bars)
    upper = lower = None
    direction = "DOWN"
    for i, b in enumerate(bars):
        a = volatility[i]
        if a is None:
            continue
        bu, bl = (b.high+b.low)/2+factor*a, (b.high+b.low)/2-factor*a
        if upper is None:
            upper, lower = bu, bl
        else:
            upper = bu if bu < upper or bars[i-1].close > upper else upper
            lower = bl if bl > lower or bars[i-1].close < lower else lower
            if direction == "DOWN" and b.close > upper:
                direction = "UP"
            elif direction == "UP" and b.close < lower:
                direction = "DOWN"
        directions[i] = direction
        lines[i] = lower if direction == "UP" else upper
    return lines, directions
