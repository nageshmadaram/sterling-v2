"""``/api/v1/journal/*``: read-only, scoped to the caller's tenant, the owner alone may combine."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tj_support import journal_db, ms  # noqa: F401  (journal_db is a fixture)

OWNER_LIVE = "default"
OWNER_SIM = "sim:default"


def _user(user_id, role):
    from app.core.auth import UserContext
    return UserContext(user_id=user_id, username=user_id, role=role)


@pytest.fixture
def api(journal_db):
    """A client whose caller can be switched per request: ``api.as_(user_id, role)``."""
    from app.api.v1.endpoints import journal as endpoints
    from app.core.auth import get_current_user

    app = FastAPI()
    app.include_router(endpoints.router, prefix="/api/v1")
    state = {"user": _user(OWNER_LIVE, "admin")}
    app.dependency_overrides[get_current_user] = lambda: state["user"]
    client = TestClient(app)

    def as_(user_id, role="admin"):
        state["user"] = _user(user_id, role)
        return client

    client.as_ = as_
    client.journal = journal_db
    return client


def _closed(tj, uid, strategy, key, pnl, closed_at, reason="TARGET", symbol=None, underlying="NIFTY"):
    """A closed trade whose gross P&L is exactly ``pnl`` (75 units from 100.00), no charges."""
    tid = tj.record_entry(uid=uid, strategy=strategy, symbol=symbol or f"{underlying}SYM{key}", key=key,
                          status="OPEN", qty=75, lots=1, lot_size=75, entry_price=100.0,
                          entry_ts_ms=closed_at - 3_600_000, underlying=underlying, option_type="CE",
                          strike=25000.0, initial_sl=90.0)
    tj.record_exit(trade_id=tid, exit_price=100.0 + pnl / 75.0, qty=75, ts_ms=closed_at, reason=reason, charges=0.0)
    return tid


@pytest.fixture
def book(api):
    """LIVE: +1000 / -400 / +600 (12 Oct, supertrend, supertrend, gamma) and one open;
    SIMULATION: -900 (gamma, 13 Oct) and +300 (snapback, 13 Oct)."""
    tj = api.journal
    ids = {
        "L1": _closed(tj, OWNER_LIVE, "supertrend", "L1", 1000.0, ms(10, 0, 12), "TARGET"),
        "L2": _closed(tj, OWNER_LIVE, "supertrend", "L2", -400.0, ms(11, 0, 12), "SL", underlying="BANKNIFTY"),
        "L3": _closed(tj, OWNER_LIVE, "gamma", "L3", 600.0, ms(12, 0, 12), "T2"),
        "S1": _closed(tj, OWNER_SIM, "gamma", "S1", -900.0, ms(10, 0, 13), "SL"),
        "S2": _closed(tj, OWNER_SIM, "snapback", "S2", 300.0, ms(11, 0, 13), "RUNNER_TRAIL_STOP"),
    }
    ids["LO"] = tj.record_entry(uid=OWNER_LIVE, strategy="supertrend", symbol="NIFTYOPEN", key="LO", status="OPEN",
                                qty=75, entry_price=80.0, entry_ts_ms=ms(14, 0, 13), underlying="NIFTY")
    tj.record_event(trade_id=ids["L1"], event_type="FILLED", ts_ms=ms(9, 1, 12), price=100.0, qty=75, event_key="f")
    tj.record_event(trade_id=ids["L1"], event_type="SIGNAL", ts_ms=ms(9, 0, 12), detail="Signal", event_key="s")
    tj.record_skip(uid=OWNER_LIVE, strategy="supertrend", code="daily_loss_halt", signal_id="x1", ts_ms=ms(10, 0, 12))
    tj.record_skip(uid=OWNER_SIM, strategy="snapback", code="quote_not_executable", signal_id="x2", ts_ms=ms(10, 0, 13))
    return ids


def test_the_default_view_is_the_callers_own_tenant(api, book):
    live = api.as_(OWNER_LIVE).get("/api/v1/journal/trades").json()
    assert live["mode"] == "LIVE" and live["total"] == 4
    assert {t["mode"] for t in live["items"]} == {"LIVE"}
    sim = api.as_(OWNER_SIM).get("/api/v1/journal/trades").json()
    assert sim["mode"] == "SIMULATION" and sim["total"] == 2 and {t["mode"] for t in sim["items"]} == {"SIMULATION"}


def test_explicit_modes_and_the_owner_only_combined_view(api, book):
    owner = api.as_(OWNER_LIVE, "admin")
    assert owner.get("/api/v1/journal/trades?mode=simulation").json()["total"] == 2
    both = owner.get("/api/v1/journal/trades?mode=all").json()
    assert both["mode"] == "ALL" and both["total"] == 6 and {t["mode"] for t in both["items"]} == {"LIVE", "SIMULATION"}
    # the owner acting inside the simulation view may still read live and the combination
    in_sim = api.as_(OWNER_SIM, "admin")
    assert in_sim.get("/api/v1/journal/trades?mode=live").json()["total"] == 4
    assert in_sim.get("/api/v1/journal/trades?mode=all").json()["total"] == 6
    assert owner.get("/api/v1/journal/trades?mode=nonsense").status_code == 400


def test_a_non_owner_sees_only_their_own_live_trades_and_cannot_reach_simulation_or_all(api, book):
    tj = api.journal
    _closed(tj, "alice", "gamma", "A1", 250.0, ms(10, 0, 12))
    alice = api.as_("alice", "user")
    page = alice.get("/api/v1/journal/trades").json()
    assert page["total"] == 1 and page["items"][0]["uid"] == "alice"
    assert alice.get("/api/v1/journal/trades?mode=simulation").status_code == 403
    assert alice.get("/api/v1/journal/trades?mode=all").status_code == 403
    assert alice.get("/api/v1/journal/summary?mode=all").status_code == 403
    assert alice.get("/api/v1/journal/skips?mode=all").status_code == 403
    # the owner's trade id is not reachable from alice's tenant: 404, not 403 (no existence leak)
    assert alice.get(f"/api/v1/journal/trades/{book['L1']}").status_code == 404
    assert alice.get("/api/v1/journal/summary").json()["summary"]["net_pnl"] == 250.0


def test_the_live_view_never_reads_simulation_rows_and_vice_versa(api, book):
    live_summary = api.as_(OWNER_LIVE).get("/api/v1/journal/summary").json()["summary"]
    sim_summary = api.as_(OWNER_SIM).get("/api/v1/journal/summary").json()["summary"]
    assert live_summary["net_pnl"] == 1200.0 and sim_summary["net_pnl"] == -600.0
    assert api.as_(OWNER_LIVE).get(f"/api/v1/journal/trades/{book['S1']}").status_code == 404
    assert api.as_(OWNER_SIM).get(f"/api/v1/journal/trades/{book['L1']}").status_code == 404
    assert api.as_(OWNER_LIVE, "admin").get(f"/api/v1/journal/trades/{book['S1']}?mode=simulation").status_code == 200


def test_trade_filters_sort_and_pagination(api, book):
    c = api.as_(OWNER_LIVE)
    syms = lambda q: [t["symbol"] for t in c.get(f"/api/v1/journal/trades?{q}").json()["items"]]  # noqa: E731
    assert syms("strategy=gamma") == ["NIFTYSYML3"]
    assert syms("outcome=win&sort=pnl_desc") == ["NIFTYSYML1", "NIFTYSYML3"]
    assert syms("outcome=loss") == ["BANKNIFTYSYML2"]
    assert syms("outcome=open") == ["NIFTYOPEN"]
    assert syms("symbol=banknifty") == ["BANKNIFTYSYML2"]
    assert syms("date_from=2026-10-13&date_to=2026-10-13") == ["NIFTYOPEN"]
    assert syms("exit_reason=SL") == ["BANKNIFTYSYML2"]
    assert syms("sort=oldest&limit=2") == ["NIFTYSYML1", "BANKNIFTYSYML2"]
    page = c.get("/api/v1/journal/trades?sort=oldest&limit=2&offset=2").json()
    assert page["total"] == 4 and page["limit"] == 2 and page["offset"] == 2 and len(page["items"]) == 2
    assert c.get("/api/v1/journal/trades?outcome=maybe").status_code == 400
    assert c.get("/api/v1/journal/trades?sort=mood").status_code == 400
    assert c.get("/api/v1/journal/trades?strategy=nope").status_code == 400
    assert c.get("/api/v1/journal/trades?date_from=12-10-2026").status_code == 400
    assert c.get("/api/v1/journal/trades?limit=0").status_code == 422


def test_trade_detail_carries_the_story_and_the_ordered_timeline(api, book):
    out = api.as_(OWNER_LIVE).get(f"/api/v1/journal/trades/{book['L1']}").json()
    t = out["trade"]
    assert out["mode"] == "LIVE" and t["strategy_label"] == "Supertrend" and t["exit_reason"] == "TARGET"
    assert t["exit_reason_label"] == "Target reached" and t["net_pnl"] == 1000.0
    assert t["narrative"].startswith("Bought 1 lot NIFTY 25000 CE at 100.00")
    assert [e["type"] for e in t["timeline"]] == ["SIGNAL", "FILLED", "EXIT_FILLED"]      # by time, not insertion
    assert api.as_(OWNER_LIVE).get("/api/v1/journal/trades/ST-doesnotexist").status_code == 404


def test_summary_figures_and_the_per_mode_split(api, book):
    out = api.as_(OWNER_LIVE, "admin").get("/api/v1/journal/summary?mode=all").json()
    s = out["summary"]
    assert out["mode"] == "ALL" and s["trades"] == 5 and s["open"] == 1
    assert (s["wins"], s["losses"]) == (3, 2) and s["win_rate_pct"] == 60.0
    assert s["net_pnl"] == 600.0                      # 1000 - 400 + 600 - 900 + 300
    assert s["profit_factor"] == 1.46                 # 1900 / 1300
    assert s["expectancy"] == 120.0 and s["avg_win"] == 633.33 and s["avg_loss"] == -650.0
    assert s["max_drawdown"] == 900.0                 # peak 1200 after L3, then S1 -900
    assert set(out["by_mode"]) == {"LIVE", "SIMULATION"}
    assert out["by_mode"]["LIVE"]["net_pnl"] == 1200.0 and out["by_mode"]["SIMULATION"]["net_pnl"] == -600.0
    filtered = api.as_(OWNER_LIVE, "admin").get("/api/v1/journal/summary?mode=all&strategy=gamma").json()["summary"]
    assert filtered["trades"] == 2 and filtered["net_pnl"] == -300.0
    ranged = api.as_(OWNER_LIVE, "admin").get("/api/v1/journal/summary?mode=all&date_from=2026-10-13").json()["summary"]
    assert ranged["trades"] == 2 and ranged["net_pnl"] == -600.0


def test_by_strategy_exit_reasons_equity_and_calendar(api, book):
    c = api.as_(OWNER_LIVE, "admin")
    table = {g["strategy"]: g for g in c.get("/api/v1/journal/by-strategy?mode=all").json()["strategies"]}
    assert table["supertrend"]["net_pnl"] == 600.0 and table["gamma"]["net_pnl"] == -300.0
    assert table["snapback"]["net_pnl"] == 300.0 and table["supertrend"]["rank"] == 1
    reasons = {r["reason"]: r for r in c.get("/api/v1/journal/exit-reasons?mode=all").json()["reasons"]}
    assert reasons["SL"]["count"] == 2 and reasons["SL"]["net_pnl"] == -1300.0
    assert reasons["TARGET"]["count"] == 2 and reasons["TSL"]["count"] == 1      # T2 -> TARGET, RUNNER_TRAIL_STOP -> TSL
    eq = c.get("/api/v1/journal/equity?mode=all").json()
    assert [p["cum_pnl"] for p in eq["by_trade"]] == [1000.0, 600.0, 1200.0, 300.0, 600.0]
    assert [(d["day"], d["cum_pnl"]) for d in eq["by_day"]] == [("2026-10-12", 1200.0), ("2026-10-13", 600.0)]
    assert [p["cum_pnl"] for p in eq["by_mode"]["SIMULATION"]["by_trade"]] == [-900.0, -600.0]
    cal = c.get("/api/v1/journal/calendar?mode=all").json()["days"]
    assert [(d["day"], d["net_pnl"]) for d in cal] == [("2026-10-12", 1200.0), ("2026-10-13", -600.0)]


def test_skips_are_scoped_filtered_and_paged(api, book):
    live = api.as_(OWNER_LIVE).get("/api/v1/journal/skips").json()
    assert live["mode"] == "LIVE" and live["total"] == 1 and live["items"][0]["code"] == "daily_loss_halt"
    assert live["items"][0]["reason"].startswith("The daily loss limit is reached")
    both = api.as_(OWNER_LIVE, "admin").get("/api/v1/journal/skips?mode=all").json()
    assert both["total"] == 2 and {i["mode"] for i in both["items"]} == {"LIVE", "SIMULATION"}
    assert {c["code"] for c in both["by_code"]} == {"daily_loss_halt", "quote_not_executable"}
    only = api.as_(OWNER_LIVE, "admin").get("/api/v1/journal/skips?mode=all&strategy=snapback").json()
    assert [i["code"] for i in only["items"]] == ["quote_not_executable"]
    assert api.as_(OWNER_LIVE, "admin").get("/api/v1/journal/skips?mode=all&limit=1").json()["items"].__len__() == 1


def test_an_empty_journal_answers_with_zeros_not_errors(api):
    c = api.as_(OWNER_LIVE)
    assert c.get("/api/v1/journal/trades").json()["total"] == 0
    s = c.get("/api/v1/journal/summary").json()["summary"]
    assert s["trades"] == 0 and s["net_pnl"] == 0.0 and s["win_rate_pct"] is None
    assert [g["trades"] for g in c.get("/api/v1/journal/by-strategy").json()["strategies"]] == [0, 0, 0]
    assert c.get("/api/v1/journal/exit-reasons").json()["reasons"] == []
    assert c.get("/api/v1/journal/equity").json()["by_trade"] == []
    assert c.get("/api/v1/journal/calendar").json()["days"] == []
    assert c.get("/api/v1/journal/skips").json()["total"] == 0


def test_scope_endpoint_tells_the_ui_what_it_may_ask_for(api):
    assert api.as_(OWNER_LIVE, "admin").get("/api/v1/journal/scope").json()["can_view_all"] is True
    sim = api.as_(OWNER_SIM, "admin").get("/api/v1/journal/scope").json()
    assert sim["default_mode"] == "SIMULATION" and "all" in sim["modes"]
    user = api.as_("alice", "user").get("/api/v1/journal/scope").json()
    assert user["modes"] == ["live"] and user["can_view_all"] is False


def test_the_journal_api_is_read_only(api):
    from app.api.v1.endpoints import journal as endpoints
    methods = {m for route in endpoints.router.routes for m in route.methods}
    assert methods <= {"GET", "HEAD"}, methods
    for verb in ("post", "put", "patch", "delete"):
        assert getattr(api.as_(OWNER_LIVE), verb)("/api/v1/journal/trades").status_code == 405


def test_the_router_is_mounted_in_the_application():
    import main
    paths = set(main.app.openapi()["paths"])
    assert {"/api/v1/journal/trades", "/api/v1/journal/summary", "/api/v1/journal/skips",
            "/api/v1/journal/trades/{trade_id}"} <= paths
