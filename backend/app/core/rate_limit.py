"""
Simple sliding-window rate limiter for expensive endpoints.
Uses in-process deque — lightweight, no Redis needed.
Not for multi-worker deployments (use Redis-backed limiter there).
"""
import time
from collections import deque, defaultdict
from typing import Dict, Deque

from fastapi import Request, HTTPException


class SlidingWindowRateLimiter:
    """
    Tracks call timestamps per key in a sliding time window.
    Thread-safe for asyncio (single-process uvicorn).
    """

    def __init__(self, max_calls: int, window_seconds: float) -> None:
        self._max = max_calls
        self._window = window_seconds
        self._store: Dict[str, Deque[float]] = defaultdict(deque)

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        window_start = now - self._window
        q = self._store[key]
        # Evict old timestamps
        while q and q[0] < window_start:
            q.popleft()
        if len(q) >= self._max:
            return False
        q.append(now)
        return True

    def remaining(self, key: str) -> int:
        now = time.monotonic()
        window_start = now - self._window
        q = self._store[key]
        while q and q[0] < window_start:
            q.popleft()
        return max(0, self._max - len(q))


# Rate limiters for specific endpoint groups
_run_all_limiter = SlidingWindowRateLimiter(max_calls=6, window_seconds=60)      # 6/min
_run_once_limiter = SlidingWindowRateLimiter(max_calls=20, window_seconds=60)    # 20/min
_backtest_limiter = SlidingWindowRateLimiter(max_calls=10, window_seconds=60)    # 10/min
_login_limiter = SlidingWindowRateLimiter(max_calls=10, window_seconds=60)       # 10/min
_refresh_limiter = SlidingWindowRateLimiter(max_calls=30, window_seconds=60)     # 30/min


def _is_production() -> bool:
    from app.core import runtime_env
    return runtime_env.is_production()


def _client_key(request: Request) -> str:
    """Identify client by IP. Falls back to 'unknown'."""
    fwd = request.headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_run_all(request: Request) -> None:
    if not _is_production():
        return
    key = _client_key(request)
    if not _run_all_limiter.is_allowed(key):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded: run-all max 6/min. Use /watchlist for passive monitoring.",
        )


def check_run_once(request: Request) -> None:
    if not _is_production():
        return
    key = _client_key(request)
    if not _run_once_limiter.is_allowed(key):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded: run-once max 20/min.",
        )


def check_backtest(request: Request) -> None:
    if not _is_production():
        return
    key = _client_key(request)
    if not _backtest_limiter.is_allowed(key):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded: backtest max 10/min.",
        )


def check_login(request: Request) -> None:
    """Rate-limit password login in every environment.

    Use the directly connected peer rather than X-Forwarded-For. Sterling's
    supported production deployment is loopback/reverse-proxy controlled; trusting
    an arbitrary forwarded header here would let an attacker rotate fake source
    addresses and bypass the brute-force guard.
    """
    key = request.client.host if request.client else "unknown"
    if not _login_limiter.is_allowed(key):
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Try again later.",
        )


def check_refresh(request: Request) -> None:
    """Rate-limit refresh-token exchange in every environment (token-guessing guard)."""
    key = request.client.host if request.client else "unknown"
    if not _refresh_limiter.is_allowed(key):
        raise HTTPException(
            status_code=429,
            detail="Too many token refresh attempts. Try again later.",
        )
