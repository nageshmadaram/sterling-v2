"""Current signal eligibility, independent of paper/live execution transport."""
from datetime import datetime
from app.engines.ema_trend.config import PROFILES


def current(row: dict, settings: dict, now: datetime) -> dict:
    from app.core.session_calendar import is_trading_session, next_trading_session
    if (row.get('mode') not in PROFILES or not isinstance(row.get('symbol'), str)
            or ':' not in row['symbol'] or not isinstance(row.get('close_ms'), (int, float))):
        return {'signal_source': 'BROKER_COMPLETED_CANDLES', 'entry_expires_ms': None,
                'entry_window_open': False, 'runtime_blockers': ['SIGNAL_EVIDENCE_INCOMPLETE'],
                'observed_ms': int(now.timestamp() * 1000)}
    profile = PROFILES[row["mode"]]
    exchange = row["symbol"].split(":", 1)[0]
    blockers = ["INDEX_SPOT_WATCH_ONLY_NO_ORDERS"] if row.get("watch_only") or row.get("signal_source")=="INDEX_SPOT_WATCH_ONLY" else []
    expiry = None
    if row["close_ms"] > int(now.timestamp()*1000):
        blockers.append("SIGNAL_NOT_CLOSED")
    try:
        if profile.mode == "swing":
            signal_day = datetime.fromtimestamp(row["close_ms"]/1000, now.tzinfo).date()
            entry_day = next_trading_session(signal_day, exchange)
            expiry = int(datetime.combine(entry_day, datetime.strptime(profile.entry_end, "%H:%M").time(), now.tzinfo).timestamp()*1000)
        else:
            expiry = row["close_ms"] + profile.signal_minutes*60000
        if int(now.timestamp()*1000) > expiry:
            blockers.append("ENTRY_EXPIRED")
        if not is_trading_session(now.date(), exchange) or not profile.entry_start <= now.strftime("%H:%M") <= profile.entry_end:
            blockers.append("OUTSIDE_PROFILE_ENTRY_WINDOW")
    except Exception:
        blockers.append("ENTRY_CALENDAR_UNVERIFIED")
    if row.get("config_revision") != settings["revision"]:
        blockers.append("SIGNAL_CONFIG_SUPERSEDED")
    if not row.get("activation_eligible"):
        blockers.append("PRE_ACTIVATION_SETUP")
    if row.get("status") != "armed":
        blockers.append("SETUP_NOT_ARMED")
    if not settings["config"]["enabled"] or settings.get("paused"):
        blockers.append("NEW_EXPOSURE_DISABLED_OR_PAUSED")
    return {"signal_source": ("INDEX_SPOT_WATCH_ONLY" if row.get("watch_only") else "BROKER_COMPLETED_CANDLES"), "entry_expires_ms": expiry,
            "entry_window_open": not blockers, "runtime_blockers": blockers,
            "observed_ms": int(now.timestamp()*1000)}
