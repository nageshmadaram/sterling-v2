"""Explicit opt-in historical retrieval and local cache, observation-only.

Does not replace kitelake, alter current snapshot cache or execute blocking lake
access on the FastAPI event loop. Sync file access must run off-loop if wired.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from app.schemas.market_reliability import (
    Candle, HistoryQuery, HistoryWindow, InstrumentKey, Quote, as_utc,
)

_IST = ZoneInfo("Asia/Kolkata")
_MAX_BARS = 10000


def step_for(timeframe: str) -> timedelta:
    unit = timeframe[-1]
    amount = int(timeframe[:-1])
    if unit == "m":
        step = timedelta(minutes=amount)
    elif unit == "h":
        step = timedelta(hours=amount)
    elif unit == "d":
        step = timedelta(days=amount)
    else:
        raise ValueError("unsupported timeframe")
    if step.total_seconds() < 60 or step.total_seconds() > 86400:
        raise ValueError("unsupported historical resolution")
    return step


def _moment(value) -> datetime:
    if isinstance(value, datetime):
        ts = value
    elif isinstance(value, (int, float)):
        ts = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    elif isinstance(value, str):
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("unrecognized candle timestamp")
    # TrueData's legacy bar payloads may be exchange-local; explicit IST
    # interpretation is restricted to the TrueData adapter.
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_IST)
    return as_utc(ts)


def normalize_bars(rows: list[dict], query: HistoryQuery, *, source: str) -> tuple[Candle, ...]:
    if not isinstance(rows, list) or len(rows) > _MAX_BARS:
        raise ValueError("unbounded/malformed historical rows")
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("historical bar is not a mapping")
        stamp = row.get("timestamp", row.get("timestamp_ms"))
        if stamp is None:
            raise ValueError("historical bar timestamp missing")
        candle = Candle(
            instrument=query.instrument, timeframe=query.timeframe,
            timestamp_utc=_moment(stamp), open=row["open"], high=row["high"],
            low=row["low"], close=row["close"], volume=row.get("volume", 0),
            source=source,
        )
        if query.start_utc <= candle.timestamp_utc < query.end_utc:
            result.append(candle)
    result.sort(key=lambda c: c.timestamp_utc)
    if any(a.timestamp_utc == b.timestamp_utc for a, b in zip(result, result[1:])):
        raise ValueError("duplicate candle timestamp")
    return tuple(result)


def history_window(query: HistoryQuery, candles: tuple[Candle, ...], *,
                   source: str | None) -> HistoryWindow:
    step = step_for(query.timeframe)
    slots = int((query.end_utc - query.start_utc).total_seconds() // step.total_seconds())
    if slots < 1 or slots > _MAX_BARS:
        raise ValueError("unbounded historical slots")
    found = set()
    for candle in candles:
        if candle.instrument != query.instrument or candle.timeframe != query.timeframe:
            raise ValueError("candle contract mismatch")
        delta = (candle.timestamp_utc - query.start_utc).total_seconds()
        if delta < 0 or delta >= (query.end_utc - query.start_utc).total_seconds():
            raise ValueError("candle outside query")
        if delta % step.total_seconds():
            raise ValueError("misaligned candle")
        if candle.timestamp_utc in found:
            raise ValueError("duplicate candle timestamp")
        found.add(candle.timestamp_utc)
    gaps = tuple(query.start_utc + i * step for i in range(slots)
                 if query.start_utc + i * step not in found)
    coverage = len(found) * 100 // slots
    return HistoryWindow(
        query=query, candles=tuple(sorted(candles, key=lambda c: c.timestamp_utc)),
        gap_starts_utc=gaps, coverage_pct=coverage,
        quality_status="missing" if not candles else
                       ("good" if not gaps else "degraded"),
        source=source,
    )


def aggregate_candles(candles: tuple[Candle, ...], *, target_timeframe: str
                      ) -> tuple[Candle, ...]:
    """UTC-aligned OHLCV rollup; excludes incomplete child groups."""
    if not candles:
        return ()
    parent_step = step_for(target_timeframe)
    child_step = step_for(candles[0].timeframe)
    if parent_step <= child_step or parent_step.total_seconds() % child_step.total_seconds():
        raise ValueError("target must be an integer multiple of source timeframe")
    if any(c.instrument != candles[0].instrument or c.timeframe != candles[0].timeframe for c in candles):
        raise ValueError("mixed instruments/timeframes")
    ratio = int(parent_step.total_seconds() / child_step.total_seconds())
    groups: dict[datetime, list[Candle]] = {}
    for candle in candles:
        epoch = int(candle.timestamp_utc.timestamp())
        start = datetime.fromtimestamp((epoch // int(parent_step.total_seconds())) *
                                       int(parent_step.total_seconds()), tz=timezone.utc)
        groups.setdefault(start, []).append(candle)
    out = []
    for start, group in sorted(groups.items()):
        ordered = sorted(group, key=lambda x: x.timestamp_utc)
        expected = [start + i * child_step for i in range(ratio)]
        if len(ordered) != ratio or [c.timestamp_utc for c in ordered] != expected:
            continue  # never synthesize an incomplete OHLCV aggregation
        out.append(Candle(
            instrument=ordered[0].instrument, timeframe=target_timeframe,
            timestamp_utc=start, open=ordered[0].open, high=max(c.high for c in ordered),
            low=min(c.low for c in ordered), close=ordered[-1].close,
            volume=sum((c.volume for c in ordered), Decimal(0)),
            source=ordered[0].source,
        ))
    return tuple(out)


class LocalHistoricalLake:
    """Separate opt-in local JSON store; no writes to existing kitelake paths.

    Directory should be owned by the Sterling service account and not shared
    with untrusted users. Atomic replacement, fixed hashed filenames.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def _file(self, query: HistoryQuery) -> Path:
        digest = hashlib.sha256(
            f"{query.instrument.qualified}|{query.timeframe}|"
            f"{query.start_utc.isoformat()}|{query.end_utc.isoformat()}".encode()
        ).hexdigest()
        return self.root / f"{digest}.json"

    def save(self, window: HistoryWindow) -> None:
        if window.quality_status == "missing":
            raise ValueError("do not persist an empty window as market truth")
        path = self._file(window.query)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.is_symlink():
            raise ValueError("refuse symlinked lake file")
        payload = window.model_dump(mode="json")
        payload["source"] = "lake"
        for candle in payload["candles"]:
            candle["source"] = "lake"
        tmp = None
        try:
            with tempfile.NamedTemporaryFile("w", dir=self.root, delete=False,
                                             encoding="utf-8") as f:
                tmp = Path(f.name)
                os.chmod(tmp, 0o600)
                json.dump(payload, f, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)

    def load(self, query: HistoryQuery) -> HistoryWindow | None:
        path = self._file(query)
        if path.is_symlink() or not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        record = HistoryWindow.model_validate(data)
        if record.query != query or record.source != "lake":
            raise ValueError("local cache record mismatch")
        return history_window(query, record.candles, source="lake")


