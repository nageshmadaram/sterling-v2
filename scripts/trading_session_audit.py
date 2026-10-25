#!/usr/bin/env python3
"""Read-only session observations, never a substitute for live acceptance.

Log timestamps are the deployment's IST timestamps. This tool issues no HTTP
requests and never changes orders, controls, strategy settings or databases.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, time
import json
import hashlib
import math
from pathlib import Path
import re
import sqlite3

_TIMESTAMP = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)')
_STALL = re.compile(r'^(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d).*Event loop stalled ([\d.]+)s; locations=(.*)')


def summarize(lines, day):
    date.fromisoformat(day)
    market, closed = [], []
    recovery_errors = sim_timeouts = unparsed_stalls = 0
    first = last = None
    dated_lines = 0
    for line in lines:
        timestamp = _TIMESTAMP.match(line)
        try:
            at = datetime.fromisoformat(timestamp[1]) if timestamp else None
        except ValueError:
            at = None
        if at is not None:
            first = min(first, at) if first else at
            last = max(last, at) if last else at
            dated_lines += 1
        match = _STALL.match(line)
        try:
            duration = float(match[3]) if match else None
        except ValueError:
            duration = None
        if 'Event loop stalled' in line and (at is None or duration is None or not math.isfinite(duration)):
            unparsed_stalls += 1
            continue
        if at is None or at.date().isoformat() != day:
            continue
        if 'gamma_unwind cycle failed' in line and 'historical' in line:
            recovery_errors += 1
        if 'simulation gamma pass' in line and 'timed out' in line:
            sim_timeouts += 1
        if match:
            when = time.fromisoformat(match[2])
            record = (duration, ' > '.join(match[4].split(' > ')[-3:]))
            (market if time(9, 15) <= when <= time(15, 30) else closed).append(record)
    def stats(records):
        return {'count': len(records), 'max_seconds': max((s for s, _ in records), default=None),
                'locations': dict(Counter(frame for _, frame in records).most_common())}
    observed = unparsed_stalls == 0 and len(market) < 3 and all(s <= 5 for s, _ in market)
    opening = datetime.combine(date.fromisoformat(day), time(9, 15))
    closing = datetime.combine(date.fromisoformat(day), time(15, 30))
    log_coverage = {'dated_lines': dated_lines, 'first': first.isoformat() if first else None,
                    'last': last.isoformat() if last else None,
                    'covers_market_session': bool(first and last and first <= opening and last >= closing)}
    return {'day': day, 'log_coverage': log_coverage, 'unparsed_stall_lines': unparsed_stalls, 'timezone': 'Asia/Kolkata', 'market_stalls': stats(market),
            'after_hours_stalls': stats(closed), 'gamma_historical_failures': recovery_errors,
            'simulation_gamma_timeouts': sim_timeouts, 'observed_threshold_met': observed,
            'coverage': 'unverified', 'full_session_accepted': False,
            'remaining_proof': ['Verify the log covers the full exchange trading session and the deployed PID/release.',
                               'Observe Gamma watched > 0 after a midday restart on two consecutive trading days.',
                               'Observe Snapback restored real signal rows read-only, then a fresh scan and AUTO-toggle retention.']}



def session_coverage(samples, day):
    """Verify a complete regular NFO session, never infer coverage from silence."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
    from app.core.session_calendar import is_trading_session, CALENDAR_VERSION
    try:
        calendar_ok = is_trading_session(date.fromisoformat(day), 'NFO')
    except LookupError:
        calendar_ok = False
    ist = ZoneInfo('Asia/Kolkata')
    opening = datetime.combine(date.fromisoformat(day), time(9, 15), ist)
    closing = datetime.combine(date.fromisoformat(day), time(15, 30), ist)
    parsed, malformed = [], 0
    for sample in samples:
        try:
            at = datetime.fromisoformat(sample['at'])
            if at.tzinfo is None:
                raise ValueError('timestamp timezone required')
            at = at.astimezone(ist)
            if opening-timedelta(seconds=90) <= at <= closing+timedelta(seconds=90):
                parsed.append((at, sample))
        except (KeyError, TypeError, ValueError):
            malformed += 1
    parsed.sort(key=lambda x: x[0])
    before = [x for x in parsed if x[0] <= opening]
    after = [x for x in parsed if x[0] >= closing]
    bounded = bool(before and after)
    rows = [x for x in parsed if before[-1][0] <= x[0] <= after[0][0]] if bounded else parsed
    gaps = [(b[0]-a[0]).total_seconds() for a, b in zip(rows, rows[1:])]
    releases = sorted({str(x.get('release')) for _, x in rows})
    healthy = bool(rows) and all(x.get('backend_ok') is True and isinstance(x.get('pid'), int)
                    and x['pid'] > 1 and re.fullmatch(r'[a-f0-9]{40,64}', str(x.get('release','')))
                    for _, x in rows)
    verified = bool(calendar_ok and bounded and not malformed and healthy
                    and len(releases) == 1 and gaps and max(gaps) <= 90)
    return {'verified': verified, 'calendar_verified': calendar_ok, 'calendar_version': CALENDAR_VERSION,
            'normal_session': '09:15–15:30 IST; special sessions require separate acceptance',
            'samples': len(rows), 'malformed_samples': malformed,
            'first': rows[0][0].isoformat() if rows else None,
            'last': rows[-1][0].isoformat() if rows else None,
            'max_gap_seconds': max(gaps, default=None),
            'releases': releases, 'pids': sorted({x['pid'] for _, x in rows if isinstance(x.get('pid'), int)})}

