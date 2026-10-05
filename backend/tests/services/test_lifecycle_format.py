"""Canonical Telegram alert wording shared by SuperTrend, Snapback and Gamma Unwind."""
from __future__ import annotations

import re

import pytest

from app.services.notifications import lifecycle_format as fmt


def _ms(iso: str) -> int:
    from datetime import datetime

    return int(datetime.fromisoformat(iso).timestamp() * 1000)


T_IST = _ms("2026-10-05T10:12:03+05:30")


def test_inr_uses_one_format_everywhere():
    assert fmt.inr(1234.5) == "₹1,234.50"
    assert fmt.inr(0.05) == "₹0.05"
    assert fmt.inr(-50) == "-₹50.00"
    assert fmt.inr(500, signed=True) == "+₹500.00"
    assert fmt.inr(-1234.5, signed=True) == "-₹1,234.50"
    # A rounded-to-zero loss must not print a confusing negative zero.
    assert fmt.inr(-0.001, signed=True) == "+₹0.00"
    assert fmt.inr(None) == "—"
    assert fmt.inr(float("nan")) == "—"


def test_ist_stamp_is_asia_kolkata_not_utc():
    assert fmt.ist_stamp(T_IST) == "05 Oct 10:12:03"
    assert fmt.ist_stamp(0) == ""
    assert fmt.ist_stamp("bad") == ""


@pytest.mark.parametrize(
    "symbol,kind",
    [
        ("NIFTY26OCT25000CE", "CE"),
        ("NFO:BANKNIFTY26OCT52000PE", "PE"),
        ("NIFTY26OCTFUT", "FUT"),
        ("RELIANCE", ""),
        ("", ""),
    ],
)
def test_instrument_kind(symbol, kind):
    assert fmt.instrument_kind(symbol) == kind


def test_strategy_labels_are_stable_across_spellings():
    assert fmt.strategy_label("Gamma-Unwind") == "Gamma Unwind"
    assert fmt.strategy_label("gamma_move") == "Gamma Unwind"
    assert fmt.strategy_label("kite_supertrend") == "SuperTrend"
    assert fmt.strategy_label("SuperTrend") == "SuperTrend"
    assert fmt.strategy_label("snapback") == "Snapback"
    assert fmt.strategy_label("") == "Sterling"


# (event type, historical detail string) -> canonical detail
_DIALECTS = [
    # SuperTrend
    ("ENTRY FILLED", "Qty 75 · Fill ₹123.4 · SL ₹100", "Entry ₹123.40 · SL ₹100.00 · Qty 75"),
    ("ENTRY FILLED", "Filled 75/75 · Avg ₹123.40 · SL ₹100.00", "Entry ₹123.40 · SL ₹100.00 · Qty 75"),
    ("PARTIAL ENTRY FILL", "Filled 40/75 · Avg ₹123.40 · SL ₹100.00", "Entry ₹123.40 · SL ₹100.00 · Qty 40/75"),
    ("SL ARMED", "Broker GTT #12 · SL ₹100.00 · Qty 75", "SL ₹100.00 · Qty 75 · GTT #12"),
    ("TSL UPDATED", "₹100.00 → ₹110.00 · Qty 75 · GTT #12", "TSL ₹100.00 → ₹110.00 · Qty 75 · GTT #12"),
    ("TSL SYNCED", "SL ₹100.00 → ₹110.00 · Qty 75 · GTT #12", "TSL ₹100.00 → ₹110.00 · Qty 75 · GTT #12"),
    (
        "PROTECTION FAILED",
        "Broker TSL update not confirmed · Qty 75 · required SL ₹110.00 · new entries must remain halted",
        "SL ₹110.00 · Qty 75 · Broker TSL update not confirmed · new entries must remain halted",
    ),
    (
        "POSITION CLOSED",
        "Exit filled 75/75 · Avg ₹150.00 · stop breach",
        "Exit ₹150.00 · Qty 75 · Reason stop breach",
    ),
    (
        "POSITION CLOSED",
        "Qty 75 · trailing stop · Entry ₹120.00",
        "Entry ₹120.00 · Qty 75 · Reason trailing stop",
    ),
    # Gamma Unwind
    (
        "ENTRY FILLED",
        "Qty 75 · Fill ₹120.00 · SL ₹100.00 · T1 ₹130.00 · T2 ₹140.00 · INTRADAY",
        "Entry ₹120.00 · SL ₹100.00 · T1 ₹130.00 · T2 ₹140.00 · Qty 75 · INTRADAY",
    ),
    (
        "POSITION CLOSED",
        "Qty 75 · Exit ₹150.00 · SL · P&L ₹-1,500.00",
        "Exit ₹150.00 · P&L -₹1,500.00 · Qty 75 · Reason SL",
    ),
    (
        "POSITION CLOSED",
        "Qty 75 · Exit ₹160.00 · T2 · P&L ₹3,000.00",
        "Exit ₹160.00 · P&L +₹3,000.00 · Qty 75 · Reason T2",
    ),
    (
        "PARTIAL EXIT",
        "Sold 25/50 @ ₹130.00 · 25 remains · T1",
        "Exit ₹130.00 · Qty 25/50 · Remaining 25 · Reason T1",
    ),
    (
        "T1 / BREAKEVEN",
        "T1 ₹130.00 reached · TSL moved to entry ₹120.00 · T2 ₹140.00",
        "TSL ₹120.00 · T1 ₹130.00 · T2 ₹140.00",
    ),
]


@pytest.mark.parametrize("event,raw,expected", _DIALECTS)
def test_every_known_dialect_maps_to_the_canonical_vocabulary(event, raw, expected):
    assert fmt.canonicalize_detail(event, raw) == expected


