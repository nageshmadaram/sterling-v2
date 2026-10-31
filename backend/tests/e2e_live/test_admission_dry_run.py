"""End-to-end admission dry run: a valid signal -> a broker order, on a FRESH install.

One live account, a clean database, no operator environment beyond the account itself,
the Snapback evidence runner NOT running (its preflight fails exactly as on a machine with
no experiment configured). Every test boots the application through ``main.lifespan`` and
drives a REAL production entry path with a synthetic signal. The broker is an offline
Kite REST backend behind the production ``KiteClient``; see ``tests/e2e_live/machine.py``.

A test fails with the FIRST refusal the signal met, in the refusal's own words, so a wiring
gap reads as "<strategy> was refused: <code/reason>" rather than as an assertion on state.
"""
from __future__ import annotations

import pytest

from tests.e2e_live import scenarios as S
from tests.e2e_live.machine import ACCOUNT_API_KEY, ACCOUNT_API_SECRET, UID


async def _fresh_live_machine(machine, *, bound: bool = True):
    """Day-two boot: the account was added and logged in yesterday, the process restarts.

    ``bound`` sets the documented account binding (STERLING_FAMILY_USER_ID /
    STERLING_FAMILY_ACCOUNT_ID, as ``.env.example`` asks and as this machine's ``.env`` has it).
    With it set, Snapback readiness also needs a fresh clean reconciliation of that account.
    """
    machine.fresh_process()
    machine.provision_account()
    if bound:
        machine.bind_family_account()
    await machine.restart()
    await machine.run_live_runtime_once()
    return machine


def _explain(outcome: "S.Outcome") -> str:
    return f"{outcome}\n" + "\n".join(f"  {line}" for line in outcome.activity[-8:])


@pytest.mark.asyncio
async def test_fresh_process_reconstructs_datetime_routes_with_real_schema_types(machine):
    """A restart under the synthetic market clock must not poison Pydantic.

    Python's real datetime is required at *model construction* even though
    scenario/runtime time remains frozen. This regression is offline only.
    """
    from freezegun.api import real_datetime

    machine.fresh_process()
    machine.provision_account()
    await machine.restart()

    route = machine.m("app.api.v1.endpoints.opening_volume_leaders")
    field = route.OpeningVolumeBarRequest.model_fields["timestamp"]
    assert field.annotation is real_datetime
    validated = route.OpeningVolumeBarRequest.model_validate({
        "timestamp": "2026-10-12T10:16:00+05:30",
        "open": 100.0, "high": 101.0,
        "low": 99.0, "close": 100.0, "volume": 5.0,
    })
    assert validated.timestamp.year == 2026


# ---------------------------------------------------------------------------------- boot

@pytest.mark.asyncio
async def test_configured_live_account_without_kite_session_stays_up_and_serves_login(machine):
    """A configured account must never create the impossible "login required to log in" state."""
    machine.fresh_process()
    db = machine.m("app.services.db")
    accounts = machine.m("app.services.exchanges.kite.accounts")
    models = machine.m("app.services.exchanges.kite.models")
    assert db.init()
    accounts.bootstrap()
    account = accounts.add(
        UID,
        models.KiteAccountCreate(
            label="Zerodha",
            api_key=ACCOUNT_API_KEY,
            api_secret=ACCOUNT_API_SECRET,
        ),
    )
    machine.account_id = account.id

    # Boot a new process with credentials persisted but deliberately no access token.
    await machine.restart()
    await machine.run_live_runtime_once()

    live_runtime = machine.m("app.services.live_runtime")
    status = live_runtime.live_status()
    recovery = status["recovery"]["accounts"]
    assert recovery and recovery[0]["state"] == live_runtime.WAITING_FOR_SESSION, recovery
    assert status["admission"]["may_open_new_exposure"] is False

    # The backend is still alive and the exact route used by the UI can start login.
    from starlette.testclient import TestClient

    http = TestClient(machine.m("main").app, raise_server_exceptions=False)
    login = http.get("/api/v1/kite/login-url")
    assert login.status_code == 200, login.text
    payload = login.json()
    assert payload["state"]
    assert payload["login_url"].startswith("https://kite.zerodha.com/connect/login")


