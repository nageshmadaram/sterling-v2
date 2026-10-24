"""Operational scope of the source strategy; legacy maths remain readable.

Reference: https://x.com/apoorvsinghasr/status/2110394084598960273
Daily EMA execution and weekly SuperTrend context. Retired intraday profiles
remain in the engine solely to interpret recorded campaigns and their exits.
"""
from app.engines.ema_trend.config import canonical_mode

ORIGINAL_MODE = 'swing'
ORIGINAL_ONLY = 'EMA_ORIGINAL_DAILY_WEEKLY_ONLY'


def require_original(mode: str) -> str:
    selected = canonical_mode(mode)
    if selected != ORIGINAL_MODE:
        raise ValueError(ORIGINAL_ONLY)
    return selected


def scan_modes(config) -> list[str]:
    return [ORIGINAL_MODE] if ORIGINAL_MODE in config.modes else []
