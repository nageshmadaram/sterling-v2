"""The three production entry paths, driven the way production drives them.

Each ``drive_*`` function takes a booted :class:`~tests.e2e_live.machine.LiveMachine`,
configures the strategy the way an operator does (through the real HTTP config routes
where there are any), feeds the REAL entry path a synthetic but valid signal, and
returns an outcome record. Application modules are imported inside the functions so the
same function works after ``machine.restart()``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from tests.e2e_live.fake_kite import (
    MONTHLY_EXPIRY, NEXT_MONTHLY_EXPIRY, NIFTY_LOT, NIFTY_TOKEN, WEEKLY_EXPIRY,
    future_symbol, monthly_symbol, weekly_symbol,
)
from tests.e2e_live.machine import IST, NOW, UID


@dataclass
class Outcome:
    """What happened to one signal. ``first_refusal`` is the first thing that said no."""

    strategy: str
    ordered: bool = False
    protected: bool = False
    first_refusal: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)
    activity: List[str] = field(default_factory=list)

    def __str__(self) -> str:  # pragma: no cover - diagnostics
        return (f"{self.strategy}: ordered={self.ordered} protected={self.protected} "
                f"first_refusal={self.first_refusal!r} detail={self.detail}")


def http(machine, method: str, path: str, **kw):
    """One request against the real FastAPI app, as the operator's browser would send it."""
    from starlette.testclient import TestClient

    main = machine.m("main")
    client = TestClient(main.app, raise_server_exceptions=False)
    return client.request(method, f"/api/v1{path}", **kw)


# ---------------------------------------------------------------------------
# Supertrend
# ---------------------------------------------------------------------------

SUPERTREND_SYMBOL = monthly_symbol("NIFTY", MONTHLY_EXPIRY, 25000, "CE")


def supertrend_bar_ms() -> int:
    """The 09:15 IST 1H bar, the one that closed at 10:15 (one minute ago)."""
    return int(NOW.replace(hour=9, minute=15, second=0).timestamp() * 1000)


def configure_supertrend(machine, **overrides) -> Dict[str, Any]:
    """Operator flow: apply the production preset, then switch AUTO on."""
    out: Dict[str, Any] = {}
    resp = http(machine, "POST", "/kite/engine/config/production")
    out["preset"] = (resp.status_code, resp.text[:300])
    patch = {"auto_execute": True, "supertrend_auto_execute": True, "stop_mode": "both",
             "engine_enabled": True, **overrides}
    resp = http(machine, "PATCH", "/kite/engine/config", json=patch)
    out["patch"] = (resp.status_code, resp.text[:400])
    return out


def supertrend_row(*, direction: str = "long", ts_ms: Optional[int] = None,
                   symbol: str = SUPERTREND_SYMBOL, strike: float = 25000.0,
                   expiry: str = MONTHLY_EXPIRY.isoformat(), spot: float = 25000.0,
                   adx: float = 30.0, token: int = 0):
    from app.engines.sterling_kite_engine.schemas import AlignmentChip, EngineSignalRow, OptionLeg

    opt = "CE" if direction == "long" else "PE"
    trend = 1 if direction == "long" else -1
    return EngineSignalRow(
        underlying="NIFTY", token=NIFTY_TOKEN, exchange="NFO",
        regime="BULL" if direction == "long" else "BEAR",
        alignment=AlignmentChip(fast=trend, mid=trend, slow=trend),
        direction=direction, option_type=opt,
        legs=[OptionLeg(moneyness="ATM", option_type=opt, option_symbol=symbol, strike=strike,
                        expiry=expiry, lot_size=NIFTY_LOT, token=token)],
        spot=spot, underlying_spot=spot,
        stop_loss=spot - 150.0 if direction == "long" else spot + 150.0,
        score=85.0, timestamp_ms=ts_ms if ts_ms is not None else supertrend_bar_ms(),
        is_fresh=True, is_active=True, source="spot", adx=adx, atr_pct=60.0)