class LakeQuoteSource:
    """Historical close as degraded observation; never a live quote."""
    def __init__(self, store: LocalHistoricalLake, query: HistoryQuery):
        self._store, self._query = store, query

    async def read_quote(self, key: InstrumentKey) -> Quote | None:
        if key != self._query.instrument:
            return None
        from app.core.async_work import run_blocking
        window = await run_blocking(self._store.load, self._query)
        if not window or not window.candles:
            return None
        bar = window.candles[-1]
        return Quote(instrument=key, last_price=bar.close,
                     observed_at_utc=bar.timestamp_utc,
                     fetched_at_utc=datetime.now(timezone.utc), source="lake")


class TrueDataAlternateQuoteSource:
    """Read-only TrueData tick adapter with explicit, reviewed symbol mapping.

    Unknown symbols never generate an upstream query. Without a provider
    timestamp the quote is present but degraded by the quality layer.
    """

    def __init__(self, client, symbol_map: dict[str, str]):
        self._client = client
        self._symbol_map = dict(symbol_map)

    async def read_quote(self, key: InstrumentKey) -> Quote | None:
        mapped = self._symbol_map.get(key.qualified)
        if not mapped or not isinstance(mapped, str):
            return None
        rows = await self._client.get_last_ticks(mapped, 1)
        if not isinstance(rows, list) or not rows:
            return None
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise ValueError("unexpected alternate tick response")
        tick = rows[0]
        stamp = tick.get("timestamp")
        observed = _moment(stamp) if stamp else None
        return Quote(instrument=key, last_price=tick.get("ltp"),
                     observed_at_utc=observed,
                     fetched_at_utc=datetime.now(timezone.utc),
                     source="alternate")


class TrueDataHistoricalSource:
    """Adapts existing TrueDataHistoricalClient.get_bars; no new credentials/transport."""

    def __init__(self, client):
        self._client = client

    async def history(self, query: HistoryQuery) -> HistoryWindow:
        minutes = int(step_for(query.timeframe).total_seconds() // 60)
        interval = f"{minutes}min"
        if interval not in self._client.DOCUMENTED_BAR_INTERVALS:
            raise ValueError("TrueData historical interval not documented; explicit aggregation required")
        start = query.start_utc.astimezone(_IST).strftime("%Y-%m-%d %H:%M:%S")
        end = query.end_utc.astimezone(_IST).strftime("%Y-%m-%d %H:%M:%S")
        rows = await self._client.get_bars(query.instrument.symbol, start, end,
                                           interval=interval)
        normalized = normalize_bars(rows, query, source="alternate")
        return history_window(query, normalized, source="alternate")
