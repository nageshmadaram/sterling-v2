"""Alerts from the virtual tenant are prefixed, never say "live", and reach the owner's chat."""
from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace

import pytest
from tenancy_support import OWNER, SIM_ACCOUNT_ID, SIM_UID

from app.services import db
from app.services.exchanges.kite import accounts as kite_accounts
from app.services.exchanges.kite.models import KiteAccountCreate
from app.services.notifications import lifecycle_format as lf
from app.services.notifications import system_alerts as sa
from app.services.notifications import telegram_kite as tk
from app.services.notifications import telegram_lifecycle as outbox


@pytest.fixture(autouse=True)
def alerts_db(tmp_path, monkeypatch):
    path = str(tmp_path / "tenancy_alerts.db")
    monkeypatch.setenv("STERLING_DB_PATH", path)
    monkeypatch.setenv("STERLING_ALERTS_IN_TESTS", "1")
    monkeypatch.setattr(db, "_DB_PATH", path)
    monkeypatch.setattr(db, "_available", True)
    db.init()
    outbox.clear_for_tests()
    sa.reset_for_tests()
    yield
    outbox.clear_for_tests()


def _rows(uid):
    return outbox.due(uid, now_ms=outbox._now_ms() + 10)


def _render(event, uid):
    return lf.render_lifecycle(
        strategy=event.strategy, event_type=event.event_type, title=event.title,
        detail_text=event.detail, severity=event.severity, created_at_ms=event.created_at_ms,
        mode="LIVE", now_ms=event.created_at_ms, uid=uid,
    )


def _says_live(text):
    return re.search(r"(?i)\blive\b", lf.to_plain(text)) is not None


# ── the renderer ─────────────────────────────────────────────────────────────
def test_simulation_alert_is_prefixed_footed_and_never_says_live():
    text = lf.render_lifecycle(
        strategy="sterling-kite", event_type="ENTRY FILLED", title="NIFTY26OCT24500CE",
        detail_text="Entry 120.5 · Live session ok · LIVE floor", severity="INFO",
        created_at_ms=1_760_000_000_000, mode="LIVE", now_ms=1_760_000_000_000, uid=SIM_UID,
    )
    assert text.startswith("🧪 SIMULATION")
    assert "<i>SIMULATION" in text
    assert not _says_live(text) and "LIVE" not in text
    assert "SuperTrend" in text and "NIFTY26OCT24500CE" in text      # still the same readable card


def test_live_alert_is_unchanged_by_the_simulation_feature():
    kwargs = dict(strategy="gamma_move", event_type="EXIT FILLED", title="BANKNIFTY26OCT52000PE",
                  detail_text="Exit 80 · P&L -500", severity="WARNING",
                  created_at_ms=1_760_000_000_000, mode="LIVE", now_ms=1_760_000_000_000)
    live = lf.render_lifecycle(uid="default", **kwargs)
    assert live == lf.render_lifecycle(**kwargs)                     # uid is optional and inert for live
    assert "SIMULATION" not in live and "🧪" not in live and "<i>LIVE" in live


def test_the_house_rule_still_turns_simulation_into_test_for_live_alerts():
    text = lf.render_lifecycle(strategy="snapback", event_type="NOTE", title="x",
                               detail_text="a simulation note", mode="LIVE", uid="default")
    assert "test note" in text and "simulation" not in text.lower()


def test_header_and_footer_helpers_take_the_simulated_flag():
    assert lf.header("snapback", "SIGNAL", simulated=True).startswith(lf.SIM_BANNER)
    assert lf.footer("LIVE", simulated=True).startswith("<i>SIMULATION")
    assert not lf.header("snapback", "SIGNAL").startswith(lf.SIM_BANNER)


# ── the outbox path the strategies and system alerts share ────────────────────
def test_lifecycle_event_from_the_sim_uid_renders_with_the_prefix_through_telegram_kite():
    outbox.enqueue(SIM_UID, dedupe_key="k1", strategy="sterling-kite", event_type="ENTRY FILLED",
                   title="NIFTY26OCT24500CE", detail="Entry 100 · Live protection ok")
    (event,) = _rows(SIM_UID)
    html = tk._lifecycle_html(event, SIM_UID)
    assert html.startswith("🧪 SIMULATION") and not _says_live(html)
    live_html = tk._lifecycle_html(event, OWNER)
    assert "SIMULATION" not in live_html and not live_html.startswith("🧪")