async def drive_supertrend(machine, *, ltp: float = 180.0, signal_ts_ms: Optional[int] = None,
                           deliver_fill: bool = True) -> Outcome:
    """A fresh 1H index-option signal through ``kite_engine.service`` auto-entry."""
    service = machine.m("app.services.kite_engine.service")
    state = machine.m("app.services.kite_engine.state")
    positions = machine.m("app.services.kite_engine.positions")
    ex = machine.exchange
    key = f"NFO:{SUPERTREND_SYMBOL}"
    ex.set_quote(key, ltp)
    ex.spot["NIFTY 50"] = 25000.0

    client = await machine.client()
    await machine.refresh_exposure()          # the 20s exposure refresher firing
    before = len(ex.entry_posts())
    row = supertrend_row(ts_ms=signal_ts_ms,
                         token=int(ex.instrument(SUPERTREND_SYMBOL, "NFO")["instrument_token"]))
    await service._make_place_cb(client, UID)(row, None)
    out = Outcome("supertrend")
    out.activity = [f"{e.kind}: {e.message}" for e in state.activity(UID)]
    out.ordered = len(ex.entry_posts()) > before
    blocked = [e for e in state.activity(UID) if e.kind in ("order_blocked", "order_failed")]
    if blocked and not out.ordered:
        out.first_refusal = blocked[0].message
    if out.ordered:
        if deliver_fill:
            await machine.postbacks()
        pos = positions.get(UID, SUPERTREND_SYMBOL)
        out.detail["position"] = None if pos is None else {
            "status": pos.status, "qty": pos.qty, "gtt_id": pos.gtt_id,
            "stop_premium": pos.stop_premium, "protection_pending": pos.protection_pending,
            "account_id": pos.account_id, "product": pos.product}
        out.protected = bool(pos and pos.gtt_id and pos.status == positions.OPEN
                             and not pos.protection_pending)
    return out


# ---------------------------------------------------------------------------
# Gamma Unwind
# ---------------------------------------------------------------------------

GAMMA_SYMBOL = weekly_symbol("NIFTY", WEEKLY_EXPIRY, 25000, "CE")


def configure_gamma(machine, **overrides) -> Dict[str, Any]:
    """Operator flow: shared master AUTO through the kite-engine route, Gamma's own AUTO
    through the Gamma config route."""
    out: Dict[str, Any] = {}
    resp = http(machine, "PATCH", "/kite/engine/config", json={"auto_execute": True})
    out["master_auto"] = (resp.status_code, resp.text[:200])
    values = {"enabled": True, "auto_execute": True, "execution_lots": 1,
              "stop_mode": "both", **overrides}
    resp = http(machine, "PUT", "/config/gamma-move", json=values)
    out["gamma"] = (resp.status_code, resp.text[:200])
    return out


def gamma_signal(machine, *, symbol: str = GAMMA_SYMBOL, entry: float = 120.0,
                 base_low: float = 98.0, dte: int = 1, at_ms: Optional[int] = None,
                 signal_id: str = "gamma-e1-nifty-25000ce-1"):
    from app.engines.gamma_move import GammaSignal, TIER_ENTRY
    from app.engines.gamma_move.models import GateContext, InstrumentRef, WatchContract

    instrument = machine.exchange.instrument(symbol, "NFO")
    inst = InstrumentRef(
        instrument_id=str(instrument["instrument_token"]), tradingsymbol=symbol,
        option_type="CE", strike=float(instrument["strike"]), expiry=str(instrument["expiry"]),
        lot_size=int(instrument["lot_size"]), tick_size=float(instrument["tick_size"]),
        exchange="NFO")
    contract = WatchContract(
        underlying="NIFTY", underlying_token=NIFTY_TOKEN, list_name="L1", instrument=inst,
        tag="HOI-1", oi=300_000, candidates_n=2, ref_price=25000.0, zone_edge=25020.0,
        atr_pct=0.8, build_oi=330_000.0, chain_ltp=entry, dte=dte)
    gates = GateContext(
        zone_gap_pct=0.1, zone_limit_pct=1.0, avg_vol_lots=40.0, zero_vol_bars=0,
        f1=True, f2=True, unwind=True, gate_reasons=(), gate_unknown=(),
        passes=("zone", "liquidity", "direction", "unwind"), quadrant=None)
    now_ms = int(datetime.now(IST).timestamp() * 1000)
    return GammaSignal(
        id=signal_id, at_ms=at_ms if at_ms is not None else now_ms, contract=contract, ltp=entry,
        oi_drop_pct=9.5, vol_ratio=1.8, strong=False, base_low=base_low, base_high=entry + 4.0,
        reason="E1 breakout", snapshot={}, gates=gates, tier=TIER_ENTRY)


