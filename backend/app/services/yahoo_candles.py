"""Yahoo Finance OHLCV source for index and stock underlyings.

Used when the operator selects "yahoo" as the market data source, and as a
fresher fallback than the local store when the Kite historical API is down.
Yahoo has no option-contract history, so contract symbols map to ``None``.
Plain HTTPS against the public chart endpoint; no extra dependency.
"""
from __future__ import annotations

import asyncio
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

# Throttle: a universe scan fans out hundreds of symbols at once. Cap in-flight
# requests and space request starts so Yahoo does not answer 429.
_MAX_CONCURRENT = 4
_MIN_SPACING_S = 0.12
# Retry: transient failures only (429, 5xx, network). 4xx other than 429 is final.
_MAX_ATTEMPTS = 3
_BACKOFF_BASE_S = 0.8
_BACKOFF_MAX_S = 8.0
# A symbol Yahoo does not know stays unknown for a while; do not re-ask per scan.
_NEGATIVE_TTL_S = 3600.0
_unknown_until: Dict[str, float] = {}


class _Gate:
    """Per-event-loop concurrency cap + minimum spacing between request starts."""

    def __init__(self) -> None:
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.sem: Optional[asyncio.Semaphore] = None
        self.lock: Optional[asyncio.Lock] = None
        self.next_start = 0.0

    def bind(self) -> None:
        loop = asyncio.get_running_loop()
        if self.loop is not loop:
            self.loop, self.sem, self.lock = loop, asyncio.Semaphore(_MAX_CONCURRENT), asyncio.Lock()
            self.next_start = 0.0

    async def wait_turn(self) -> None:
        assert self.lock is not None
        async with self.lock:
            now = time.monotonic()
            delay = self.next_start - now
            self.next_start = max(now, self.next_start) + _MIN_SPACING_S
        if delay > 0:
            await asyncio.sleep(delay)


_gate = _Gate()


def _retry_delay(attempt: int, retry_after: Optional[str]) -> float:
    if retry_after:
        try:
            return min(float(retry_after), _BACKOFF_MAX_S)
        except ValueError:
            pass
    return min(_BACKOFF_BASE_S * (2 ** attempt), _BACKOFF_MAX_S)


async def _get_json(url: str, params: dict) -> Optional[dict]:
    """GET with throttle and retry. None means final failure (already logged)."""
    _gate.bind()
    assert _gate.sem is not None
    async with _gate.sem:
        for attempt in range(_MAX_ATTEMPTS):
            await _gate.wait_turn()
            retry_after: Optional[str] = None
            try:
                async with httpx.AsyncClient(timeout=_TIMEOUT_S, headers=_HEADERS) as client:
                    resp = await client.get(url, params=params)
                if resp.status_code == 404:
                    _unknown_until[url] = time.monotonic() + _NEGATIVE_TTL_S
                    log.warning("Yahoo has no data for %s (404)", url.rsplit("/", 1)[-1])
                    return None
                if resp.status_code == 429 or resp.status_code >= 500:
                    retry_after = getattr(resp, "headers", {}).get("Retry-After")
                    raise httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}", request=resp.request, response=resp)
                resp.raise_for_status()
                return resp.json()
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                transient = status is None or status == 429 or status >= 500
                if not transient or attempt == _MAX_ATTEMPTS - 1:
                    log.warning("Yahoo fetch failed for %s after %d attempt(s): %s",
                                url.rsplit("/", 1)[-1], attempt + 1, exc or type(exc).__name__)
                    return None
                await asyncio.sleep(_retry_delay(attempt, retry_after))
            except Exception as exc:  # noqa: BLE001 - malformed body etc., not retryable
                log.warning("Yahoo fetch failed for %s: %s", url.rsplit("/", 1)[-1], exc)
                return None
    return None

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
    url = _BASE + quote(sym, safe="")
    if _unknown_until.get(url, 0.0) > time.monotonic():
        return []
    payload = await _get_json(url, params)
    if payload is None:
        return []
    try:
        result = payload["chart"]["result"][0]
        stamps = result.get("timestamp") or []
        quote_ = result["indicators"]["quote"][0]
    except (KeyError, IndexError, TypeError) as exc:
        log.warning("Yahoo response for %s (%s) had no usable chart: %r", name, sym, exc)
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
