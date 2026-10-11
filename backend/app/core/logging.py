import logging
import re
import sys
from collections import deque
from itertools import count
from uuid import uuid4
from app.core.config import settings

# ── secret redaction ─────────────────────────────────────────────────────────
# httpx logs every request line at INFO ("HTTP Request: POST
# https://api.telegram.org/bot<TOKEN>/sendMessage"), and records reach stdout, the
# in-memory ring buffer the UI can read, and the websocket log stream.  A bot token
# or broker key must never be written to any of them.
_SECRET_PATTERNS: "list[tuple[re.Pattern[str], str]]" = [
    # Mask Bearer before the field matcher consumes the word and leaves the token.
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [redacted]"),
    (re.compile(r"(?i)([\"']?(?:password|authorization|cookie|set-cookie|api[_-]?key|api[_-]?secret|access[_-]?token|request[_-]?token|refresh[_-]?token|secret|enctoken|checksum)[\"']?\s*[:=]\s*)(?:\"[^\"\n]*\"|'[^'\n]*'|[^\s,\"'&}]+)"), r"\1[redacted]"),
    (re.compile(r"bot\d{5,}:[A-Za-z0-9_-]{20,}"), "bot[redacted]"),
    (re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b"), "[redacted]"),
    (
        re.compile(
            r"(?i)\b(access[_-]?token|request[_-]?token|refresh[_-]?token|api[_-]?secret|"
            r"api[_-]?key|enctoken|checksum|password|secret|authorization)"
            r"(\s*[=:]\s*)(?!\[redacted\])([A-Za-z0-9._~+/=-]{8,})"
        ),
        r"\1\2[redacted]",
    ),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{10,}"), "bearer [redacted]"),
]


def redact_secrets(text: object) -> str:
    out = str(text if text is not None else "")
    for pattern, repl in _SECRET_PATTERNS:
        out = pattern.sub(repl, out)
    return out


class RedactSecretsFilter(logging.Filter):
    """Rewrite a record so no credential survives into any handler's output."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a broken format string must still be logged
            return True
        cleaned = redact_secrets(message)
        if cleaned != message:
            record.msg = cleaned
            record.args = ()
        if record.exc_info:
            record.exc_text = redact_secrets(logging.Formatter().formatException(record.exc_info))
        if record.stack_info:
            record.stack_info = redact_secrets(record.stack_info)
        return True


_REDACTOR = RedactSecretsFilter()
_NOISY_HTTP_LOGGERS = ("httpx", "httpcore")


# ── In-memory ring buffer of recent server logs ──────────────────────────────
# Lets the UI (Sterling Kite Terminal) interleave real backend logs with engine activity
# without writing/tailing a file. Bounded so it can never grow unbounded.
_LOG_RING: "deque[dict]" = deque(maxlen=1000)
_LOG_SEQUENCE = count(1)
_LOG_SESSION = uuid4().hex[:12]


class RingBufferLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            from app.core.observability import get_correlation_id
            _LOG_RING.append({
                "id": f"{_LOG_SESSION}:{next(_LOG_SEQUENCE)}",
                "ts_ms": int(record.created * 1000),
                "level": record.levelname,
                "name": record.name,
                "message": redact_secrets(self.format(record))[:8000],
                "correlation_id": getattr(record, "correlation_id", None) or get_correlation_id(),
                **{key: getattr(record, key) for key in ("method", "path", "status", "duration_ms") if hasattr(record, key)},
            })
        except Exception:
            pass


def attach_ring_logger() -> None:
    root = logging.getLogger()
    existing = [h for h in root.handlers if isinstance(h, RingBufferLogHandler)]
    if existing:
        for h in existing:
            if _REDACTOR not in h.filters:
                h.addFilter(_REDACTOR)
        return
    handler = RingBufferLogHandler()
    handler.addFilter(_REDACTOR)
    root.addHandler(handler)


def recent_logs(limit: int = 300) -> list[dict]:
    items = list(_LOG_RING)
    return items[-min(1000, max(1, limit)):]


def setup_logging() -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    for handler in logging.getLogger().handlers:
        if _REDACTOR not in handler.filters:
            handler.addFilter(_REDACTOR)
    # Request lines carry the full URL (bot tokens live in the path); the transport
    # libraries have nothing operators need at INFO.
    for name in _NOISY_HTTP_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    attach_ring_logger()

import asyncio
log_queue = None

class WsLogHandler(logging.Handler):
    def emit(self, record):
        global log_queue
        if log_queue is None:
            return
        try:
            msg = self.format(record)
            # Try to push to queue from threadsafe mechanism if possible, but put_nowait is ok for same thread
            # Or better, just get the running loop
            loop = asyncio.get_running_loop()
            payload = {"type": "log", "message": redact_secrets(msg), "level": record.levelname, "name": record.name}
            def enqueue():
                if log_queue.full():
                    log_queue.get_nowait()
                log_queue.put_nowait(payload)
            loop.call_soon_threadsafe(enqueue)
        except Exception:
            pass

def attach_ws_logger():
    global log_queue
    if log_queue is None:
        log_queue = asyncio.Queue(maxsize=1000)
    root = logging.getLogger()
    handler = WsLogHandler()
    handler.addFilter(_REDACTOR)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
    handler.setFormatter(formatter)
    root.addHandler(handler)

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

