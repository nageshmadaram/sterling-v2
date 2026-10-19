"""Versioned execution policies, frozen per position rather than read from Settings."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.core.horizon import canonical_mode, timeline_for

IST = ZoneInfo('Asia/Kolkata')
POLICY_VERSION = 'mode_execution_v1'
TIMEFRAMES = {
    'ultra_scalping': (1,), 'scalping': (1, 3, 5),
    'intraday': (5, 15, 30), 'overnight': (30, 60), 'swing': (60,),
}


def timeframe_minutes(mode: str, requested: int | None = None) -> int:
    key = canonical_mode(mode).value
    default = int(timeline_for(key).signal_timeframe.removesuffix('m'))
    value = default if requested is None else int(requested)
    if value not in TIMEFRAMES[key]:
        raise ValueError(f'{key} timeframe must be one of {TIMEFRAMES[key]} minutes')
    return value


def freeze_policy(strategy: str, mode: str, now: datetime, *, exchange: str = 'NFO') -> dict:
    """Refuse unknown calendars; persist the deadline once for a new position."""
    from app.core.trade_horizon import build_horizon_plan
    mode = canonical_mode(mode).value
    horizon = build_horizon_plan(strategy_id=strategy, mode=mode, entry_at=now, exchange=exchange)
    timeline = timeline_for(mode)
    # Overnight uses its three-session expected ceiling, below the five-session
    # emergency ceiling. It is a distinct, bounded holding policy, not swing.
    deadline = horizon.hard_exit_at
    if deadline is None:
        session = (horizon.expected_window_end.date() if mode == 'overnight'
                   else horizon.hard_exit_session)
        deadline = datetime.combine(session, time(15, 20), tzinfo=IST)
    return {
        'version': POLICY_VERSION, 'mode': mode, 'horizon_plan_id': horizon.plan_id,
        'entry_ms': int(now.timestamp() * 1000), 'hard_exit_ms': int(deadline.timestamp() * 1000),
        'allow_overnight': timeline.allow_overnight,
        'no_weekend_carry': mode == 'overnight',
        'broker_stop_required': timeline.allow_overnight,
        'review_interval_seconds': timeline.review_interval_seconds,
        'max_concurrent_positions': timeline.max_concurrent_positions,
        'max_new_entries_per_session': timeline.max_new_entries_per_session,
        'expiry_buffer_calendar_days': 2 if mode == 'overnight' else 1,
    }


def carry_entry_refusal(policy: dict, *, expiry: str, now: datetime, stop_mode: str = 'both') -> str:
    if policy.get('mode') != 'overnight':
        return ''
    if stop_mode not in ('broker', 'both'):
        return 'overnight_broker_stop_required'
    try:
        expiration = date.fromisoformat(str(expiry)[:10])
        deadline = datetime.fromtimestamp(policy['hard_exit_ms'] / 1000, IST).date()
    except (ValueError, KeyError, TypeError):
        return 'overnight_expiry_unknown'
    if expiration <= deadline + timedelta(days=int(policy['expiry_buffer_calendar_days'])):
        return 'overnight_expiry_buffer'
    if policy.get('no_weekend_carry'):
        from app.services.navigator.calendar import next_trading_day
        try:
            today = now.astimezone(IST).date()
            if (next_trading_day(today) - today).days > 1:
                return 'overnight_closed_session_gap'
        except Exception:
            return 'overnight_calendar_unknown'
    return ''


def policy_exit_reason(policy: dict, now: datetime) -> str:
    """Management fails toward closing, even if a later calendar becomes unknown."""
    if not policy:
        return ''  # historical positions retain their original exit management
    try:
        if int(now.timestamp() * 1000) >= int(policy['hard_exit_ms']):
            return 'mode_holding_deadline'
    except (ValueError, KeyError, TypeError):
        return 'mode_policy_unreadable'
    if policy.get('no_weekend_carry'):
        local = now.astimezone(IST)
        if local.time() >= time(15, 20):
            from app.services.navigator.calendar import next_trading_day
            try:
                if (next_trading_day(local.date()) - local.date()).days > 1:
                    return 'mode_closed_session_gap'
            except Exception:
                return 'mode_calendar_unknown'
    return ''


def trading_minutes_held(opened_ms: int, now_ms: int) -> int:
    """Closed nights/holidays do not consume candle budgets of carried trades."""
    from app.services.navigator.calendar import is_trading_day
    start = datetime.fromtimestamp(opened_ms / 1000, IST)
    end = datetime.fromtimestamp(now_ms / 1000, IST)
    day = start.date()
    seconds = 0.0
    while day <= end.date():
        try:
            trading = is_trading_day(day)
        except Exception:
            trading = day.weekday() < 5  # earlier management exit if a calendar disappears
        if trading:
            left = max(start, datetime.combine(day, time(9, 15), IST))
            right = min(end, datetime.combine(day, time(15, 30), IST))
            seconds += max(0.0, (right - left).total_seconds())
        day += timedelta(days=1)
    return int(seconds // 60)