async def drive_gamma(machine, *, entry: float = 120.0, signal_id: str = "gamma-e1-nifty-25000ce-1",
                      signal_at_ms: Optional[int] = None) -> Outcome:
    """An E1 ENTRY signal for an intraday option through ``gamma_move_runner``'s auto path."""
    runner = machine.m("app.services.gamma_move_runner")
    positions_store = machine.m("app.services.gamma_move_positions")
    ex = machine.exchange
    # Last print at the offer, so the LIMIT at the signal price is marketable.
    ex.set_quote(f"NFO:{GAMMA_SYMBOL}", entry, bid=entry - 0.5, ask=entry)
    await machine.client()
    await machine.refresh_exposure()
    before = len(ex.entry_posts())
    sig = gamma_signal(machine, entry=entry, signal_id=signal_id, at_ms=signal_at_ms)
    await runner._register_signals(UID, [sig])
    session = runner.session_for(UID)
    out = Outcome("gamma")
    out.activity = [f"{n['kind']}: {n['message']}" for n in session.notes]
    out.ordered = len(ex.entry_posts()) > before
    blocked = [n for n in session.notes if n["kind"] in ("blocked", "error", "warning")]
    if blocked and not out.ordered:
        out.first_refusal = blocked[0]["message"]
    pos = positions_store.get(UID, GAMMA_SYMBOL)
    if out.ordered:
        await machine.postbacks()
        pos = positions_store.get(UID, GAMMA_SYMBOL)
    out.detail["position"] = None if pos is None else {
        "status": pos.status, "quantity": pos.quantity, "gtt_id": pos.gtt_id,
        "stop": pos.stop, "gtt_pending": pos.gtt_pending, "account_id": pos.account_id,
        "hold": pos.hold}
    out.protected = bool(pos and pos.gtt_id and pos.is_open and not pos.gtt_pending)
    return out


# ---------------------------------------------------------------------------
# Snapback
# ---------------------------------------------------------------------------

SNAPBACK_OPTION = monthly_symbol("NIFTY", NEXT_MONTHLY_EXPIRY, 25000, "PE")
SNAPBACK_FUTURE = future_symbol("NIFTY", NEXT_MONTHLY_EXPIRY)
SNAPBACK_SIGNAL_SESSION = datetime(2026, 10, 9, 0, 0, tzinfo=IST)   # Friday's daily close
SNAPBACK_SIGNAL_ID = "snapback:fade_up:NIFTY:1791504000000"


def configure_snapback(machine, **overrides) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    resp = http(machine, "PATCH", "/kite/engine/config", json={"auto_execute": True})
    out["master_auto"] = (resp.status_code, resp.text[:120])
    values = {"enabled": True, "auto_execute": True, "trading_mode": "swing", **overrides}
    resp = http(machine, "PUT", "/config/snapback", json=values)
    out["snapback"] = (resp.status_code, resp.text[:300])
    assert resp.status_code == 200, f"the operator could not configure Snapback: {resp.text[:400]}"
    return out


