"""HTTP-edge hardening: request-size limit, no-store API caching, operator audit trail.

One pure-ASGI middleware (no BaseHTTPMiddleware: it must not buffer SSE streams or
add a task per request).

* **Body limit** - uvicorn imposes none and only nginx (Docker deployment) had one.
  A Content-Length above the limit is refused before the handler runs; a chunked
  body is counted as it arrives and cut off at the limit (413 either way).
  ``STERLING_MAX_BODY_BYTES`` (default 8 MiB).
* **Cache-Control: no-store** on ``/api/`` and ``/health`` responses that do not set
  their own policy: positions, orders and signals must not sit in a browser or proxy
  cache.
* **Operator audit trail** - one JSON line per state-changing API request (and the
  Kite login callback): who, what route, outcome, from where. Never the query string
  (it carries ``request_token`` / ``state``) and never a body. Append-only JSONL,
  mode 0600, size-rotated, and a failure to write can never fail the request.
  ``STERLING_AUDIT_LOG`` overrides the path (default ``data/audit/operator_audit.jsonl``
  under the state root).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("sterling.audit")

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_AUDITED_SAFE_PATHS = frozenset({"/api/v1/kite/callback"})
_DEFAULT_MAX_BODY = 8 * 1024 * 1024
_DEFAULT_AUDIT_MAX = 20 * 1024 * 1024

_audit_lock = threading.Lock()
_audit_failure_reported = False


def _int_env(name: str, default: int) -> int:
    try:
        value = int((os.environ.get(name, "") or "").strip() or default)
        return value if value > 0 else default
    except ValueError:
        return default


def max_body_bytes() -> int:
    return _int_env("STERLING_MAX_BODY_BYTES", _DEFAULT_MAX_BODY)


def audit_path() -> str:
    configured = (os.environ.get("STERLING_AUDIT_LOG") or "").strip()
    if configured:
        return configured
    from app.core.state_paths import state_path

    return str(state_path("data/audit/operator_audit.jsonl"))


def _write_audit(row: dict) -> None:
    global _audit_failure_reported
    try:
        path = audit_path()
        line = (json.dumps(row, separators=(",", ":"), default=str) + "\n").encode("utf-8")
        with _audit_lock:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            try:
                if os.path.getsize(path) >= _int_env("STERLING_AUDIT_MAX_BYTES", _DEFAULT_AUDIT_MAX):
                    os.replace(path, path + ".1")
            except FileNotFoundError:
                pass
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, line)
            finally:
                os.close(fd)
    except Exception as exc:  # noqa: BLE001 - auditing must never fail a request
        if not _audit_failure_reported:
            _audit_failure_reported = True
            log.warning("operator audit log unwritable: %s", exc)


def _is_api(path: str) -> bool:
    return path.startswith("/api/") or path == "/health"


def _should_audit(method: str, path: str) -> bool:
    if not path.startswith("/api/"):
        return False
    return method.upper() not in _SAFE_METHODS or path in _AUDITED_SAFE_PATHS


def _header(headers: list, name: bytes) -> Optional[bytes]:
    for key, value in headers:
        if key.lower() == name:
            return value
    return None


class HttpHardeningMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "GET")).upper()
        path = str(scope.get("path", ""))
        started = time.perf_counter()
        limit = max_body_bytes()
        state = {"status": 0, "rejected": False, "cid": ""}
        received = 0

        async def emit_413() -> None:
            body = b'{"detail":"Request body too large"}'
            state["rejected"] = True
            state["status"] = 413
            await send({
                "type": "http.response.start", "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"cache-control", b"no-store"),
                ],
            })
            await send({"type": "http.response.body", "body": body})

        async def limited_receive() -> dict:
            nonlocal received
            if state["rejected"]:
                return {"type": "http.disconnect"}
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body") or b"")
                if received > limit:
                    await emit_413()
                    # The handler sees a vanished client; whatever it then tries to
                    # send is dropped below because the 413 already went out.
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: dict) -> None:
            if state["rejected"]:
                return
            if message.get("type") == "http.response.start":
                state["status"] = int(message.get("status", 0))
                headers = list(message.get("headers") or [])
                cid = _header(headers, b"x-correlation-id")
                if cid:
                    state["cid"] = cid.decode("latin-1")[:64]
                if _is_api(path) and _header(headers, b"cache-control") is None:
                    headers.append((b"cache-control", b"no-store"))
                    message = {**message, "headers": headers}
            await send(message)

        try:
            declared = _header(list(scope.get("headers") or []), b"content-length")
            too_big = False
            if declared is not None:
                try:
                    too_big = int(declared) > limit
                except ValueError:
                    too_big = False
            if too_big:
                await emit_413()
            else:
                try:
                    await self.app(scope, limited_receive, guarded_send)
                except Exception:
                    # The handler saw the synthetic disconnect we fed it after the
                    # 413 went out; that is the expected end of this request.
                    if not state["rejected"]:
                        raise
        finally:
            if _should_audit(method, path):
                client = scope.get("client")
                user = (scope.get("state") or {}).get("sterling_user_id")
                if not state["cid"]:
                    # The app's own middleware (outside this one) mints/echoes the id;
                    # its context variable is visible here, its response header is not.
                    try:
                        from app.core.observability import get_correlation_id

                        state["cid"] = (get_correlation_id() or "")[:64]
                    except Exception:  # noqa: BLE001
                        state["cid"] = ""
                _write_audit({
                    "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                    "method": method,
                    "path": path[:300],
                    "status": state["status"],
                    "user": user,
                    "peer": client[0] if client else None,
                    "cid": state["cid"] or None,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                })


def install_http_hardening(app: Any) -> None:
    """Register the middleware on a FastAPI/Starlette app."""
    app.add_middleware(HttpHardeningMiddleware)


__all__ = ["HttpHardeningMiddleware", "install_http_hardening", "max_body_bytes", "audit_path"]
