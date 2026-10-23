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
from pathlib import Path
import re
import sqlite3

_STALL = re.compile(r'^(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d).*Event loop stalled ([\d.]+)s; locations=(.*)')


def summarize(lines, day):
    date.fromisoformat(day)
    market, closed = [], []
    recovery_errors = sim_timeouts = 0
    for line in lines:
        if not line.startswith(day):
            continue
        if 'gamma_unwind cycle failed' in line and 'historical' in line:
            recovery_errors += 1
        if 'simulation gamma pass' in line and 'timed out' in line:
            sim_timeouts += 1
        match = _STALL.match(line)
        if match:
            when = time.fromisoformat(match[2])
            record = (float(match[3]), ' > '.join(match[4].split(' > ')[-3:]))
            (market if time(9, 15) <= when <= time(15, 30) else closed).append(record)
    def stats(records):
        return {'count': len(records), 'max_seconds': max((s for s, _ in records), default=None),
                'locations': dict(Counter(frame for _, frame in records).most_common())}
    observed = len(market) < 3 and all(s <= 5 for s, _ in market)
    return {'day': day, 'timezone': 'Asia/Kolkata', 'market_stalls': stats(market),
            'after_hours_stalls': stats(closed), 'gamma_historical_failures': recovery_errors,
            'simulation_gamma_timeouts': sim_timeouts, 'observed_threshold_met': observed,
            'coverage': 'unverified', 'full_session_accepted': False,
            'remaining_proof': ['Verify the log covers the full exchange trading session and the deployed PID/release.',
                               'Observe Gamma watched > 0 after a midday restart on two consecutive trading days.',
                               'Observe Snapback restored real signal rows read-only, then a fresh scan and AUTO-toggle retention.']}


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log', required=True, type=Path)
    parser.add_argument('--day', required=True)
    parser.add_argument('--db', type=Path)
    args = parser.parse_args()
    with args.log.open(errors='replace') as stream:
        report = summarize(stream, args.day)
    if args.db:
        report['stored_observations'] = stored_observations(args.db)
    report['generated_at'] = datetime.now().astimezone().isoformat()
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