def test_daily_loss_halt_of_the_sim_tenant_is_alerted_to_the_sim_tenant_only():
    sa.daily_loss_halt(SIM_UID, -20_500.0, -20_000.0)
    assert _rows(OWNER) == []
    (event,) = _rows(SIM_UID)
    assert event.event_type == "DAILY LOSS HALT"
    html = tk._lifecycle_html(event, SIM_UID)
    assert html.startswith("🧪 SIMULATION") and "20,500" in html


def test_platform_alerts_are_not_fanned_out_to_the_virtual_tenant(monkeypatch):
    live = SimpleNamespace(user_id=OWNER, is_active=True)
    sim = SimpleNamespace(user_id=SIM_UID, is_active=True)
    monkeypatch.setattr(kite_accounts, "all_accounts", lambda: [live, sim])
    assert sa._tenants() == [OWNER]


def test_a_live_kill_switch_alert_reaches_the_live_tenant_but_not_the_sim_tenant(monkeypatch):
    from app.services import live_safety

    kite_accounts.ensure_sim_account(OWNER)
    kite_accounts.add(OWNER, KiteAccountCreate(label="L", api_key="k", api_secret="s"))
    outbox.clear_for_tests()
    live_safety.reset_all_for_tests()
    outbox.clear_for_tests()
    live_safety.set_kill_switch(True, "halt for test")
    assert [r.event_type for r in _rows(OWNER)] == ["KILL SWITCH ENGAGED"]
    assert _rows(SIM_UID) == []


def test_the_sim_tenants_own_recovery_transition_alerts_only_the_sim_tenant():
    kite_accounts.ensure_sim_account(OWNER)
    outbox.clear_for_tests()
    db.set_execution_control(operator_state="RUNNING", recovery_state="CLEAN", uid=SIM_UID,
                             account_id=SIM_ACCOUNT_ID, reason_code="t")
    outbox.clear_for_tests()
    sa.reset_for_tests()
    db.set_recovery_state("RECOVERY_REQUIRED", reason_code="BROKER_TRANSPORT_UNCERTAINTY",
                          reason="virtual order unknown", uid=SIM_UID, account_id=SIM_ACCOUNT_ID)
    assert _rows(OWNER) == []
    (event,) = _rows(SIM_UID)
    assert event.event_type == "RECOVERY REQUIRED"


# ── delivery: the sim tenant speaks through the owner's chat ──────────────────
class _Kts:
    def __init__(self, targets):
        self.targets, self.asked = targets, []

    def enabled_targets(self, uid):
        self.asked.append(uid)
        return list(self.targets)


async def test_sim_alerts_are_delivered_to_the_owners_targets_and_signals_are_not_repeated(monkeypatch):
    target = SimpleNamespace(id="t1", bot_token="tok", chat_id="chat")
    kts = _Kts([target])
    drained, signal_calls = [], []

    async def fake_drain(uid, targets, use_legacy):
        drained.append((uid, list(targets), use_legacy))

    monkeypatch.setattr(tk, "_drain_lifecycle", fake_drain)
    monkeypatch.setattr(tk, "alerts_enabled", lambda uid: signal_calls.append(uid) or True)

    await tk._push_user(SIM_UID, kts, True, True)

    assert kts.asked == [OWNER]                        # the owner's destinations, not a sim chat
    assert drained == [(SIM_UID, [target], False)]
    assert signal_calls == []                          # no duplicate signal cards for the sim tenant


async def test_sim_alerts_use_the_legacy_bot_only_through_the_owner(monkeypatch):
    kts = _Kts([])
    drained = []

    async def fake_drain(uid, targets, use_legacy):
        drained.append((uid, use_legacy))

    monkeypatch.setattr(tk, "_drain_lifecycle", fake_drain)
    monkeypatch.setattr(tk, "alerts_enabled", lambda uid: False)

    await tk._push_user(SIM_UID, kts, True, True)
    assert drained == [(SIM_UID, True)]
    drained.clear()
    await tk._push_user("someone-else", kts, True, True)   # a real non-owner tenant never gets the legacy bot
    assert drained == []
