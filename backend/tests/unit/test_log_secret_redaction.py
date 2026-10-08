"""No credential may reach stdout, the UI log ring or the log websocket."""
from __future__ import annotations

import logging

from app.core import logging as core_logging

TOKEN = "123456789:" + "T" * 35


def _record(msg, *args):
    return logging.LogRecord("httpx", logging.INFO, __file__, 1, msg, args, None)


def test_filter_scrubs_a_bot_token_from_a_request_line():
    rec = _record('HTTP Request: POST https://api.telegram.org/bot%s/sendMessage "HTTP/1.1 200 OK"', TOKEN)
    assert core_logging.RedactSecretsFilter().filter(rec) is True
    assert TOKEN not in rec.getMessage()
    assert "bot[redacted]/sendMessage" in rec.getMessage()


def test_filter_scrubs_broker_and_bearer_credentials():
    rec = _record("retry failed access_token=abcdEFGH12345678 api_secret: s3cr3tvalue99 Bearer abcdefghijklmnop")
    core_logging.RedactSecretsFilter().filter(rec)
    out = rec.getMessage()
    for leaked in ("abcdEFGH12345678", "s3cr3tvalue99", "abcdefghijklmnop"):
        assert leaked not in out


def test_innocent_text_is_untouched_and_bad_format_strings_still_log():
    rec = _record("order %s placed", "ABC123")
    core_logging.RedactSecretsFilter().filter(rec)
    assert rec.getMessage() == "order ABC123 placed"
    broken = _record("only one %s but %s", "x")
    assert core_logging.RedactSecretsFilter().filter(broken) is True


def test_ring_buffer_and_httpx_level_after_setup(monkeypatch):
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_levels = {n: logging.getLogger(n).level for n in ("httpx", "httpcore")}
    try:
        core_logging._LOG_RING.clear()
        core_logging.setup_logging()
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("httpcore").level == logging.WARNING
        logging.getLogger("tg").warning("POST https://api.telegram.org/bot%s/getUpdates", TOKEN)
        recent = core_logging.recent_logs(5)
        assert recent and all(TOKEN not in item["message"] for item in recent)
    finally:
        for h in list(root.handlers):
            if h not in saved_handlers:
                root.removeHandler(h)
        for name, level in saved_levels.items():
            logging.getLogger(name).setLevel(level)
