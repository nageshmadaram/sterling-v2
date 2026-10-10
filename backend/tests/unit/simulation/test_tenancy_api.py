"""``/api/v1/sim/*`` over real HTTP: status, per-strategy mode, reset and capital. Owner only; works from
either view; AUTO (real money) is refused from the Simulation view; no route touches live global state."""
from __future__ import annotations

import time

import pytest
from starlette.testclient import TestClient
from tenancy_support import OWNER, SIM_ACCOUNT_ID, SIM_UID, live_account, unprotected_position, wipe_tenants

import main
from app.core import lane_registry
from app.services import db, gamma_move, live_safety
from app.services import trade_journal as journal
from app.services.exchanges.kite import accounts as kite_accounts
from app.services.kite_engine import positions
from app.services.kite_engine import state as engine_state
from app.services.simulation import mode, tenant
from app.services.simulation.ledger import get_ledger

VIEW = {"X-Sterling-View": "simulation"}
URL = "/api/v1/sim"


@pytest.fixture(autouse=True)
def _clean():
    wipe_tenants()
    yield
    wipe_tenants()


@pytest.fixture
def client():
    return TestClient(main.app, raise_server_exceptions=False)


@pytest.fixture
def live():
    return live_account()


def _put(client, strategy, new_mode, headers=None):
    return client.put(f"{URL}/strategy/{strategy}/mode", json={"mode": new_mode}, headers=headers or {})


# ── status ───────────────────────────────────────────────────────────────────
def test_status_describes_the_virtual_account_and_the_three_strategies(client, live):
    body = client.get(f"{URL}/status").json()
    assert body["virtual"] is True and body["mode"] == "SIMULATION"
    assert (body["uid"], body["account_id"], body["owner"]) == (SIM_UID, SIM_ACCOUNT_ID, OWNER)
    assert body["capital"] == body["cash"] == body["equity"] == 1_000_000
    assert body["today_pnl"] == 0 and body["open_positions"] == 0
    assert {"realised_today", "unrealised", "charges_today", "pnl_total", "available"} <= set(body)
    assert set(body["strategies"]) == {"supertrend", "snapback", "gamma"}
    assert {s["mode"] for s in body["strategies"].values()} == {"MANUAL"}
    assert set(body["runtime"]) >= {"running", "last_pass_ms", "passes", "last_error", "session_open"}
    assert body["price_feed"] == {"available": True, "detail": ""}
    assert "no real orders" in body["message"]


def test_status_says_when_virtual_prices_cannot_flow(client):
    body = client.get(f"{URL}/status").json()                 # no broker account at all
    assert body["price_feed"]["available"] is False and "no prices" in body["price_feed"]["detail"]
    live_account(connected=False)
    assert "not logged in" in client.get(f"{URL}/status").json()["price_feed"]["detail"]


def test_status_creates_the_tenant_on_first_look(client, live):
    assert kite_accounts.sim_account_for(OWNER) is None
    client.get(f"{URL}/status")
    assert kite_accounts.sim_account_for(OWNER) is not None
    control = db.get_execution_control(uid=SIM_UID, account_id=SIM_ACCOUNT_ID)
    assert control["recovery_state"] == "CLEAN"


def test_the_same_owner_is_served_from_either_view(client, live):
    live_view = client.get(f"{URL}/status").json()
    sim_view = client.get(f"{URL}/status", headers=VIEW).json()
    assert (sim_view["owner"], sim_view["uid"]) == (live_view["owner"], live_view["uid"]) == (OWNER, SIM_UID)


# ── who may call ─────────────────────────────────────────────────────────────
ROUTES = [("GET", "/status", None), ("PUT", "/strategy/gamma/mode", {"mode": "SIMULATION"}),
          ("POST", "/reset", {"confirm": True}), ("PUT", "/capital", {"capital": 500000})]


@pytest.mark.parametrize("method,path,body", ROUTES, ids=[f"{m} {p}" for m, p, _ in ROUTES])
def test_a_non_admin_is_refused_everywhere(client, live, method, path, body):
    response = client.request(method, URL + path, json=body, headers={"X-User-Id": "intern"})
    assert response.status_code == 403
    assert _db_rows(SIM_UID) == []


@pytest.mark.parametrize("method,path,body", ROUTES, ids=[f"{m} {p}" for m, p, _ in ROUTES])
def test_an_admin_who_is_not_the_owner_is_refused_everywhere(client, live, monkeypatch, method, path, body):
    monkeypatch.setenv("STERLING_OWNER_UID", "someone-else")
    response = client.request(method, URL + path, json=body)
    assert response.status_code == 403 and response.json()["detail"]["code"] == "owner_only"
    assert _db_rows(SIM_UID) == []
    assert kite_accounts.sim_account_for("someone-else") is None