def snapback_row(machine, *, ask: float = 300.0, signal_id: str = SNAPBACK_SIGNAL_ID) -> Dict[str, Any]:
    option = machine.exchange.instrument(SNAPBACK_OPTION, "NFO")
    return {
        "signal_id": signal_id, "strategy": "snapback", "side": "fade_up", "symbol": "NIFTY",
        "state": "armed", "reason": None, "origin": "live_quote", "direction": "BEARISH",
        "opt_type": "PE", "timestamp_ms": int(SNAPBACK_SIGNAL_SESSION.timestamp() * 1000),
        "spot": 25000.0, "mean_target": 24250.0, "distance_pct": 3.3, "stretch": 2.1,
        "strength": "STRONG", "underlying_token": NIFTY_TOKEN, "cash_exchange": "NSE",
        "cash_tradingsymbol": "NIFTY 50", "assumed_iv_pct": 22.0,
        "contract": {"symbol": SNAPBACK_OPTION, "exchange": "NFO", "strike": 25000.0,
                     "option_type": "PE", "expiry": NEXT_MONTHLY_EXPIRY.isoformat(), "dte": 43,
                     "lot_size": NIFTY_LOT, "token": int(option["instrument_token"]),
                     "delta": -0.7, "tick_size": 0.05},
        "premium": ask, "premium_is_modelled": False,
        "quote": {"premium": ask, "bid": ask - 1, "ask": ask, "blockers": []},
        "stop_premium": round(ask * 0.65, 2), "target_premium": round(ask * 1.4, 2),
        "lots": 1, "quantity": NIFTY_LOT, "hold_days": 15,
    }


async def drive_snapback(machine, *, ask: float = 300.0, signal_id: str = SNAPBACK_SIGNAL_ID) -> Outcome:
    """A hedged swing row on a fresh board snapshot through the production dispatcher cycle."""
    from app.services import snapback as snapback_service  # noqa: F401  (import-time wiring)

    dispatcher_mod = machine.m("app.services.snapback_dispatcher")
    snapback = machine.m("app.services.snapback")
    live_positions = machine.m("app.services.snapback_live_positions")
    plan_store_mod = machine.m("app.services.snapback_plan_store")
    ex = machine.exchange
    ex.set_quote(f"NFO:{SNAPBACK_OPTION}", ask - 0.5, bid=ask - 1.0, ask=ask)
    ex.set_quote(f"NFO:{SNAPBACK_FUTURE}", 25020.0, bid=25019.5, ask=25020.5, oi=9_000_000)
    ex.set_quote("NSE:NIFTY 50", 25000.0)

    await machine.client()
    await machine.refresh_exposure()
    st = snapback.status(UID)
    st.rows = [snapback_row(machine, ask=ask, signal_id=signal_id)]
    st.last_scan_ms = snapback.ist_now_ms()
    before = len(ex.order_posts)

    dispatcher = dispatcher_mod.get_dispatcher()
    cycle = await dispatcher.cycle(UID)
    out = Outcome("snapback")
    out.detail["cycle"] = {k: v for k, v in cycle.items() if k != "result"}
    posts = ex.order_posts[before:]
    out.ordered = any(b.get("transaction_type") == "BUY" and b.get("tradingsymbol") == SNAPBACK_OPTION
                      for b in posts)
    reasons = list(cycle.get("blockers") or []) + list((cycle.get("refusals") or {}).values())
    if reasons and not out.ordered:
        out.first_refusal = str(reasons[0])
    store = live_positions.default_store()
    pos = [p for p in store.open_positions(UID)]
    out.detail["positions"] = [
        {"symbol": p.option_symbol, "status": p.status, "gtt_id": p.gtt_id,
         "protection": p.protection_state, "hedge": p.hedge_symbol, "hedge_qty": p.hedge_qty,
         "hedge_entry_order": p.hedge_entry_order_id} for p in pos]
    out.protected = bool(pos and all(p.gtt_id and p.protection_state == live_positions.PROTECTION_ACTIVE
                                     for p in pos))
    plan = plan_store_mod.shared_store().get(plan_store_mod.__dict__.get("plan_id_for", lambda s: "")(signal_id)) \
        if False else None
    out.detail["plan"] = plan
    return out


# ---------------------------------------------------------------------------
# Supertrend through the real scan (candles -> regime -> strikes -> place_cb)
# ---------------------------------------------------------------------------

