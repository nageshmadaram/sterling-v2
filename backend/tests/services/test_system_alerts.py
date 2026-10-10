"""Platform-level Telegram alerts: halts, SAFE_MODE, daily loss, broker session, orders."""
from __future__ import annotations

import re
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.services import db, live_safety
from app.services.notifications import lifecycle_format as lf
from app.services.notifications import system_alerts as sa
from app.services.notifications import telegram_lifecycle as outbox


@pytest.fixture(autouse=True)
def alerts_db(tmp_path, monkeypatch):
    path = str(tmp_path / "system_alerts.db")
    monkeypatch.setenv("STERLING_DB_PATH", path)
    monkeypatch.setenv("STERLING_ALERTS_IN_TESTS", "1")  # opt in; unit tests are muted by default
    monkeypatch.setattr(db, "_DB_PATH", path)
    monkeypatch.setattr(db, "_available", True)
    db.init()
    outbox.clear_for_tests()
    sa.reset_for_tests()
    monkeypatch.setattr(sa, "_tenants", lambda: ["default"])
    yield
    outbox.clear_for_tests()


def _rows(uid="default"):
    return outbox.due(uid, now_ms=outbox._now_ms() + 10)


def _render(event):
    return lf.render_lifecycle(
        strategy=event.strategy, event_type=event.event_type, title=event.title,
        detail_text=event.detail, severity=event.severity,
        created_at_ms=event.created_at_ms, mode="LIVE", now_ms=event.created_at_ms,
    )


def test_unit_tests_are_muted_unless_they_opt_in(monkeypatch):
    monkeypatch.delenv("STERLING_ALERTS_IN_TESTS")
    sa.safe_mode_engaged(triggers=["OPERATOR"], reason="x", entered_at="t")
    assert _rows() == []


def test_kill_switch_engage_and_release_are_alerted_once_each():
    live_safety.reset_all_for_tests()
    outbox.clear_for_tests()
    live_safety.set_kill_switch(True, "emergency stop")
    rows = _rows()
    assert [r.event_type for r in rows] == ["KILL SWITCH ENGAGED"]
    assert rows[0].severity == "CRITICAL"
    text = _render(rows[0])
    assert "emergency stop" in text and "STERLING · KILL SWITCH ENGAGED" in text

    # Writing the same state again is not a new event.
    live_safety.set_kill_switch(True, "emergency stop")
    assert len(_rows()) == 1

    live_safety.set_kill_switch(False)
    assert [r.event_type for r in _rows()] == ["KILL SWITCH ENGAGED", "TRADING RESUMED"]


def test_unknown_outcome_recovery_required_is_critical_and_boot_markers_are_silent():
    live_safety.reset_all_for_tests()
    outbox.clear_for_tests()
    db.set_recovery_state(
        "RECOVERY_REQUIRED", reason_code="BROKER_TRANSPORT_UNCERTAINTY",
        reason="Broker submit uncertain for intent X",
    )
    db.set_recovery_state("CLEAN", reason_code="reconciled", reason="ok")
    db.set_recovery_state("RECOVERY_REQUIRED", reason_code="STARTUP", reason="start")
    rows = _rows()
    # The boot sequence's own STARTUP marker (and its clearing) is announced once,
    # by live_runtime's LIVE STATUS message, not here.
    assert [(r.event_type, r.severity) for r in rows] == [
        ("RECOVERY REQUIRED", "CRITICAL"),
        ("RECOVERY CLEAN", "INFO"),
    ]
    db.set_recovery_state("CLEAN", reason_code="reconciled", reason="boot complete")
    assert len(_rows()) == 2
    assert "Broker submit uncertain" in rows[0].detail