@pytest.mark.asyncio
async def test_fresh_machine_boots_into_an_open_admission(machine):
    await _fresh_live_machine(machine)

    live_runtime = machine.m("app.services.live_runtime")
    status = live_runtime.live_status()
    assert status["safe_mode"]["state"] == "NORMAL", status["safe_mode"]
    assert status["recovery"]["overall"] == "CLEAN", status["recovery"]
    assert status["exposure"]["fresh"] and status["exposure"]["readable"], status["exposure"]
    verdict = machine.admission()
    assert verdict["allowed"], verdict

    # The retired strategies' loops exist (maintenance-only) and so do the three live ones.
    names = machine.task_names()
    assert names.count("auto_scan_loop") >= 5, names       # kite engine, gamma, snapback board,
    assert "snapback-live-dispatcher" in names, names       # adaptive edge, intraday
    assert "live-recovery" in names and "live-exposure" in names, names


@pytest.mark.asyncio
async def test_snapback_evidence_runner_is_not_running_and_does_not_gate_live_entries(machine):
    """The machine this suite models cannot run the prospective evidence runner."""
    await _fresh_live_machine(machine)

    preflight = machine.m("app.services.snapback_preflight").run_preflight()
    assert not preflight.may_start_runner, "the evidence runner must be unable to start here"
    health = machine.m("app.services.snapback_health")
    assert "prospective_runner_unavailable" in health.get_prospective_health(UID)["unresolved_errors"]

    # A dispatcher cycle is what records the live heartbeat and (with the family account bound)
    # produces the reconciliation readiness reads; after one, nothing about the dead evidence
    # runner may be reported as a blocker for live Snapback.
    S.configure_snapback(machine)
    dispatcher = machine.m("app.services.snapback_dispatcher").get_dispatcher()
    await dispatcher.cycle(UID)
    readiness = machine.m("app.services.snapback_readiness")
    assert readiness.gate_blockers(UID) == [], readiness.readiness_state(UID)


# ------------------------------------------------------------------------- the three paths

@pytest.mark.asyncio
async def test_supertrend_signal_reaches_the_broker_and_is_protected(machine):
    await _fresh_live_machine(machine)
    S.configure_supertrend(machine)
    outcome = await S.drive_supertrend(machine)

    assert outcome.ordered, f"Supertrend was refused: {_explain(outcome)}"
    assert outcome.protected, f"Supertrend entry is not protected: {_explain(outcome)}"
    gtt = machine.exchange.gtt_posts
    assert len(gtt) == 1
    # A priced LIMIT sell leg under the trigger, NRML (Kite refuses MIS and unpriced GTTs).
    assert '"order_type": "LIMIT"' in gtt[0]["orders"] and '"product": "NRML"' in gtt[0]["orders"]


