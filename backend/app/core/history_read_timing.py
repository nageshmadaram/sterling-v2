"""Caller-owned Gamma history diagnostics; no identifiers or persistent registry.

Durations are cumulative per read, not additive wall time: two option reads
may overlap. HTTP time includes transport/pool/server/body wait, not just wire.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic

_current = ContextVar('history_read_timing', default=None)


@contextmanager
def history_scope(metrics, role):
    token = _current.set(metrics.setdefault(role, {}))
    try:
        yield
    finally:
        _current.reset(token)


@contextmanager
def phase(name, *, enabled=True):
    metrics = _current.get()
    if metrics is None or not enabled:
        yield
        return
    started = monotonic()
    try:
        yield
    finally:
        item = metrics.setdefault(name, {'count': 0, 'seconds': 0.0, 'max_seconds': 0.0})
        elapsed = monotonic() - started
        item['count'] += 1
        item['seconds'] += elapsed
        item['max_seconds'] = max(item['max_seconds'], elapsed)


def event(name, *, enabled=True):
    metrics = _current.get()
    if metrics is not None and enabled:
        metrics[name] = metrics.get(name, 0) + 1