def _session_opens(last_open: datetime, n: int) -> List[datetime]:
    """The ``n`` NSE hourly bar opens (09:15 .. 15:15 on weekdays) ending at ``last_open``."""
    from datetime import timedelta

    out = [last_open]
    cur = last_open
    while len(out) < n:
        if cur.hour > 9 or (cur.hour == 9 and cur.minute > 15):
            cur = cur - timedelta(hours=1)
        else:
            cur = (cur - timedelta(days=1)).replace(hour=15, minute=15)
            while cur.weekday() >= 5:
                cur = cur - timedelta(days=1)
        out.append(cur)
    return list(reversed(out))


def serve_fresh_long_index_signal(machine) -> float:
    """Serve NIFTY 1H candles whose LAST CLOSED bar is a fresh SuperTrend long entry, plus the
    quotes the option chain needs. Returns the spot."""
    import numpy as np

    from app.engines.sterling_kite_engine.config import SterlingKiteEngineConfig
    from app.engines.sterling_kite_engine.regime import compute_regime, entry_transitions

    base = np.array(list(np.linspace(300, 150, 60)) + list(np.linspace(150, 600, 80)), float)
    px = 25000.0 * (1 + 0.04 * (base / 300.0 - 1))
    o = np.concatenate([[px[0]], px[:-1]])
    h = np.maximum(o, px) * 1.0004
    l = np.minimum(o, px) * 0.9996
    longs, _ = entry_transitions(compute_regime(o, h, l, px, SterlingKiteEngineConfig()))
    idx = int(np.where(longs)[0][0])
    o, h, l, c = o[: idx + 1], h[: idx + 1], l[: idx + 1], px[: idx + 1]
    k = 25000.0 / c[-1]
    o, h, l, c = o * k, h * k, l * k, c * k

    last_open = NOW.replace(hour=9, minute=15, second=0, microsecond=0)   # closed at 10:15
    rows = [[t.strftime("%Y-%m-%dT%H:%M:%S+0530"), float(o[i]), float(h[i]), float(l[i]),
             float(c[i]), 1000] for i, t in enumerate(_session_opens(last_open, len(c)))]
    forming = NOW.replace(hour=10, minute=15, second=0, microsecond=0)
    rows.append([forming.strftime("%Y-%m-%dT%H:%M:%S+0530"), float(c[-1]), float(c[-1]) + 2,
                 float(c[-1]) - 2, float(c[-1]), 10])
    ex = machine.exchange
    ex.candles[(NIFTY_TOKEN, "60minute")] = rows
    spot = float(c[-1])
    ex.spot["NIFTY 50"] = spot
    for inst in ex.instruments:
        if inst["name"] == "NIFTY" and inst["instrument_type"] in ("CE", "PE"):
            intrinsic = max(0.0, (spot - inst["strike"]) if inst["instrument_type"] == "CE"
                            else (inst["strike"] - spot))
            ex.set_quote(f"NFO:{inst['tradingsymbol']}", round(intrinsic + 90.0, 2))
    return spot


async def drive_supertrend_scan(machine) -> Outcome:
    """One production ``scan_user`` cycle over a served fresh long index signal."""
    service = machine.m("app.services.kite_engine.service")
    state = machine.m("app.services.kite_engine.state")
    positions = machine.m("app.services.kite_engine.positions")
    ex = machine.exchange
    serve_fresh_long_index_signal(machine)
    client = await machine.client()
    await machine.refresh_exposure()
    before = len(ex.entry_posts())
    await service.scan_user(client, UID)
    out = Outcome("supertrend-scan")
    out.activity = [f"{e.kind}: {e.message}" for e in state.activity(UID)]
    out.ordered = len(ex.entry_posts()) > before
    blocked = [e for e in state.activity(UID) if e.kind in ("order_blocked", "order_failed")]
    if blocked and not out.ordered:
        out.first_refusal = blocked[0].message
    if out.ordered:
        await machine.postbacks()
        symbol = ex.entry_posts()[-1]["tradingsymbol"]
        pos = positions.get(UID, symbol)
        out.detail["symbol"] = symbol
        out.detail["position"] = None if pos is None else {
            "status": pos.status, "qty": pos.qty, "gtt_id": pos.gtt_id, "expiry": pos.expiry}
        out.protected = bool(pos and pos.gtt_id and pos.status == positions.OPEN)
    return out