def test_equivalent_events_render_with_identical_labels_across_strategies():
    """SuperTrend and Gamma entries must expose the same field names in the same order."""
    st = fmt.canonicalize_detail("ENTRY FILLED", "Filled 75/75 · Avg ₹123.40 · SL ₹100.00")
    gm = fmt.canonicalize_detail("ENTRY FILLED", "Qty 75 · Fill ₹123.40 · SL ₹100.00")
    assert st == gm

    st_exit = fmt.canonicalize_detail("POSITION CLOSED", "Exit filled 75/75 · Avg ₹90.00 · stop")
    gm_exit = fmt.canonicalize_detail("POSITION CLOSED", "Qty 75 · Exit ₹90.00 · stop")
    assert st_exit == gm_exit


def test_builder_produces_canonical_order_and_skips_missing_fields():
    out = fmt.detail(
        entry=100, ltp=105.5, sl=90, tsl=95, tsl_from=90,
        targets=[("T1", 120)], exit=None, pnl=-250.0, qty=75, qty_total=100,
        capital=50000, reason="stop",
    )
    assert out == (
        "Entry ₹100.00 · LTP ₹105.50 · SL ₹90.00 · TSL ₹90.00 → ₹95.00 · "
        "T1 ₹120.00 · P&L -₹250.00 · Qty 75/100 · Capital ₹50,000.00 · Reason stop"
    )
    assert fmt.detail(qty=1) == "Qty 1"


def test_builder_output_is_a_fixed_point_of_canonicalisation():
    built = fmt.detail(entry=100, sl=90, pnl=12, qty=75, reason="target")
    assert fmt.canonicalize_detail("POSITION CLOSED", built) == built


def test_side_line_shows_option_kind_and_buy_sell():
    entry = fmt.render_lifecycle(
        strategy="SuperTrend", event_type="ENTRY FILLED", title="NIFTY26OCT25000CE",
        detail_text="Qty 75", created_at_ms=T_IST, mode="LIVE", now_ms=T_IST + 1000,
    )
    assert "<b>NIFTY26OCT25000CE</b> · CE · BUY" in entry
    exit_ = fmt.render_lifecycle(
        strategy="Gamma-Unwind", event_type="POSITION CLOSED", title="NIFTY26OCT25000PE",
        detail_text="Qty 75", created_at_ms=T_IST, mode="LIVE", now_ms=T_IST + 1000,
    )
    assert "· PE · SELL" in exit_
    assert "STERLING · Gamma Unwind · POSITION CLOSED" in exit_
    assert exit_.rstrip().endswith("<i>LIVE · 05 Oct 10:12:03 IST</i>")


def test_message_is_html_safe_for_stray_markup_characters():
    out = fmt.render_lifecycle(
        strategy="SuperTrend", event_type="PROTECTION <FAILED>",
        title="A&B_<x>*", detail_text="reason: a < b && c > d _under_ *star* [x](y)",
        created_at_ms=T_IST, mode="LIVE", now_ms=T_IST,
    )
    # Only our own b/i tags may appear; everything operator-visible is escaped.
    stripped = re.sub(r"</?(?:b|i)>", "", out)
    assert "<" not in stripped and ">" not in stripped
    assert "&amp;&amp;" in out and "&lt;" in out and "&gt;" in out


def test_message_never_exceeds_telegram_limit_even_when_every_char_expands():
    out = fmt.render_lifecycle(
        strategy="S" * 5000, event_type="E" * 5000, title="&" * 9000,
        detail_text="<&>" * 9000, severity="CRITICAL", created_at_ms=T_IST, mode="LIVE",
    )
    assert len(out) <= fmt.TELEGRAM_MAX_CHARS
    # Truncation must not leave a half-escaped entity behind.
    assert not re.search(r"&(?!amp;|lt;|gt;)", out)


def test_secrets_are_redacted_from_titles_and_details():
    token = "123456789:" + "A" * 35
    out = fmt.render_lifecycle(
        strategy="SuperTrend", event_type="PROTECTION FAILED", title="x",
        detail_text=(
            f"POST https://api.telegram.org/bot{token}/sendMessage failed · "
            "access_token=abcdEFGH12345678 · api_secret: s3cr3tvalue99 · Bearer abcdefghijklmnop"
        ),
        created_at_ms=T_IST, mode="LIVE", now_ms=T_IST,
    )
    assert token not in out
    assert "abcdEFGH12345678" not in out
    assert "s3cr3tvalue99" not in out
    assert "abcdefghijklmnop" not in out
    assert "[redacted]" in out


def test_paper_and_simulation_wording_never_reaches_an_alert():
    out = fmt.render_lifecycle(
        strategy="Snapback", event_type="ENTRY FILLED", title="Paper position",
        detail_text="simulated fill in SIMULATION mode via paper account",
        created_at_ms=T_IST, mode="SIMULATION", now_ms=T_IST,
    )
    assert not re.search(r"(?i)paper|simulat", out)
    assert fmt.mode_text("SIMULATION") == "TEST"
    assert fmt.mode_text("PAPER-u1") == "TEST"
    assert fmt.mode_text("LIVE") == "LIVE"
    assert fmt.mode_text("no account") == "NO ACCOUNT"


def test_late_delivery_is_flagged_with_original_ist_time():
    out = fmt.render_lifecycle(
        strategy="SuperTrend", event_type="POSITION CLOSED", title="NIFTY26OCT25000CE",
        detail_text="Qty 75", created_at_ms=T_IST, mode="LIVE", now_ms=T_IST + 12 * 60_000,
    )
    assert "05 Oct 10:12:03 IST" in out
    assert "delayed 12m" in out


def test_plain_fallback_strips_markup_and_unescapes():
    assert fmt.to_plain("<b>A &amp; B</b>\n<i>x &lt; y</i>") == "A & B\nx < y"