def stored_observations(path):
    path = Path(path).resolve(strict=True)
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as conn:
        conn.execute('PRAGMA query_only=ON')
        raw = conn.execute('SELECT value FROM system_config WHERE key=?', ('snapback.last_rows.v1:default',)).fetchone()
        value = json.loads(raw[0]) if raw else {}
        return {'snapback_saved_rows': len(value.get('rows') or []),
                'snapback_saved_ms': value.get('saved_ms'),
                'snapback_saved_bytes': len(raw[0].encode()) if raw else 0,
                'daily_coverage': [dict(zip(('resolution','symbols','rows','first_epoch','last_epoch'), row)) for row in
                    conn.execute("SELECT resolution,count(DISTINCT symbol),count(*),min(time),max(time) FROM ohlcv GROUP BY resolution")]}


def load_probes(stream):
    """Keep damaged records visible to coverage; never repair or echo raw input."""
    samples, invalid = [], []
    json_records = 0
    for number, raw in enumerate(stream, 1):
        if not raw.strip():
            continue
        encoded = raw if isinstance(raw, bytes) else raw.encode('utf-8')
        try:
            value = json.loads(encoded.decode('utf-8'))
        except (ValueError, UnicodeError):
            invalid.append({'line': number, 'sha256': hashlib.sha256(encoded).hexdigest()})
            # A missing timestamp is malformed evidence, so coverage stays false.
            samples.append({'_invalid_json_line': number})
        else:
            samples.append(value)
            json_records += 1
    return samples, {'json_records': json_records, 'invalid_json_lines': invalid}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log', required=True, type=Path)
    parser.add_argument('--day', required=True)
    parser.add_argument('--db', type=Path)
    parser.add_argument('--probes', type=Path, help='JSONL from the read-only session probe')
    args = parser.parse_args()
    with args.log.open(errors='replace') as stream:
        report = summarize(stream, args.day)
    if args.probes:
        with args.probes.open('rb') as stream:
            samples, report['probe_input'] = load_probes(stream)
        coverage = session_coverage(samples, args.day)
        report['session_coverage'] = coverage
        complete = coverage['verified'] and report['log_coverage']['covers_market_session']
        report['coverage'] = 'verified' if complete else 'unverified'
        report['market_load_accepted'] = bool(complete and report['observed_threshold_met'])
    if args.db:
        report['stored_observations'] = stored_observations(args.db)
    report['generated_at'] = datetime.now().astimezone().isoformat()
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
