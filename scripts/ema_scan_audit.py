#!/usr/bin/env python3
"""Compare persisted EMA scan failures with later successful symbol/mode results.

SQLite is opened read-only without importing the app, initializing schemas,
triggering scans, or making provider/order requests. Raw provider errors and
account identifiers are deliberately omitted from the report.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


def compare(scans, *, historical_job=None, reason='HISTORICAL_BAR_RESTATED_RECONCILIATION_REQUIRED'):
    scans = sorted(scans, key=lambda x: x['updated_ms'])
    historical = [s for s in scans if any(e.get('reason') == reason for e in s.get('errors', []))]
    if historical_job is not None:
        historical = [s for s in historical if s.get('id') == historical_job]
    if not historical:
        return {'status': 'UNAVAILABLE', 'reason': 'matching_historical_failure_not_found', 'resolved_pairs': [], 'unresolved_pairs': []}
    failed = historical[-1]
    pairs = {(e['symbol'], e['mode']) for e in failed['errors']
             if e.get('reason') == reason and e.get('symbol') and e.get('mode')}
    # A running, cancelled, failed or older job cannot certify this repair.
    completed = [s for s in scans if s['updated_ms'] > failed['updated_ms'] and s.get('status') == 'COMPLETED']
    latest = completed[-1] if completed else None
    successes = {(x.get('symbol'), x.get('mode')) for x in (latest or {}).get('results', [])}
    errors = {(x.get('symbol'), x.get('mode')) for x in (latest or {}).get('errors', [])}
    covered = pairs & (successes - errors)
    def rows(values):
        return [{'symbol': s, 'mode': m} for s, m in sorted(values)]
    return {'status': 'RESOLVED' if pairs and covered == pairs else 'PENDING',
            'failure_reason': reason, 'historical_job': failed.get('id'),
            'historical_updated_ms': failed['updated_ms'], 'historical_pair_count': len(pairs),
            'historical_error_count': len(failed.get('errors', [])),
            'completed_job': (latest or {}).get('id'),
            'completed_updated_ms': (latest or {}).get('updated_ms'),
            'completed_result_count': len((latest or {}).get('results', [])),
            'completed_error_count': len((latest or {}).get('errors', [])),
            'resolved_pairs': rows(covered), 'unresolved_pairs': rows(pairs - covered),
            'scope': 'Persisted broker-data scan coverage only; no fills, edge or live promotion is inferred.'}


def audit(path, *, historical_job=None):
    uri = Path(path).resolve(strict=True).as_uri() + '?mode=ro'
    with sqlite3.connect(uri, uri=True) as conn:
        conn.execute('PRAGMA query_only=ON')
        scans = []
        for updated, raw in conn.execute("SELECT updated_ms,data_json FROM ema_trend_documents WHERE kind='scan' ORDER BY updated_ms"):
            scans.append({**json.loads(raw), 'updated_ms': updated})
    return {**compare(scans, historical_job=historical_job), 'generated_at': datetime.now(timezone.utc).isoformat(),
            'database_read_only': True, 'provider_requests': 0, 'broker_mutations': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, type=Path)
    parser.add_argument('--historical-job', help='Exact recorded failed job to recheck')
    args = parser.parse_args()
    print(json.dumps(audit(args.db, historical_job=args.historical_job), indent=2))