@pytest.mark.asyncio
async def test_gamma_entry_reaches_the_broker_and_is_protected(machine):
    await _fresh_live_machine(machine)
    S.configure_gamma(machine)
    outcome = await S.drive_gamma(machine)

    assert outcome.ordered, f"Gamma Unwind was refused: {_explain(outcome)}"
    assert outcome.protected, f"Gamma entry is not protected: {_explain(outcome)}"
    assert len(machine.exchange.gtt_posts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", [True, False], ids=["family-account-bound", "no-binding"])
async def test_snapback_swing_entry_reaches_the_broker_with_stop_and_hedge(machine, bound):
    await _fresh_live_machine(machine, bound=bound)
    S.configure_snapback(machine)
    outcome = await S.drive_snapback(machine)

    assert outcome.ordered, f"Snapback was refused: {_explain(outcome)}"
    assert outcome.protected, f"Snapback entry is not protected: {_explain(outcome)}"
    positions = outcome.detail["positions"]
    assert positions and positions[0]["status"] == "OPEN", positions
    assert positions[0]["hedge_qty"] > 0 and positions[0]["hedge_entry_order"], positions
    symbols = [(b["tradingsymbol"], b["transaction_type"]) for b in machine.exchange.order_posts]
    assert (S.SNAPBACK_OPTION, "BUY") in symbols and (S.SNAPBACK_FUTURE, "BUY") in symbols, symbols


# ------------------------------------------------------------- concurrency across strategies

@pytest.mark.asyncio
async def test_a_second_strategy_is_admitted_while_the_first_holds_its_own_gtt_stop(machine):
    """Sterling's own protective GTTs and its own positions are not 'external automation'."""
    await _fresh_live_machine(machine)
    S.configure_supertrend(machine)
    S.configure_gamma(machine)
    S.configure_snapback(machine)

    first = await S.drive_supertrend(machine)
    assert first.ordered and first.protected, _explain(first)
    await machine.refresh_exposure()
    assert machine.admission(strategy_id="gamma_move")["allowed"], machine.admission(strategy_id="gamma_move")

    second = await S.drive_gamma(machine)
    assert second.ordered and second.protected, f"Gamma was refused behind SuperTrend's stop: {_explain(second)}"
    await machine.refresh_exposure()
    assert machine.admission(strategy_id="snapback")["allowed"], machine.admission(strategy_id="snapback")

    third = await S.drive_snapback(machine)
    assert third.ordered and third.protected, f"Snapback was refused behind two stops: {_explain(third)}"

    await machine.refresh_exposure()
    observation = machine.m("app.services.external_positions").last_observation()
    assert observation["count"] == 0 and observation["automation"] == [], observation
    assert len(observation["owned_automation"]) == 3, observation["owned_automation"]
    assert machine.admission()["allowed"], machine.admission()

    # A minute on, the periodic recovery pass and the exposure refresher run again over three
    # open, stopped positions: neither may turn the account back into 'blocked'.
    lr = machine.m("app.services.live_runtime")
    await lr.recovery_loop(initial_delay=0, sleep=__import__("tests.e2e_live.machine", fromlist=["x"]).instant_sleep,
                           max_cycles=2)
    await machine.refresh_exposure()
    status = lr.live_status()
    assert status["admission"]["may_open_new_exposure"], status["admission"]
    assert status["recovery"]["overall"] == "CLEAN", status["recovery"]


# ------------------------------------------------------------------------------ kill switch

@pytest.mark.asyncio
async def test_kill_switch_stops_every_entry_but_not_protection_or_exits(machine, monkeypatch):
    """STERLING_LIVE_EXECUTION=0 is read when the process starts. It stops new entries only."""
    await _fresh_live_machine(machine)
    S.configure_supertrend(machine)
    # A SuperTrend entry is out and filled, and the process dies before the fill is processed.
    opened = await S.drive_supertrend(machine, deliver_fill=False)
    assert opened.ordered, _explain(opened)
    machine.exchange.postbacks.clear()            # the websocket died with the process

    monkeypatch.setenv("STERLING_LIVE_EXECUTION", "0")
    await machine.restart()
    await machine.run_live_runtime_once()
    lr = machine.m("app.services.live_runtime")
    assert lr.live_execution_state()["kill_switch"] is True

    # Protection of the position that already exists is NOT an entry: the stop goes out.
    positions = machine.m("app.services.kite_engine.positions")
    position = positions.get(UID, S.SUPERTREND_SYMBOL)
    assert position is not None and position.gtt_id and position.status == positions.OPEN, position
    assert len(machine.exchange.active_gtts()) == 1

    # No NEW entry, by any strategy, reaches the broker.
    S.configure_gamma(machine)
    S.configure_snapback(machine)
    entries_before = len(machine.exchange.entry_posts())
    gamma = await S.drive_gamma(machine)
    snapback = await S.drive_snapback(machine)
    second = await S.drive_supertrend(machine, signal_ts_ms=S.supertrend_bar_ms() + 1)
    assert not (gamma.ordered or snapback.ordered or second.ordered), (gamma, snapback, second)
    assert len(machine.exchange.entry_posts()) == entries_before

    # An exit is still sent: the premium breaches the stop on a tick.
    exits_before = len(machine.exchange.exit_posts())
    tick = {"instrument_token": position.token, "last_price": position.stop_premium - 2.0}
    ticker = machine.ticker()
    assert ticker is not None, "the order/tick stream must be up to deliver ticks"
    await ticker._on_ticks([tick])
    assert len(machine.exchange.exit_posts()) == exits_before + 1, machine.exchange.order_posts[-3:]


# --------------------------------------------------------------------- restart idempotency

@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["supertrend", "gamma", "snapback"])
async def test_a_restart_never_sends_a_second_order_for_the_same_signal(machine, strategy):
    await _fresh_live_machine(machine)
    configure = {"supertrend": S.configure_supertrend, "gamma": S.configure_gamma,
                 "snapback": S.configure_snapback}[strategy]
    drive = {"supertrend": S.drive_supertrend, "gamma": S.drive_gamma,
             "snapback": S.drive_snapback}[strategy]
    configure(machine)
    first = await drive(machine)
    assert first.ordered and first.protected, _explain(first)
    orders_before = list(machine.exchange.order_posts)
    gtts_before = len(machine.exchange.gtt_posts)

    # The process dies and a new one starts on the same database; the same signal is seen again
    # (the board still shows it / the scanner re-emits it / the user clicks the same row).
    await machine.restart()
    await machine.run_live_runtime_once()
    again = await drive(machine)

    assert machine.exchange.order_posts == orders_before, (
        f"{strategy}: a restart re-sent an order for the same signal: "
        f"{machine.exchange.order_posts[len(orders_before):]}\n{_explain(again)}")
    assert len(machine.exchange.gtt_posts) == gtts_before, "a restart armed a second stop"


