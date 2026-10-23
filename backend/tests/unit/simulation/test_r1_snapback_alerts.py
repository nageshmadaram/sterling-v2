"""Snapback lifecycle alerts carry the position's tenant: a virtual one is queued for the virtual
tenant (rendered with the simulation prefix), never as a live alert in the owner's chat."""
from __future__ import annotations

import ast
import pathlib

import pytest
from tenancy_support import OWNER, SIM_UID, wipe_tenants

from app.services import snapback_lifecycle_alerts as alerts
from app.services import snapback_live_wiring as wiring
from app.services.notifications import lifecycle_format as lf
from tests.snapback_live_harness import live_env, run, swing_row
from tests.snapback_live_harness import cycle as _cycle
from tests.snapback_live_harness import signal_id as _signal_id

_ = live_env   # registers the shared ``env`` fixture


@pytest.fixture(autouse=True)
def _clean_tenants(tmp_path, monkeypatch):
    # Keep the patched temporary database alive until tenant teardown is done.
    wipe_tenants()           # the virtual-position test leaves journal rows for the sim tenant in the shared db
    yield
    wipe_tenants()


@pytest.fixture
def sent(monkeypatch):
    out = []

    def fake(uid, **kw):
        out.append({"uid": uid, **kw})
        return "TGL-x"

    monkeypatch.setattr("app.services.notifications.telegram_lifecycle.enqueue_best_effort", fake)
    monkeypatch.setattr("app.services.snapback_alert_telegram.alert_user_id", lambda: "family-user")
    return out


def test_a_sim_alert_is_queued_for_the_sim_tenant(sent):
    wiring.alert("POSITION CLOSED", "NIFTY26NOV25000PE", "Exit 100", key="snapback:p1:EXIT",
                 uid=SIM_UID)
    assert [e["uid"] for e in sent] == [SIM_UID]


def test_a_live_alert_still_goes_to_the_live_alert_user(sent):
    wiring.alert("POSITION CLOSED", "NIFTY26NOV25000PE", "Exit 100", key="snapback:p1:EXIT")
    wiring.alert("POSITION CLOSED", "NIFTY26NOV25000PE", "Exit 100", key="snapback:p1:EXIT",
                 uid=OWNER)
    assert [e["uid"] for e in sent] == ["family-user", "family-user"]


def test_the_lifecycle_producers_without_a_uid_stay_live(sent):
    assert alerts.emit(dedupe_key="k", event_type="ENTRY FILLED", title="X", detail="d")
    assert [e["uid"] for e in sent] == ["family-user"]


def test_a_queued_sim_event_renders_with_the_simulation_prefix(sent):
    wiring.alert("EXIT FAILING", "NIFTY26NOV25000PE", "new entries halted", key="k",
                 severity="CRITICAL", uid=SIM_UID)
    (event,) = sent
    text = lf.render_lifecycle(strategy=event["strategy"], event_type=event["event_type"],
                               title=event["title"], detail_text=event["detail"],
                               severity=event["severity"], mode="LIVE", uid=event["uid"])
    assert text.startswith("🧪 SIMULATION") and "<i>SIMULATION" in text


def test_the_same_signal_alerts_both_tenants_without_swallowing_either(tmp_path, monkeypatch):
    from app.services import db
    from app.services.notifications import telegram_lifecycle as outbox

    path = str(tmp_path / "alerts.db")
    monkeypatch.setenv("STERLING_DB_PATH", path)
    monkeypatch.setattr(db, "_DB_PATH", path)
    monkeypatch.setattr(db, "_available", True)
    db.init()
    outbox.clear_for_tests()
    monkeypatch.setattr("app.services.snapback_alert_telegram.alert_user_id", lambda: OWNER)
    for uid in (SIM_UID, OWNER):
        wiring.alert("POSITION CLOSED", "NIFTY26NOV25000PE", "Exit 100", key="snapback:p1:EXIT",
                     uid=uid)
    now = outbox._now_ms() + 10
    assert [e.event_type for e in outbox.due(OWNER, now_ms=now)] == ["POSITION CLOSED"]
    assert [e.event_type for e in outbox.due(SIM_UID, now_ms=now)] == ["POSITION CLOSED"]
    outbox.clear_for_tests()


def test_a_virtual_position_entry_and_exit_alerts_are_queued_for_the_sim_tenant(env, sent):
    env.uid = SIM_UID
    env.broker._account_id = "SIM-TEST"
    env.rows = [swing_row(signal_id=_signal_id())]
    _cycle(env)
    pos = env.positions.open_positions(SIM_UID)[0]
    run(env.dispatcher.exit_manager.exit_position(env.broker, pos.position_id, "operator"))

    kinds = {e["event_type"]: e["uid"] for e in sent}
    assert kinds.get("ENTRY FILLED") == SIM_UID
    assert kinds.get("POSITION CLOSED") == SIM_UID
    assert "family-user" not in {e["uid"] for e in sent if e["strategy"] == "Snapback"
                                 and e["event_type"] in ("ENTRY FILLED", "POSITION CLOSED",
                                                         "SL ARMED")}


_SYSTEM_WIDE = {"CALENDAR EXPIRING"}      # about the exchange holiday list, not a tenant's position


def test_every_position_alert_call_site_names_its_tenant():
    root = pathlib.Path(wiring.__file__).parent
    missing = []
    for name in ("snapback_live_exits.py", "snapback_live_wiring.py", "snapback_dispatcher.py"):
        tree = ast.parse((root / name).read_text())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "alert"):
                title = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else ""
                if title not in _SYSTEM_WIDE and "uid" not in {k.arg for k in node.keywords}:
                    missing.append(f"{name}:{node.lineno}:{title}")
    assert not missing, missing
