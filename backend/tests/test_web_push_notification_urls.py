"""Web Push destinations must only address read-only Sterling workspaces."""
from urllib.parse import parse_qs, urlsplit

from app.services.notifications.web_push import notification_url


def test_signal_push_keeps_strategy_exact_signal_and_ist_timestamp():
    link = notification_url(
        "signals", strategy="snapback", signal_id="sb:NSE:ABC 42",
        symbol="ABC LTD", at_ms=1793160000000,
    )
    parsed = urlsplit(link)
    assert parsed.path == "/"
    assert parsed.netloc == ""
    assert parse_qs(parsed.query) == {
        "view": ["live"],
        "notification": ["signals"],
        "strategy": ["snapback"],
        "signal": ["sb:NSE:ABC 42"],
        "symbol": ["ABC LTD"],
        "at": ["1793160000000"],
    }


def test_supertrend_and_gamma_notifications_use_supported_engine_names():
    for strategy, expected in [
        ("kite_supertrend", "supertrend"),
        ("gamma_move", "gamma_move"),
        ("ema_trend", "ema_trend"),
    ]:
        query = parse_qs(urlsplit(notification_url(
            "signals", strategy=strategy, symbol="NIFTY",
        )).query)
        assert query["strategy"] == [expected]


def test_unknown_category_and_untrusted_strategy_cannot_redirect():
    assert notification_url("https://evil.invalid") == "/"
    query = parse_qs(urlsplit(notification_url(
        "signals", strategy="https://evil.invalid/x",
        signal_id="x" * 200, symbol="N" * 200, at_ms=-1,
    )).query)
    assert query == {"view": ["live"], "notification": ["signals"]}


def test_non_signal_messages_open_the_correct_builtin_view_without_order_links():
    for category in ("pcr", "alerts"):
        query = parse_qs(urlsplit(notification_url(
            category, strategy="snapback", signal_id="dangerous-order",
        )).query)
        assert query == {"view": ["live"], "notification": [category]}