# ------------------------------------------------------------------- first-ever boot

@pytest.mark.asyncio
async def test_first_ever_boot_then_login_opens_every_strategy(machine):
    """The real first run: an empty machine boots with NO account, then the operator adds the
    account and logs in through the UI's routes. Nothing is restarted afterwards."""
    machine.fresh_process()
    await machine.boot()
    await machine.run_live_runtime_once()          # no account yet: nothing to observe or recover
    lr = machine.m("app.services.live_runtime")
    assert lr.live_status()["live_accounts"] == 0

    machine.operator_adds_account_and_logs_in()
    # The loops run on their own cadence (watchdog 30s, exposure 20s, recovery <=60s): one more
    # pass of each is what the next minute does.
    await machine.run_live_runtime_once()
    status = lr.live_status()
    assert status["admission"]["may_open_new_exposure"], status["admission"]
    assert status["recovery"]["overall"] == "CLEAN", status["recovery"]
    assert machine.ticker() is not None, "the order stream must be up once a session exists"

    S.configure_supertrend(machine)
    S.configure_gamma(machine)
    S.configure_snapback(machine)
    for drive in (S.drive_supertrend, S.drive_gamma, S.drive_snapback):
        outcome = await drive(machine)
        assert outcome.ordered and outcome.protected, _explain(outcome)


# ----------------------------------------------------------- the real SuperTrend scan path

@pytest.mark.asyncio
async def test_a_scanned_fresh_signal_buys_a_contract_the_square_off_will_not_sell(machine):
    """Candles in, order out: the production scan (instruments -> universe -> 1H candles ->
    regime -> option chain -> place_cb). The day before a weekly expiry the nearest-money leg
    is the weekly; the entry must trade the next expiry, not one it sells at the next pass."""
    await _fresh_live_machine(machine)
    S.configure_supertrend(machine)
    outcome = await S.drive_supertrend_scan(machine)

    assert outcome.ordered, f"the scanned signal was refused: {_explain(outcome)}"
    assert outcome.protected, f"the scanned entry is not protected: {_explain(outcome)}"
    assert outcome.detail["symbol"].startswith("NIFTY26OCT"), outcome.detail   # monthly, not the 13th
    assert outcome.detail["position"]["expiry"] == "2026-10-27", outcome.detail


# ------------------------------------------------------------------------ stop-out round trips

@pytest.mark.asyncio
async def test_supertrend_stop_out_leaves_the_machine_open_for_the_next_signal(machine):
    await _fresh_live_machine(machine)
    S.operator_sets_daily_loss(machine)
    S.configure_supertrend(machine)
    first = await S.drive_supertrend(machine)
    assert first.ordered and first.protected, _explain(first)

    await S.stop_out(machine, S.SUPERTREND_SYMBOL)
    positions = machine.m("app.services.kite_engine.positions")
    assert positions.get(UID, S.SUPERTREND_SYMBOL).status == positions.CLOSED
    await machine.refresh_exposure()
    assert machine.admission()["allowed"], machine.admission()

    second = await S.drive_supertrend(machine, signal_ts_ms=S.supertrend_bar_ms() + 1)
    assert second.ordered and second.protected, _explain(second)


@pytest.mark.asyncio
async def test_gamma_stop_out_leaves_the_machine_open_for_the_next_signal(machine):
    await _fresh_live_machine(machine)
    S.operator_sets_daily_loss(machine)
    S.configure_gamma(machine)
    first = await S.drive_gamma(machine)
    assert first.ordered and first.protected, _explain(first)

    await S.stop_out(machine, S.GAMMA_SYMBOL)
    reconciled = await machine.m("app.services.gamma_move_runner").reconcile(UID)
    assert reconciled["vanished"] == [], "the broker stop firing is Gamma's own exit, not a vanished position"
    await machine.refresh_exposure()
    assert machine.admission(strategy_id="gamma_move")["allowed"], machine.admission()

    second = await S.drive_gamma(machine, signal_id="gamma-e1-nifty-25000ce-2")
    assert second.ordered and second.protected, _explain(second)


