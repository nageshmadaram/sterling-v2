"""Yahoo Finance OHLCV source for index and stock underlyings.

Used when the operator selects "yahoo" as the market data source, and as a
fresher fallback than the local store when the Kite historical API is down.
Yahoo has no option-contract history, so contract symbols map to ``None``.
Plain HTTPS against the public chart endpoint; no extra dependency.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Dict, List, Optional
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)

_BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Sterling)"}
_TIMEOUT_S = 12.0

_INDEX_SYMBOLS: Dict[str, str] = {
    "NIFTY 50": "^NSEI", "NIFTY": "^NSEI",
    "NIFTY BANK": "^NSEBANK", "BANKNIFTY": "^NSEBANK",
    "NIFTY FIN SERVICE": "NIFTY_FIN_SERVICE.NS", "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
    "SENSEX": "^BSESN", "BSE:SENSEX": "^BSESN",
}
# Plain cash-equity tradingsymbols only. Option/future contracts carry digits
# (``NIFTY26OCT22650PE``) and have no Yahoo history.
_STOCK_RE = re.compile(r"^[A-Z&\-]{1,20}$")

# scanner resolution -> (yahoo interval, max calendar days Yahoo serves)
_INTERVALS: Dict[str, tuple[str, int]] = {
    "1m": ("1m", 7), "5m": ("5m", 59), "15m": ("15m", 59), "30m": ("30m", 59),
    "1H": ("60m", 729), "4H": ("60m", 729), "D": ("1d", 3650), "1D": ("1d", 3650),
}


_SESSION_CLOSE_MIN = 15 * 60 + 30


def _minute_of_day_ist(ts: int) -> int:
    return ((int(ts) + 19800) % 86400) // 60


def yahoo_symbol(name: str) -> Optional[str]:
    key = (name or "").strip().upper()
    if key in _INDEX_SYMBOLS:
        return _INDEX_SYMBOLS[key]
    if _STOCK_RE.match(key):
        return f"{key}.NS"
    return None


def supports_resolution(resolution: str) -> bool:
    return resolution in _INTERVALS


async def fetch_candles(name: str, resolution: str, days: int) -> List[dict]:
    """Return ``[{time (epoch s), open, high, low, close, volume}]`` ascending.

    Empty list means "nothing usable" (unmapped symbol, unsupported resolution,
    HTTP/parse failure); the caller decides what to fall back to.
    """
    sym = yahoo_symbol(name)
    spec = _INTERVALS.get(resolution)
    if not sym or not spec:
        return []
    interval, max_days = spec
    now = int(time.time())
    params = {
        "interval": interval,
        "period1": now - min(max(days, 2), max_days) * 86400,
        "period2": now + 60,
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S, headers=_HEADERS) as client:
            resp = await client.get(_BASE + quote(sym, safe=""), params=params)
        resp.raise_for_status()
        result = resp.json()["chart"]["result"][0]
        stamps = result.get("timestamp") or []
        quote_ = result["indicators"]["quote"][0]
    except Exception as exc:  # noqa: BLE001
        log.warning("Yahoo candle fetch failed for %s (%s): %s", name, sym, exc)
        return []

    rows: List[dict] = []
    for i, ts in enumerate(stamps):
        try:
            o, h, l, c = (quote_[k][i] for k in ("open", "high", "low", "close"))
            if None in (o, h, l, c):
                continue  # Yahoo emits null bars for halted/partial intervals
            if interval != "1d" and _minute_of_day_ist(ts) >= _SESSION_CLOSE_MIN:
                continue  # closing-auction print at 15:30, not a traded bar
            v = quote_.get("volume", [None] * len(stamps))[i]
            rows.append({
                "time": int(ts), "open": float(o), "high": float(h),
                "low": float(l), "close": float(c), "volume": float(v or 0.0),
            })
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    rows.sort(key=lambda r: r["time"])
    return rows