def test_every_sim_route_is_covered_by_the_owner_only_table_above():
    spec = main.app.openapi()["paths"]
    published = {(verb.upper(), path[len("/api/v1/sim"):]) for path, ops in spec.items()
                 if path.startswith("/api/v1/sim/") for verb in ops}
    assert published == {("GET", "/status"), ("PUT", "/strategy/{strategy}/mode"), ("POST", "/reset"),
                         ("PUT", "/capital")}


def test_the_owner_comes_from_the_request_never_the_body(client, live):
    response = client.put(f"{URL}/strategy/gamma/mode", json={"mode": "SIMULATION", "owner": "victim", "uid": "x"})
    assert response.status_code == 200 and response.json()["mode"] == "SIMULATION"
    assert kite_accounts.sim_account_for("victim") is None
    assert gamma_move.get_config(SIM_UID).auto_execute is True


def _db_rows(uid):
    return [k for k in (p.format(u=uid) for p in mode._CONFIG_KEYS) if db.get_config(k)]


# ── mode ─────────────────────────────────────────────────────────────────────
def test_put_mode_switches_a_strategy_and_status_reflects_it(client, live):
    response = _put(client, "gamma", "SIMULATION")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["strategy"], body["mode"], body["previous"], body["changed"]) == ("gamma", "SIMULATION", "MANUAL", True)
    assert body["strategies"]["gamma"]["mode"] == "SIMULATION" and body["strategies"]["supertrend"]["mode"] == "MANUAL"
    assert client.get(f"{URL}/status").json()["strategies"]["gamma"]["mode"] == "SIMULATION"
    assert gamma_move.get_config(SIM_UID).auto_execute is True and gamma_move.get_config(OWNER).auto_execute is False


def test_mode_names_are_case_insensitive(client, live):
    assert _put(client, "Gamma", "simulation").json()["mode"] == "SIMULATION"


def test_an_unknown_strategy_or_mode_is_a_400_and_changes_nothing(client, live):
    for strategy, new_mode, code in (("momentum", "SIMULATION", "invalid_strategy"), ("gamma", "PAPER", "invalid_mode")):
        response = _put(client, strategy, new_mode)
        assert response.status_code == 400 and response.json()["detail"]["code"] == code
    assert client.put(f"{URL}/strategy/gamma/mode", json={}).status_code == 422
    assert _db_rows(SIM_UID) == []


def test_auto_goes_through_the_live_readiness_gate_and_answers_like_it(client, live):
    unprotected_position()
    response = _put(client, "supertrend", "AUTO")
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "auto_execute_blocked" and any("NO stop" in r for r in detail["reasons"])
    assert engine_state.get_config(OWNER).auto_execute is False                  # nothing took effect


def test_auto_arms_live_when_the_book_is_clean_and_turns_the_virtual_side_off(client, live):
    assert _put(client, "gamma", "SIMULATION").status_code == 200
    body = _put(client, "gamma", "AUTO").json()
    assert body["mode"] == "AUTO" and body["live"]["armed"] and not body["simulation"]["armed"]


def test_the_simulation_view_cannot_arm_live_money(client, live):
    response = _put(client, "gamma", "AUTO", headers=VIEW)
    assert response.status_code == 403 and response.json()["detail"]["message"] == "Not available in Simulation view"
    assert engine_state.get_config(OWNER).auto_execute is False and gamma_move.get_config(OWNER).auto_execute is False
    assert _put(client, "gamma", "SIMULATION", headers=VIEW).json()["mode"] == "SIMULATION"
    assert _put(client, "gamma", "MANUAL", headers=VIEW).json()["mode"] == "MANUAL"


def test_no_mode_change_touches_the_global_kill_switch_or_safe_mode(client, live, monkeypatch):
    from app.services.safe_mode import SafeModeService  # noqa: F401 - the live state is read through lane_registry

    before = (lane_registry.LIVE_EXECUTION_ENABLED, live_safety.daily_loss_config(OWNER))
    for strategy in mode.STRATEGIES:
        for new_mode in ("SIMULATION", "MANUAL"):
            assert _put(client, strategy, new_mode, headers=VIEW).status_code == 200
    assert (lane_registry.LIVE_EXECUTION_ENABLED, live_safety.daily_loss_config(OWNER)) == before


# ── reset ────────────────────────────────────────────────────────────────────
def test_reset_needs_an_explicit_confirm(client, live):
    _put(client, "gamma", "SIMULATION")
    get_ledger().adjust_cash(OWNER, -500)
    for body in ({}, {"confirm": False}, {"confirm": "yes"}, {"confirm": 1}, {"confirm": None}):
        response = client.post(f"{URL}/reset", json=body)
        assert response.status_code == 422, (body, response.text)
    assert get_ledger().account(OWNER)["cash"] == 999_500                       # nothing wiped
    assert gamma_move.get_config(SIM_UID).auto_execute is True


