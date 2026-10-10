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
    """Identify client by IP. Falls back to 'unknown'.

    The first X-Forwarded-For entry used here is client-supplied, so any caller could
    rotate it to evade the limit; use the proxy-aware key instead."""
    return limiter_client_key(request)


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


def _is_proxy_address(host: str) -> bool:
    import ipaddress

    try:
        ip = ipaddress.ip_address((host or "").strip())
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def limiter_client_key(request: Request) -> str:
    """The address to rate-limit a login/refresh attempt against.

    Directly connected peer by default. Only when that peer is itself a private or
    loopback address (our own reverse proxy: nginx in Docker, the Vite dev proxy) is
    X-Forwarded-For consulted, and then only its LAST entry - the hop the proxy
    appended. Everything earlier in the header is client-supplied and ignored, so a
    caller cannot rotate fake source addresses to dodge the limit. Without this every
    user behind nginx shared one bucket, so one noisy client could lock the operator
    out of login.
    """
    peer = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded and _is_proxy_address(peer):
        import ipaddress

        last = forwarded.split(",")[-1].strip()
        try:
            ipaddress.ip_address(last)
        except ValueError:
            return peer
        return last
    return peer


def check_login(request: Request) -> None:
    """Rate-limit password login in every environment.

    Keyed by :func:`limiter_client_key`: the directly connected peer, or - behind
    our own private-address reverse proxy - the single address that proxy appended.
    An arbitrary client-supplied X-Forwarded-For is never trusted.
    """
    key = limiter_client_key(request)
    if not _login_limiter.is_allowed(key):
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Try again later.",
        )


def check_refresh(request: Request) -> None:
    """Rate-limit refresh-token exchange in every environment (token-guessing guard)."""
    key = limiter_client_key(request)
    if not _refresh_limiter.is_allowed(key):
        raise HTTPException(
            status_code=429,
            detail="Too many token refresh attempts. Try again later.",
        )
