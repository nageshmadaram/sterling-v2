"""Causal feature assembly and exchange-session aggregation."""
from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from collections.abc import Sequence

from .contracts import Bar, Feature
from .indicators import ema, dema, atr, supertrend
from .pivots import confirmed_pivots
from .calendar import regular_close

IST = ZoneInfo("Asia/Kolkata")


def validate_bars(bars: Sequence[Bar]) -> None:
    for i, b in enumerate(bars):
        if i and (b.open_ms <= bars[i-1].open_ms or b.close_ms <= bars[i-1].close_ms):
            raise ValueError("DUPLICATE_OR_OUT_OF_ORDER_BAR")
        if i and b.adjustment_version != bars[i-1].adjustment_version:
            raise ValueError("MIXED_ADJUSTMENT_VERSION")
        if i and b.available_ms < bars[i-1].available_ms:
            raise ValueError("OUT_OF_ORDER_AVAILABILITY")


def aggregate_completed_bars(bars: Sequence[Bar], minutes: int, asof_ms: int,
                             exchange: str = "NSE") -> list[Bar]:
    """Require full constituent coverage and a verified exchange calendar.

    Input daily bars for weekly output; input uniform smaller intraday bars for
    minute aggregation. An incomplete final session bucket is never emitted.
    """
    from .calendar import historical_regular_session as is_trading_session
    validate_bars(bars)
    if minutes <= 0:
        raise ValueError("invalid aggregation interval")
    groups: dict[object, list[Bar]] = defaultdict(list)
    for b in bars:
        day = datetime.fromtimestamp(b.open_ms/1000, IST).date()
        if not is_trading_session(day, exchange):
            raise ValueError("BAR_OUTSIDE_VERIFIED_SESSION")
        opening = datetime.combine(day, time(9, 15), IST)
        if minutes == 10080:
            groups[day-timedelta(days=day.weekday())].append(b)
        elif minutes == 1440:
            groups[day].append(b)
        else:
            offset = int((b.open_ms/1000-opening.timestamp())//60)
            if offset < 0 or offset >= int((datetime.combine(day,regular_close(day,exchange),IST)-opening).total_seconds()/60):
                raise ValueError("BAR_OUTSIDE_REGULAR_SESSION")
            groups[(day, offset//minutes)].append(b)
    result = []
    for key, parts in sorted(groups.items()):
        if minutes == 10080:
            expected_days = [key+timedelta(days=d) for d in range(7)
                             if is_trading_session(key+timedelta(days=d), exchange)]
            actual_days = [datetime.fromtimestamp(b.open_ms/1000, IST).date() for b in parts]
            if actual_days != expected_days:
                continue
            ending = datetime.combine(expected_days[-1], regular_close(expected_days[-1],exchange), IST)
            if any(b.close_ms != int(datetime.combine(d, regular_close(d,exchange), IST).timestamp()*1000)
                   for b, d in zip(parts, actual_days)):
                raise ValueError("WEEKLY_REQUIRES_COMPLETED_DAILY_INPUT")
        else:
            day = key if minutes == 1440 else key[0]
            start = datetime.combine(day, time(9, 15), IST)
            if minutes != 1440:
                start += timedelta(minutes=key[1]*minutes)
            ending = min(start+timedelta(minutes=minutes), datetime.combine(day, regular_close(day,exchange), IST))
            expected_start = int(start.timestamp()*1000)
            if parts[0].open_ms != expected_start or parts[-1].close_ms != int(ending.timestamp()*1000):
                continue
            if any(a.close_ms != b.open_ms for a, b in zip(parts, parts[1:])):
                continue
        available = max(parts[-1].close_ms, max(b.available_ms for b in parts))
        if available > asof_ms or int(ending.timestamp()*1000) > asof_ms:
            continue
        result.append(Bar(open_ms=parts[0].open_ms, close_ms=int(ending.timestamp()*1000),
                          available_ms=available, open=parts[0].open, high=max(b.high for b in parts),
                          low=min(b.low for b in parts), close=parts[-1].close,
                          volume=sum(b.volume for b in parts), provider=parts[0].provider,
                          adjustment_version=parts[0].adjustment_version))
    return result


def build_features(bars: Sequence[Bar], context_bars: Sequence[Bar]) -> list[Feature]:
    validate_bars(bars)
    validate_bars(context_bars)
    closes = [b.close for b in bars]
    e10, e21, d50, a10 = ema(closes, 10), ema(closes, 21), dema(closes), atr(bars)
    st, direction = supertrend(bars)
    cx = [b.close for b in context_bars]
    c10, c21 = ema(cx, 10), ema(cx, 21)
    cst, cdir = supertrend(context_bars)
    # Availability can lag close; a later-arriving context cannot backfill a signal.
    if any(a.available_ms > b.available_ms for a, b in zip(context_bars, context_bars[1:])):
        raise ValueError("OUT_OF_ORDER_CONTEXT_AVAILABILITY")
    available = [b.available_ms for b in context_bars]
    pivots = confirmed_pivots(bars)
    by_index: dict[int, list] = defaultdict(list)
    for p in pivots:
        by_index[p.confirmed_index].append(p)
    out = []
    prior_high = prior_low = None
    for i, b in enumerate(bars):
        j = bisect_right(available, b.available_ms)-1
        out.append(Feature(index=i, bar=b, ema10=e10[i], ema21=e21[i], dema50=d50[i],
                           atr10=a10[i], st_line=st[i], st_direction=direction[i],
                           context_ema10=c10[j] if j >= 0 else None,
                           context_ema21=c21[j] if j >= 0 else None,
                           context_st=cdir[j] if j >= 0 else "UNAVAILABLE",
                           context_st_line=cst[j] if j >= 0 else None,
                           context_close_ms=context_bars[j].close_ms if j >= 0 else None,
                           context_count=j+1, ready=i >= 249 and j >= 99 and (a10[i] or 0) > 0,
                           pivots=by_index.get(i, []), prior_high=prior_high, prior_low=prior_low))
        for pivot in by_index.get(i, []):
            if pivot.side == "HIGH":
                prior_high = pivot
            else:
                prior_low = pivot
    return out


def continuous(previous: Bar, current: Bar, signal_minutes: int, exchange: str = "NSE",
               *, across_sessions: bool = False) -> bool:
    """Whether ``current`` directly follows ``previous`` with no missing bar.

    Intraday bars are continuous within a session only. A mode that carries
    positions overnight (``across_sessions``) also treats the first bar of the
    next trading session after the previous session's last bar as continuous;
    otherwise every session boundary would reset its setups.
    """
    if signal_minutes < 1440:
        if current.open_ms == previous.close_ms:
            return True
        if not across_sessions:
            return False
        from .calendar import next_regular_session, regular_close
        old_dt = datetime.fromtimestamp(previous.close_ms/1000, IST)
        new_dt = datetime.fromtimestamp(current.open_ms/1000, IST)
        try:
            closed_at_bell = old_dt.time() >= regular_close(old_dt.date(), exchange)
            next_day = next_regular_session(old_dt.date(), exchange)
        except Exception:  # noqa: BLE001 - unknown calendar is a gap
            return False
        return closed_at_bell and new_dt.date() == next_day and new_dt.strftime("%H:%M") == "09:15"
    from .calendar import next_regular_session as next_trading_session
    old = datetime.fromtimestamp(previous.open_ms/1000, IST).date()
    new = datetime.fromtimestamp(current.open_ms/1000, IST).date()
    return new == next_trading_session(old, exchange)