def test_reset_wipes_the_simulation_and_reports_status(client, live):
    _put(client, "gamma", "SIMULATION")
    get_ledger().adjust_cash(OWNER, -500)
    positions.register(positions.OpenPosition(
        uid=SIM_UID, symbol="NIFTY24JUN24000CE", exchange="NFO", token=777, qty=75, entry_premium=120.0,
        stop_premium=90.0, order_id="V1", status=positions.OPEN, direction="long",
        red_count_ms=int(time.time() * 1000)))
    journal.record_entry(uid=SIM_UID, strategy="gamma", symbol="NIFTY24JUN24000CE", key="k", status="OPEN",
                         entry_price=120.0, qty=75)
    response = client.post(f"{URL}/reset", json={"confirm": True}, headers=VIEW)     # works from the simulation view too
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True and body["failed"] == {}
    assert body["status"]["cash"] == body["status"]["capital"] == 1_000_000
    assert {s["mode"] for s in body["status"]["strategies"].values()} == {"MANUAL"}
    assert positions.open_positions(SIM_UID) == [] and journal.list_trades([SIM_UID])["total"] == 0


def test_reset_leaves_live_state_alone(client, live):
    _put(client, "gamma", "AUTO")
    gamma_move.set_config({"execution_lots": 5}, uid=OWNER)
    client.post(f"{URL}/reset", json={"confirm": True})
    assert gamma_move.get_config(OWNER).execution_lots == 5 and gamma_move.get_config(OWNER).auto_execute is True
    assert engine_state.get_config(OWNER).auto_execute is True


def test_reset_can_set_new_capital(client, live):
    body = client.post(f"{URL}/reset", json={"confirm": True, "capital": 250000}).json()
    assert body["capital"] == body["status"]["capital"] == body["status"]["cash"] == 250_000


# ── capital ──────────────────────────────────────────────────────────────────
def test_put_capital_changes_capital_cash_and_the_virtual_loss_limits(client, live):
    body = client.put(f"{URL}/capital", json={"capital": 400000}).json()
    assert body["capital"] == body["cash"] == 400_000
    cfg = live_safety.daily_loss_config(SIM_UID)
    assert cfg.hard_halt_inr == -round(400_000 * tenant.DAILY_LOSS_HARD_PCT / 100.0, 2)
    assert live_safety.daily_loss_config(OWNER) == live_safety.daily_loss_config("someone-without-an-override")


@pytest.mark.parametrize("capital", [0, -10, "lots", None, 1e12])
def test_a_bad_capital_is_a_422_and_changes_nothing(client, live, capital):
    client.get(f"{URL}/status")
    assert client.put(f"{URL}/capital", json={"capital": capital}).status_code == 422
    assert get_ledger().account(OWNER)["capital"] == 1_000_000


# ── routers stay separate ────────────────────────────────────────────────────
def test_the_replay_router_is_still_there_and_separate(client, live):
    replay = client.get("/api/v1/simulation/status")
    assert replay.status_code == 200 and "virtual" not in replay.json()


# ── failure modes the UI must be able to tell apart ──────────────────────────
def test_a_tenant_that_cannot_be_prepared_is_a_503_and_nothing_is_armed(client, live, monkeypatch):
    def boom(owner):
        raise RuntimeError("ledger unreadable")

    monkeypatch.setattr(tenant, "boot_tenant", boom)
    response = _put(client, "gamma", "SIMULATION")
    assert response.status_code == 503 and response.json()["detail"]["code"] == "tenant_unavailable"
    assert gamma_move.get_config(SIM_UID).auto_execute is False and gamma_move.get_config(OWNER).auto_execute is False


def test_a_write_that_fails_part_way_is_a_500_and_leaves_the_strategy_off(client, live, monkeypatch):
    assert _put(client, "gamma", "AUTO").status_code == 200
    real = mode._arm
    monkeypatch.setattr(mode, "_arm", lambda uid, strategy: (_ for _ in ()).throw(RuntimeError("disk full"))
                        if uid == SIM_UID else real(uid, strategy))
    response = _put(client, "gamma", "SIMULATION")
    assert response.status_code == 500 and response.json()["detail"]["code"] == "mode_change_failed"
    status = client.get(f"{URL}/status").json()["strategies"]["gamma"]
    assert status["mode"] == "MANUAL" and not status["live"]["armed"] and not status["simulation"]["armed"]


def test_polling_status_does_not_rebuild_the_tenant_each_time(client, live, monkeypatch):
    client.get(f"{URL}/status")
    built = []
    real = tenant.boot_tenant
    monkeypatch.setattr(tenant, "boot_tenant", lambda owner: built.append(owner) or real(owner))
    for _ in range(3):
        assert client.get(f"{URL}/status").status_code == 200
    assert built == []


def test_a_misconfigured_owner_is_a_503_not_a_crash(client, live, monkeypatch):
    monkeypatch.setenv("STERLING_OWNER_UID", "sim:default")
    response = client.get(f"{URL}/status")
    assert response.status_code == 503 and response.json()["detail"]["code"] == "owner_misconfigured"