@pytest.mark.asyncio
async def test_snapback_stop_out_closes_the_hedge_and_leaves_the_machine_open(machine):
    await _fresh_live_machine(machine)
    S.operator_sets_daily_loss(machine)
    S.configure_snapback(machine)
    first = await S.drive_snapback(machine)
    assert first.ordered and first.protected, _explain(first)

    machine.exchange.set_quote(f"NFO:{S.SNAPBACK_OPTION}", 190.0, bid=189.5, ask=190.5)
    await S.stop_out(machine, S.SNAPBACK_OPTION)
    dispatcher = machine.m("app.services.snapback_dispatcher").get_dispatcher()
    for _ in range(3):
        await dispatcher.cycle(UID)
    assert machine.exchange.net_quantity(S.SNAPBACK_OPTION) == 0
    assert machine.exchange.net_quantity(S.SNAPBACK_FUTURE) == 0, "the hedge was left open after the stop-out"
    await machine.refresh_exposure()
    assert machine.admission(strategy_id="snapback")["allowed"], machine.admission(strategy_id="snapback")

    second = await S.drive_snapback(machine, signal_id="snapback:fade_up:NIFTY:1791504000001")
    assert second.ordered and second.protected, _explain(second)


# ------------------------------------------------- the operator-config requirement, made visible

@pytest.mark.asyncio
async def test_the_shipped_daily_loss_default_is_flagged_up_front_and_halts_every_strategy(machine):
    """The ONE setting the operator must make on a fresh install: the account daily-loss halt.

    It ships at -1,500 INR. A SuperTrend position sized at the preset's 1% risk on a 10 lakh
    account loses ~6,500 when its stop fires, which halts new entries for the day in EVERY
    strategy. That refusal is legitimate (the control is not weakened); what must not happen is
    learning the number from the halt, so readiness and the live status name it beforehand.
    """
    await _fresh_live_machine(machine)
    S.configure_supertrend(machine)

    client = await machine.client()
    service = machine.m("app.services.kite_engine.service")
    readiness = await service.check_live_readiness(client, UID)
    limit = readiness["checks"]["account_daily_loss"]
    assert limit["status"] == "warning" and limit["data"]["hard_halt_inr"] == -1500.0, limit
    assert "PUT /api/v1/kite/risk/daily-loss" in limit["detail"], limit["detail"]
    lr = machine.m("app.services.live_runtime")
    assert lr.live_status()["daily_loss"][0]["is_account_override"] is False

    first = await S.drive_supertrend(machine)
    assert first.ordered and first.protected, _explain(first)
    await S.stop_out(machine, S.SUPERTREND_SYMBOL)
    await machine.refresh_exposure()

    verdict = machine.admission()
    assert not verdict["allowed"] and verdict["code"] == "daily_loss_halt", verdict
    for strategy_id in ("gamma_move", "snapback", "sterling-kite"):
        assert machine.admission(strategy_id=strategy_id)["code"] == "daily_loss_halt"
    assert "daily_loss_halt" in lr.live_status()["summary"]

    # The operator sets the loss they accept; entries are open again immediately.
    S.operator_sets_daily_loss(machine)
    assert machine.admission()["allowed"], machine.admission()


@pytest.mark.asyncio
async def test_a_bound_family_account_stays_reconciled_while_other_strategies_hold_positions(machine):
    """With the account binding set, SuperTrend's and Gamma's open positions and resting stops are
    Sterling's own to the reconciliation Snapback's gate reads; they must not make it 'dirty'."""
    await _fresh_live_machine(machine, bound=True)
    S.operator_sets_daily_loss(machine)
    S.configure_supertrend(machine)
    S.configure_gamma(machine)
    S.configure_snapback(machine)
    reconciliation = machine.m("app.services.snapback_reconciliation")

    for drive in (S.drive_supertrend, S.drive_gamma):
        outcome = await drive(machine)
        assert outcome.ordered and outcome.protected, _explain(outcome)
        snapshot = await reconciliation.reconcile_family_account()
        assert snapshot.clean, [(m.code, m.instrument) for m in snapshot.mismatches]

    third = await S.drive_snapback(machine)
    assert third.ordered and third.protected, f"Snapback was refused behind two open positions: {_explain(third)}"


@pytest.mark.asyncio
async def test_a_manual_snapback_click_reaches_the_broker_with_auto_off(machine):
    """Publish and arm through the route handlers, as the UI's Buy does, with AUTO off. Readiness
    for a manual arm needs the same bound-account reconciliation AUTO does."""
    await _fresh_live_machine(machine, bound=True)
    S.configure_snapback(machine, auto_execute=False)
    outcome = await S.drive_snapback_manual(machine)

    assert outcome.ordered, f"the manual Snapback arm was refused: {_explain(outcome)} {outcome.first_refusal}"
    assert outcome.protected, f"the manual Snapback entry is not protected: {outcome}"
    assert outcome.detail["positions"][0]["hedge_qty"] > 0
