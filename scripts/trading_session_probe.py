#!/usr/bin/env python3
"""Sample local backend availability for QA; never contact a broker or alter controls."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def sample():
    started = time.monotonic()
    record = {'at': datetime.now(timezone.utc).isoformat(), 'backend_ok': False,
              'pid': None, 'release': None, 'broker_mutations': 0}
    try:
        # Read only the release label from the listener's environment. Never
        # persist account/session credentials or the complete environment.
        listeners = subprocess.check_output(['ss', '-ltnp', '( sport = :8000 )'], text=True, timeout=3)
        pid = int(re.search(r'pid=(\d+)', listeners).group(1))
        argv = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        if b'main:app' not in argv or not any(b'uvicorn' in a for a in argv):
            raise ValueError('unexpected_listener')
        prefix = b'STERLING_RUNTIME_SHA='
        release = next((x[len(prefix):].decode() for x in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0') if x.startswith(prefix)), '')
        head = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True, timeout=3).strip()
        dirty = subprocess.check_output(['git', '-C', str(ROOT), 'status', '--porcelain', '--', 'backend/app', 'backend/main.py'], text=True, timeout=3).strip()
        with urlopen('http://127.0.0.1:8000/api/v1/health/live', timeout=5) as response:
            body = json.load(response)
        record.update(pid=pid, release=release or None,
                      backend_ok=body.get('alive') is True and release == head and not dirty,
                      checkout_release=head, source_clean=not bool(dirty))
    except Exception as exc:
        record['error_type'] = type(exc).__name__
    record['latency_seconds'] = round(time.monotonic()-started, 3)
    return record


def append(output, record):
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(output, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, 'a') as stream:
        stream.write(json.dumps(record, sort_keys=True) + '\n')
        stream.flush()
        # This standalone QA process can sync evidence without blocking trading.
        os.fsync(stream.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    while True:
        before = time.monotonic()
        append(args.output, sample())
        if args.once:
            return
        time.sleep(max(1, 30-(time.monotonic()-before)))


if __name__ == '__main__':
    main()
