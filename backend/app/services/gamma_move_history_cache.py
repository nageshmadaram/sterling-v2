"""Account-scoped, disposable Gamma OHLCV cache; never caches failures or orders."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.async_work import run_blocking

IST = timezone(timedelta(hours=5, minutes=30))
log = logging.getLogger(__name__)


def cache_root():
    return Path(os.environ.get('STERLING_GAMMA_CACHE_DIR') or Path(__file__).resolve().parents[3] / '.runtime/market-cache/gamma')


def _now():
    return datetime.now(IST)


def _time(value, *, end=False):
    text = str(value)
    parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    if len(text) == 10 and end:
        parsed = parsed.replace(hour=15, minute=30)
    return parsed.replace(tzinfo=IST) if parsed.tzinfo is None else parsed.astimezone(IST)


def _stamp(row):
    value = row[0] if isinstance(row, (list, tuple)) and row else None
    if value is None:
        raise ValueError('unsupported historical candle shape')
    return _time(value).timestamp()


def _read(path):
    try:
        item = json.loads(path.read_text())
        if item.get('version') != 2 or not isinstance(item.get('rows'), list):
            return None
        if not math.isfinite(float(item.get('through', 0))):
            return None
        if item.get('data_hash') != _data_hash(item):
            return None
        for row in item['rows']:
            _stamp(row)
            if len(row) < 6 or not all(math.isfinite(float(v)) for v in row[1:]):
                return None
        return item
    except (OSError, ValueError, TypeError, KeyError, OverflowError, AttributeError):
        pass
    return None


def _data_hash(item):
    payload = json.dumps([item["through"], item["rows"]], allow_nan=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _write(path, item, today):
    if item.get("version") == 2 and "rows" in item:
        item = {**item, "data_hash": _data_hash(item)}
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.next-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(item, stream, allow_nan=False, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        # Only disposable account-scoped cache directories, never scan history/journals.
        cutoff = (today - timedelta(days=2)).isoformat()
        for directory in path.parent.parent.iterdir():
            if directory.is_dir() and len(directory.name) == 10:
                try:
                    datetime.fromisoformat(directory.name)
                except ValueError:
                    continue
                if directory.name < cutoff:
                    shutil.rmtree(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


async def historical(client, token, interval, frm, to, oi, fetch):
    account = getattr(client, '_account_id', None)
    if not isinstance(account, str) or not account or interval not in {'day', '5minute'}:
        return await fetch(frm, to)
    try:
        end = _time(to, end=True)
        now = _now()
        if interval == 'day':
            boundary = now.replace(hour=15, minute=30, second=0, microsecond=0)
            if now < boundary:
                boundary -= timedelta(days=1)
        else:
            boundary = datetime.fromtimestamp(int(now.timestamp()) // 300 * 300, IST)
        needed = min(end, boundary).timestamp()
        scope = hashlib.sha256(account.encode()).hexdigest()[:24]
        digest = hashlib.sha256(json.dumps([int(token), interval, str(frm), end.date().isoformat(), bool(oi)]).encode()).hexdigest()
        root = cache_root()
        path = root / scope / end.date().isoformat() / (digest + '.json')
        cached = await run_blocking(_read, path)
        prefix = cached.get('rows') if cached else []
        if prefix and float(cached.get('through', 0)) >= needed:
            return [row for row in prefix if _stamp(row) <= end.timestamp()]
        start = frm
        if prefix:
            # Re-read the last observed bar, including any formerly forming candle.
            last = max(_stamp(row) for row in prefix)
            if last >= _time(frm).timestamp():
                start = datetime.fromtimestamp(last, IST).strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError, OSError):
        return await fetch(frm, to)
    # A failed incremental read must propagate. A stale prefix is never its substitute.
    fresh = await fetch(start, to)
    if not fresh:
        return fresh
    try:
        merged = {_stamp(row): row for row in [*prefix, *fresh]}
        rows = [merged[key] for key in sorted(merged)]
        await run_blocking(_write, path, {'version': 2, 'through': needed, 'rows': rows}, now.date())
        return [row for row in rows if _stamp(row) <= end.timestamp()]
    except (OSError, ValueError, TypeError):
        log.warning('Gamma historical cache unavailable; using fresh broker response')
        # Prefix was not validated or durable. Do not represent it as fresh evidence.
        return fresh