def test_safe_mode_entry_new_trigger_and_release(tmp_path):
    from app.services.safe_mode import SafeModeService, SafeModeTrigger

    svc = SafeModeService(tmp_path / "safe.json")
    svc.engage(trigger=SafeModeTrigger.OPERATOR, reason="operator pressed stop")
    svc.engage(trigger=SafeModeTrigger.OPERATOR, reason="again")  # no new information
    svc.engage(trigger=SafeModeTrigger.POSITION_MISMATCH, reason="broker holds 75 more")
    svc.release(operator_ack=True, note="checked")
    kinds = [r.event_type for r in _rows()]
    assert kinds == ["SAFE MODE ENGAGED", "SAFE MODE ENGAGED", "SAFE MODE RELEASED"]
    assert "POSITION_MISMATCH" in _rows()[1].detail


def test_daily_loss_halt_alerts_once_per_day_with_pnl_and_limit():
    live_safety.reset_all_for_tests()
    outbox.clear_for_tests()
    live_safety.configure_daily_loss(
        live_safety.DailyLossConfig(soft_warn_usd=-100.0, hard_halt_usd=-200.0)
    )
    pos = [SimpleNamespace(
        exit_timestamp_ms=int(datetime.now().timestamp() * 1000), realized_pnl_usd=-300.0,
    )]
    for _ in range(3):
        decision = live_safety.assert_safe_to_trade(positions=pos)
        assert decision.code == "daily_loss_halt"
    rows = _rows()
    assert len(rows) == 1 and rows[0].event_type == "DAILY LOSS HALT"
    text = _render(rows[0])
    assert "P&amp;L -₹300.00" in text and "Limit -₹200.00" in text


def _request(strategy="sterling-kite", effect="INCREASE_EXPOSURE", symbol="NIFTY26OCT25000CE"):
    return SimpleNamespace(
        uid="default", strategy_id=strategy, symbol=symbol, side="BUY", quantity=75,
        signal_id="SIG1", exposure_effect=SimpleNamespace(value=effect),
    )


def _result(status, success=False, **kw):
    base = dict(success=success, status=status, error="", intent_key="INT1", order_id="", filled_price=0.0)
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.parametrize(
    "strategy,label",
    [("sterling-kite", "SuperTrend"), ("snapback", "Snapback"), ("gamma_move", "Gamma Unwind")],
)
def test_every_strategys_order_outcomes_use_the_same_alert_vocabulary(strategy, label):
    sa.order_submission(_request(strategy), _result("ACKNOWLEDGED", True, order_id="2501"))
    sa.order_submission(_request(strategy, "CLOSE_POSITION"), _result("ACKNOWLEDGED", True, intent_key="INT2", order_id="2502"))
    sa.order_submission(_request(strategy), _result("UNKNOWN", error="timeout", intent_key="INT3"))
    sa.order_submission(_request(strategy), _result("HALTED", error="Kill switch active", intent_key=""))
    rows = _rows()
    assert [r.event_type for r in rows] == [
        "ENTRY SUBMITTED", "EXIT SUBMITTED", "ORDER OUTCOME UNKNOWN", "ENTRY BLOCKED",
    ]
    assert {r.strategy for r in rows} == {label}
    assert [r.severity for r in rows] == ["INFO", "INFO", "CRITICAL", "WARNING"]
    entry = _render(rows[0])
    assert "· CE · BUY" in entry and "Qty 75" in entry and "Order 2501" in entry
    assert "· CE · SELL" in _render(rows[1])


def test_blocked_entry_for_one_signal_is_not_repeated_every_scan():
    for _ in range(5):
        sa.order_submission(_request(), _result("HALTED", error="Kill switch active", intent_key=""))
    assert len(_rows()) == 1


def test_failed_exit_is_critical():
    sa.order_submission(_request(effect="CLOSE_POSITION"), _result("REJECTED", error="verify failed", intent_key="INT9"))
    row = _rows()[0]
    assert (row.event_type, row.severity) == ("EXIT NOT PLACED", "CRITICAL")


def _intent(strategy="snapback", state="PARTIAL", filled=25, qty=75, value=2500.0, exit_=False, recon=False):
    return SimpleNamespace(
        intent_key="K1", uid="default", strategy_id=strategy, symbol="NIFTY26OCT25000CE",
        side="SELL" if exit_ else "BUY", quantity=qty, state=state, filled_quantity=filled,
        filled_value=value, reconciliation_required=recon,
        payload={"intent_type": "EXIT" if exit_ else "ENTRY"},
    )