# ---------------------------------------------------------------------------
# Stop-outs and the operator's daily-loss limit
# ---------------------------------------------------------------------------

def operator_sets_daily_loss(machine, hard_halt_inr: float = -200_000.0,
                             soft_warn_inr: float = -150_000.0) -> None:
    """The operator's one required setting: the loss they accept in a day, through the API."""
    resp = http(machine, "PUT", "/kite/risk/daily-loss", json={
        "enabled": True, "soft_warn_inr": soft_warn_inr, "hard_halt_inr": hard_halt_inr})
    assert resp.status_code == 200, resp.text


async def stop_out(machine, tradingsymbol: str) -> None:
    """The broker's resting GTT stop for ``tradingsymbol`` fires and its LIMIT sell fills; the
    postback travels down the websocket the application opened."""
    ex = machine.exchange
    trigger = next(g for g in ex.active_gtts() if g["condition"]["tradingsymbol"] == tradingsymbol)
    ex.fire_gtt(trigger["id"], trigger["orders"][0]["price"])
    await machine.postbacks()


async def drive_snapback_manual(machine, *, ask: float = 300.0,
                                signal_id: str = SNAPBACK_SIGNAL_ID) -> Outcome:
    """The operator's click: publish the plan for a board row, arm that exact revision, and let
    the background execution run - through the route handlers, with AUTO off."""
    import asyncio

    plans = machine.m("app.api.v1.endpoints.snapback_plans")
    auth = machine.m("app.core.auth")
    snapback = machine.m("app.services.snapback")
    live_positions = machine.m("app.services.snapback_live_positions")
    dispatcher = machine.m("app.services.snapback_dispatcher").get_dispatcher()
    ex = machine.exchange
    ex.set_quote(f"NFO:{SNAPBACK_OPTION}", ask - 0.5, bid=ask - 1.0, ask=ask)
    ex.set_quote(f"NFO:{SNAPBACK_FUTURE}", 25020.0, bid=25019.5, ask=25020.5, oi=9_000_000)
    ex.set_quote("NSE:NIFTY 50", 25000.0)
    await machine.client()
    await machine.refresh_exposure()
    st = snapback.status(UID)
    st.rows = [snapback_row(machine, ask=ask, signal_id=signal_id)]
    st.last_scan_ms = snapback.ist_now_ms()
    await dispatcher.cycle(UID)                 # the loop's own pass: heartbeat, reconciliation
    user = auth.UserContext(user_id=UID, username="admin", role="admin")
    out = Outcome("snapback-manual")
    before = len(ex.order_posts)
    try:
        published = await plans.publish_plan(plans.PublishRequest(signal_id=signal_id), user=user)
        armed = await plans.arm_plan(
            published["plan_id"],
            plans.ArmRequest(expected_revision=published["revision"], idempotency_key="click-1",
                             mode="LIVE"),
            user=user)
        out.detail["armed"] = armed
        if plans._executions:
            await asyncio.gather(*list(plans._executions))
    except Exception as exc:  # an HTTPException carries the refusal in .detail
        detail = getattr(exc, "detail", None)
        out.first_refusal = str(detail if detail is not None else exc)
    posts = ex.order_posts[before:]
    out.ordered = any(b.get("transaction_type") == "BUY" and b.get("tradingsymbol") == SNAPBACK_OPTION
                      for b in posts)
    store = live_positions.default_store()
    pos = list(store.open_positions(UID))
    out.detail["positions"] = [{"symbol": p.option_symbol, "status": p.status, "gtt_id": p.gtt_id,
                                "protection": p.protection_state, "hedge_qty": p.hedge_qty} for p in pos]
    out.protected = bool(pos and all(p.gtt_id and p.protection_state == live_positions.PROTECTION_ACTIVE
                                     for p in pos))
    return out
