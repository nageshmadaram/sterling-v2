"""Unit tests for Gamma Unwind reference alert formatters."""
from datetime import date
from app.services.notifications.gamma_move_alerts import (
    format_added_alert,
    format_day_summary_alert,
    format_expiry,
    format_watchlist_alert,
    format_zone_edge,
)


def test_format_zone_edge():
    assert format_zone_edge(1047.3) == "1,047.3"
    assert format_zone_edge(1766.0) == "1,766"
    assert format_zone_edge(5109.0) == "5,109"
    assert format_zone_edge(97.4) == "97.4"
    assert format_zone_edge(62.0) == "62.0"
    assert format_zone_edge(413.1) == "413.1"


def test_format_expiry():
    assert format_expiry("2026-10-27") == "27 Oct"
    assert format_expiry(date(2026, 10, 9)) == "9 Oct"


def test_watchlist_alert_matches_reference_pe():
    day = date(2026, 10, 12)
    stocks = [
        {
            "symbol": "VMM",
            "zone_edge": 97.4,
            "spot": 97.5,
            "gap_pct": 0.1,
            "rel": "above",
            "strikes": [
                {"strike": 95, "ltp": 1.36},
                {"strike": 97, "ltp": 2.29},
            ],
        },
        {
            "symbol": "NAM-INDIA",
            "zone_edge": 1047.3,
            "spot": 1045.2,
            "gap_pct": 0.2,
            "rel": "below",
            "strikes": [{"strike": 1040, "ltp": 19.45}],
        },
        {
            "symbol": "UJJIVANSFB",
            "zone_edge": 62.0,
            "spot": 62.1,
            "gap_pct": 0.2,
            "rel": "above",
            "strikes": [{"strike": 62, "ltp": 2.36}],
        },
        {
            "symbol": "SUNPHARMA",
            "zone_edge": 1766.0,
            "spot": 1761.0,
            "gap_pct": 0.3,
            "rel": "below",
            "strikes": [{"strike": 1740, "ltp": 12.00}],
        },
        {
            "symbol": "LODHA",
            "zone_edge": 1057.4,
            "spot": 1064.8,
            "gap_pct": 0.7,
            "rel": "above",
            "strikes": [{"strike": 1040, "ltp": 18.75}],
        },
    ]
    # Simulate 32 total stocks where remaining strikes count 55
    text = format_watchlist_alert(
        "BREAKDOWN-PE",
        "2026-10-27",
        day,
        stocks,
        max_shown=5,
    )
    assert "🗂️ BREAKDOWN-PE WATCHLIST · 📅 27 Oct" in text
    assert "Mon 12 Oct · live premiums" in text
    assert "NEAREST SUPPORT · 5 stocks" in text
    assert "▼ VMM 95 PE · 27 Oct\n₹1.36 · 0.1% above 97.4" in text
    assert "▼ VMM 97 PE · 27 Oct\n₹2.29 · 0.1% above 97.4" in text
    assert "▼ NAM-INDIA 1040 PE · 27 Oct\n₹19.45 · 0.2% below 1,047.3" in text
    assert "▼ SUNPHARMA 1740 PE · 27 Oct\n₹12.00 · 0.3% below 1,766" in text
    assert "% = spot's gap to support · two strikes per stock" in text


def test_added_alert_matches_reference():
    day = date(2026, 10, 12)
    pe_stocks = [
        {
            "symbol": "BHARTIARTL",
            "zone_edge": 1750.6,
            "spot": 1789.1,
            "gap_pct": 2.2,
            "rel": "above",
            "strikes": [
                {"strike": 1780, "ltp": 16.65},
                {"strike": 1760, "ltp": 10.65},
            ],
        },
        {
            "symbol": "FORTIS",
            "zone_edge": 762.7,
            "spot": 780.2,
            "gap_pct": 2.3,
            "rel": "above",
            "strikes": [
                {"strike": 780, "ltp": 19.00},
                {"strike": 760, "ltp": 11.60},
            ],
        },
        {
            "symbol": "DABUR",
            "zone_edge": 370.1,
            "spot": 379.3,
            "gap_pct": 2.5,
            "rel": "above",
            "strikes": [
                {"strike": 370, "ltp": 2.75},
                {"strike": 365, "ltp": 1.80},
            ],
        },
    ]
    ce_stocks = [
        {
            "symbol": "PNBHOUSING",
            "zone_edge": 1203.9,
            "spot": 1189.4,
            "gap_pct": 1.2,
            "rel": "below",
            "strikes": [
                {"strike": 1200, "ltp": 29.45},
                {"strike": 1220, "ltp": 20.85},
            ],
        },
        {
            "symbol": "ANGELONE",
            "zone_edge": 307.6,
            "spot": 299.9,
            "gap_pct": 2.5,
            "rel": "below",
            "strikes": [
                {"strike": 310, "ltp": 6.90},
                {"strike": 305, "ltp": 8.80},
            ],
        },
        {
            "symbol": "GODREJCP",
            "zone_edge": 885.4,
            "spot": 863.2,
            "gap_pct": 2.5,
            "rel": "below",
            "strikes": [
                {"strike": 900, "ltp": 4.00},
                {"strike": 870, "ltp": 13.20},
            ],
        },
    ]

    text = format_added_alert(
        "11:15",
        "09:20",
        "09:30–11:15",
        day,
        pe_stocks=pe_stocks,
        ce_stocks=ce_stocks,
        pe_expiry="2026-10-27",
        ce_expiry="2026-10-27",
    )

    assert "🗂️ ADDED 11:15 · 6 new stocks" in text
    assert "📅 Mon 12 Oct · reached a zone since 09:20" in text
    assert "premiums at the 09:30–11:15 build" in text
    assert "BREAKDOWN-PE · 📅 27 Oct · 3 stocks near support" in text
    assert "▼ BHARTIARTL 1780 PE · 27 Oct\n₹16.65 · 2.2% above 1,750.6" in text
    assert "BREAKOUT-CE · 📅 27 Oct · 3 stocks near resistance" in text
    assert "▲ PNBHOUSING 1200 CE · 27 Oct\n₹29.45 · 1.2% below 1,203.9" in text
    assert "% = spot's gap to the zone edge · two strikes per stock · full list on the dashboard" in text


def test_day_summary_alert_zero_entries():
    day = date(2026, 10, 12)
    text = format_day_summary_alert(day, entries=[], near_entries=[])
    expected = (
        "🧾 DAY SUMMARY\n"
        "📅 Mon 12 Oct\n"
        "━━━━━━━━━━━━\n"
        "TODAY\n"
        "⚡ 0 entries · 👀 0 near entry\n"
        "No entries today."
    )
    assert text == expected


def test_day_summary_alert_with_near_entry():
    day = date(2026, 10, 9)
    near = [
        {
            "symbol": "INFY",
            "strike": 980,
            "option_type": "PE",
            "expiry": "2026-10-27",
            "time": "13:05",
            "price": 16.45,
        }
    ]
    text = format_day_summary_alert(day, entries=[], near_entries=near)
    assert "🧾 DAY SUMMARY" in text
    assert "📅 Fri 9 Oct" in text
    assert "⚡ 0 entries · 👀 1 near entry" in text
    assert "NEAR ENTRY" in text
    assert "🔵 not entries, not tracked" in text
    assert "▼ INFY 980 PE · 27 Oct" in text
    assert "🕒 13:05 · ₹16.45" in text
    assert "Results from the trade tracker. Alert only, not advice." in text