def test_snapback_fills_are_announced_from_the_order_journal():
    sa.order_observed(SimpleNamespace(intent=_intent(), accepted=True, delta_quantity=25, reason="accepted"))
    sa.order_observed(SimpleNamespace(
        intent=_intent(state="FILLED", filled=75, value=7500.0), accepted=True,
        delta_quantity=50, reason="accepted"))
    sa.order_observed(SimpleNamespace(
        intent=_intent(state="FILLED", filled=75, value=8250.0, exit_=True), accepted=True,
        delta_quantity=75, reason="accepted"))
    rows = _rows()
    assert [r.event_type for r in rows] == ["PARTIAL ENTRY FILL", "ENTRY FILLED", "EXIT FILLED"]
    assert "Entry ₹100.00" in _render(rows[1]) and "Qty 75" in _render(rows[1])
    assert "Exit ₹110.00" in _render(rows[2])


def test_other_strategies_fills_are_not_double_announced_by_the_journal():
    sa.order_observed(SimpleNamespace(
        intent=_intent("sterling-kite", "FILLED", 75, value=7500.0), accepted=True,
        delta_quantity=75, reason="accepted"))
    assert _rows() == []


def test_conflicting_broker_evidence_is_critical_for_any_strategy():
    sa.order_observed(SimpleNamespace(
        intent=_intent("gamma_move", recon=True), accepted=False, delta_quantity=0,
        reason="overfilled_order"))
    row = _rows()[0]
    assert (row.event_type, row.severity, row.strategy) == (
        "BROKER EVIDENCE CONFLICT", "CRITICAL", "Gamma Unwind")
    assert "overfilled_order" in row.detail


def test_missing_broker_session_is_alerted_in_the_trading_window_only(monkeypatch):
    acct = SimpleNamespace(
        id="KITE-1", user_id="default", is_active=True, is_paper=False,
        access_token_enc="", token_expires_at=None,
    )
    monkeypatch.setattr(
        "app.services.exchanges.kite.accounts.all_accounts", lambda: [acct]
    )
    ist = lf.IST
    before_open = datetime(2026, 10, 5, 8, 0, tzinfo=ist)       # Monday
    in_window = datetime(2026, 10, 5, 9, 5, tzinfo=ist)
    weekend = datetime(2026, 10, 3, 9, 5, tzinfo=ist)           # Saturday
    assert sa.check_broker_sessions(before_open) == 0
    assert sa.check_broker_sessions(weekend) == 0
    assert sa.check_broker_sessions(in_window) == 1
    assert sa.check_broker_sessions(in_window) == 0, "same hour: no repeat"
    row = _rows()[0]
    assert (row.event_type, row.severity) == ("BROKER LOGIN REQUIRED", "CRITICAL")
    assert "Log in to Kite" in _render(row)


def test_valid_session_and_paper_accounts_raise_nothing(monkeypatch):
    live = SimpleNamespace(id="K1", user_id="default", is_active=True, is_paper=False,
                           access_token_enc="enc", token_expires_at=None)
    paper = SimpleNamespace(id="K2", user_id="default", is_active=True, is_paper=True,
                            access_token_enc="", token_expires_at=None)
    monkeypatch.setattr("app.services.exchanges.kite.accounts.all_accounts", lambda: [live, paper])
    monkeypatch.setattr("app.services.exchanges.kite.session.is_expired", lambda _v: False)
    assert sa.check_broker_sessions(datetime(2026, 10, 5, 10, 0, tzinfo=lf.IST)) == 0


def test_no_alert_leaks_a_secret_from_an_exception_message():
    token = "123456789:" + "Z" * 35
    sa.order_submission(
        _request(), _result("UNKNOWN", error=f"POST https://api.kite/x?access_token=abcdef123456 bot{token}"),
    )
    text = _render(_rows()[0])
    assert token not in text and "abcdef123456" not in text
