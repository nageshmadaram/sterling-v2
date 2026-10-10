"""Turns a fresh, actionable Snapback board row into a durable live plan.

The plan is the thing the rest of the execution path trusts: the live executor
sizes the hedge from it, the canonical service journals its signal id, and the
exit manager reads its frozen exit rules. So this module is where every question
that must be answered BEFORE money moves is answered, from fresh broker evidence
rather than from the cached row:

* is the signal still current (swing: the next session only; scalp: a few bars)?
* is the contract still quotable inside the strategy's own spread/premium/open
  interest limits, and at what price may we pay?
* how many lots, what stop, what target, what trail, which rules?
* on the swing lane, which index future hedges it, how many lots, and does the
  broker confirm the margin?
* can the account fund every leg, and may this lane open anything right now?

Nothing here places an order and nothing here edits a strategy parameter: the
frozen configuration is read, never written.

Identity is derived from the signal id, so a restart, a rescan or a double click
yields the same plan id, and the second attempt finds the first instead of
creating a competing authorization.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))

LANE_SWING = "swing"
LANE_SCALP = "scalp"
#: engine ``trading_mode`` -> the lane that trades it. ``intraday`` has no rules
#: of its own (it shares the scalp knobs) and is not a lane.
_LANE_OF_MODE = {"swing": LANE_SWING, "scalp": LANE_SCALP}
_HORIZON_OF_LANE = {LANE_SWING: "swing", LANE_SCALP: "scalping"}

#: The swing signal is a daily close; it is traded in the NEXT session only, and
#: only inside this window (the opening minutes have no usable book).
SWING_ENTRY_START = dtime(9, 20)
SWING_ENTRY_CUTOFF = dtime(10, 30)

#: How long a published plan may wait to be armed and executed.
PLAN_TTL_SWING_S = 300
PLAN_TTL_SCALP_S = 90

#: A marketable limit: the entry may pay up to this far above the ask. Wide
#: enough to get filled, tight enough that a book that ran away refuses instead.
ENTRY_BUFFER_ENV = "STERLING_SNAPBACK_ENTRY_BUFFER_PCT"
DEFAULT_ENTRY_BUFFER_PCT = 0.5
HEDGE_BUFFER_PCT = 0.10

#: The hedge is an index future. It always trades on NFO (SENSEX options trade on
#: BFO but there is no SENSEX future in the hedge contract rules).
HEDGE_UNDERLYING = "NIFTY"
HEDGE_EXCHANGE = "NFO"

#: Idempotency / generation identity for the canonical order journal. Constant
#: per contract version so a rebuilt plan can never open a second journal key.
def generation_id() -> str:
    from app.engines.snapback import CONTRACT_VERSION

    return f"snapback-live-{CONTRACT_VERSION}"


# --------------------------------------------------------------------- helpers

def plan_id_for(signal_id: str, uid: str = "") -> str:
    """One plan per signal and tenant, forever. Derived, never random.

    The signal id is tenant independent and ``plan_id`` is a global key, so the virtual tenant
    gets its own digest; a real uid (or none) keeps the legacy one, so live plans never move.
    """
    from app.services.simulation.identity import is_sim_uid

    seed = f"{uid}\0{signal_id}" if is_sim_uid(uid) else str(signal_id)
    return f"SBP-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:20]}"


def lane_for(cfg: Any) -> Optional[str]:
    return _LANE_OF_MODE.get(str(getattr(cfg, "trading_mode", "") or ""))


def round_up(price: float, tick: float = 0.05) -> float:
    tick = tick if tick and tick > 0 else 0.05
    return round(math.ceil(float(price) / tick - 1e-9) * tick, 2)


def round_down(price: float, tick: float = 0.05) -> float:
    tick = tick if tick and tick > 0 else 0.05
    return round(math.floor(float(price) / tick + 1e-9) * tick, 2)


def _finite(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _ist(now: Optional[datetime]) -> datetime:
    value = now or datetime.now(_IST)
    return value.astimezone(_IST) if value.tzinfo else value.replace(tzinfo=_IST)


def _entry_buffer_pct() -> float:
    raw = (os.environ.get(ENTRY_BUFFER_ENV) or "").strip()
    try:
        value = float(raw) if raw else DEFAULT_ENTRY_BUFFER_PCT
    except ValueError:
        return DEFAULT_ENTRY_BUFFER_PCT
    return value if 0 <= value <= 5 else DEFAULT_ENTRY_BUFFER_PCT


# ------------------------------------------------------------------- freshness

def freshness_reasons(row: Dict[str, Any], cfg: Any, now: Optional[datetime] = None,
                      *, manual: bool = False) -> List[str]:
    """Why this row may not be traded as a fresh signal. Empty means it may.

    Cheap and synchronous: it only reads the row. The expensive evidence (a live
    quote, margins, the hedge) is gathered afterwards, so a stale row costs no
    broker calls.
    """
    now = _ist(now)
    reasons: List[str] = []
    lane = lane_for(cfg)
    if lane is None:
        return ["lane_not_supported"]
    if str(row.get("strategy") or "snapback") != "snapback":
        reasons.append("not_a_snapback_row")
    if row.get("state") != "armed":
        reasons.append("row_not_armed")
    if row.get("stale"):
        reasons.append("row_stale")
    if row.get("origin") != "live_quote" or row.get("premium_is_modelled"):
        reasons.append("no_live_quote")
    contract = row.get("contract") or {}
    if not contract.get("symbol") or not contract.get("exchange"):
        reasons.append("contract_unresolved")

    if lane == LANE_SWING:
        ts_ms = _finite(row.get("timestamp_ms"))
        if ts_ms is None:
            reasons.append("signal_time_unknown")
        else:
            session = datetime.fromtimestamp(ts_ms / 1000.0, tz=_IST).date()
            try:
                from app.services.navigator.calendar import next_trading_day

                expected = next_trading_day(session)
            except Exception:  # noqa: BLE001 - an unknown calendar is not "today"
                reasons.append("calendar_unavailable")
            else:
                if now.date() != expected:
                    reasons.append("signal_not_for_today")
            if not manual and not (SWING_ENTRY_START <= now.time() <= SWING_ENTRY_CUTOFF):
                reasons.append("outside_swing_entry_window")
    else:
        close_ms = _finite(row.get("signal_close_ms")) or _finite(row.get("timestamp_ms"))
        timeframe = max(1, int(getattr(cfg, "scalp_timeframe_minutes", 1) or 1))
        ttl_s = timeframe * 60 + 60
        if close_ms is None:
            reasons.append("signal_time_unknown")
        else:
            age_s = now.timestamp() - close_ms / 1000.0
            if age_s > ttl_s or age_s < -5:
                reasons.append("signal_stale")
        minute = now.hour * 60 + now.minute
        if not (int(cfg.scalp_entry_start_minute) <= minute <= int(cfg.scalp_entry_end_minute)):
            # The configured scalp entry window is a rule of the lane, not a
            # convenience: an operator click does not widen it.
            reasons.append("outside_scalp_entry_window")
    return reasons


# ---------------------------------------------------------------------- context

@dataclass
class PlanContext:
    uid: str
    account_id: str
    client: Any
    cfg: Any
    now: datetime
    available_capital: Optional[float]
    #: ``exchange -> instrument rows``; cached by the caller.
    instruments: Callable[[str], Awaitable[list]]
    #: Snapback's own open positions on this account (for capacity/exposure).
    open_positions: List[Any] = field(default_factory=list)
    #: ``async (row, client) -> BetaSnapshot``; injectable for tests.
    beta_for: Optional[Callable[[Dict[str, Any], Any], Awaitable[Any]]] = None
    runtime_sha: str = ""
    #: Skip the clock-window part of the freshness check (manual operator arm).
    manual: bool = False
    #: Plans already authorised but not finished (ARMED / RECONCILING): their hedges
    #: are part of the book this plan would join.
    inflight_plans: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class PlanOutcome:
    ok: bool
    plan: Optional[Dict[str, Any]] = None
    reasons: List[str] = field(default_factory=list)
    detail: str = ""

    @property
    def reason(self) -> str:
        return self.reasons[0] if self.reasons else ""


def _refuse(*reasons: str, detail: str = "") -> PlanOutcome:
    return PlanOutcome(ok=False, reasons=[r for r in reasons if r], detail=detail)


# ------------------------------------------------------------------ the builder

async def build_plan(row: Dict[str, Any], ctx: PlanContext) -> PlanOutcome:
    """Everything needed to publish one plan, or the reason there is none."""
    cfg = ctx.cfg
    now = _ist(ctx.now)
    lane = lane_for(cfg)
    if lane is None:
        return _refuse("lane_not_supported")

    stale = freshness_reasons(row, cfg, now, manual=ctx.manual)
    if stale:
        return _refuse(*stale)

    unsupported = unsupported_config_reasons(cfg)
    if unsupported:
        return _refuse(*unsupported)

    signal_id = str(row.get("signal_id") or "")
    if not signal_id:
        return _refuse("signal_id_missing")

    contract = dict(row["contract"])
    option_exchange = str(contract["exchange"]).upper()
    option_symbol = str(contract["symbol"])
    lot_size = int(contract.get("lot_size") or 0)
    option_token = int(contract.get("token") or 0)
    if lot_size <= 0 or option_token <= 0:
        return _refuse("contract_metadata_incomplete")
    option_type = str(row.get("opt_type") or contract.get("option_type") or "").upper()
    if option_type not in ("CE", "PE"):
        return _refuse("option_type_unknown")
    side = str(row.get("side") or "")
    if side not in ("fade_up", "fade_down"):
        return _refuse("signal_side_unknown")
    tick = float(contract.get("tick_size") or 0.05)

    # ── fresh option quote ──────────────────────────────────────────────────
    from app.services import snapback as _runtime

    quote = await _runtime._quote_for(ctx.client, contract, cfg)
    if not quote or quote.get("blockers"):
        blockers = list((quote or {}).get("blockers") or ["quote_unavailable"])
        return _refuse("quote_not_executable", detail="; ".join(blockers))
    ask = _finite(quote.get("ask"))
    bid = _finite(quote.get("bid"))
    if not ask or ask <= 0 or not bid or bid <= 0:
        return _refuse("quote_not_executable", detail="two-sided book unavailable")

    # The price we may pay. A marketable limit: the ask plus a small buffer.
    limit_price = round_up(ask * (1.0 + _entry_buffer_pct() / 100.0), tick)
    if limit_price < ask:
        limit_price = round_up(ask, tick)

    # ── lane-specific levels and size ───────────────────────────────────────
    if lane == LANE_SWING:
        levels = _swing_levels(cfg, ask=ask, limit_price=limit_price, lot_size=lot_size,
                               tick=tick, row=row)
    else:
        levels = _scalp_levels(cfg, ask=ask, bid=bid, limit_price=limit_price,
                               lot_size=lot_size, tick=tick)
    if not levels["ok"]:
        return _refuse(*levels["reasons"], detail=levels.get("detail", ""))

    lots = int(levels["lots"])
    qty = lots * lot_size

    # ── hedge (swing only, when the strategy defines one) ───────────────────
    hedge: Dict[str, Any] = {}
    hedge_margin: Optional[float] = 0.0
    if lane == LANE_SWING and str(getattr(cfg, "hedge_mode", "none")) == "index_futures":
        hedged = await _size_hedge(row, ctx, contract=contract, option_type=option_type,
                                   option_qty=qty)
        if not hedged["ok"]:
            return _refuse(*hedged["reasons"], detail=hedged.get("detail", ""))
        hedge = hedged["hedge"]
        hedge_margin = hedged["margin"]

    # ── funding and admission ───────────────────────────────────────────────
    premium_cash = round(limit_price * qty, 2)
    fee_reserve = round(40.0 * (2 if hedge else 1) * 2 + premium_cash * 0.001, 2)
    capacity = _capacity(row, ctx, lane=lane, contract=contract, option_type=option_type,
                         option_qty=qty, premium_cash=premium_cash, hedge_margin=hedge_margin,
                         fee_reserve=fee_reserve, now=now)
    if not capacity["ok"]:
        return _refuse(*capacity["reasons"], detail=capacity.get("detail", ""))

    ttl = PLAN_TTL_SWING_S if lane == LANE_SWING else PLAN_TTL_SCALP_S
    expires_at = (now + timedelta(seconds=ttl)).astimezone(timezone.utc).isoformat()

    from app.engines.snapback.manifest import compute_config_hash
    from app.engines.snapback.policy import EXECUTION_POLICY

    plan: Dict[str, Any] = dict(
        plan_id=plan_id_for(signal_id, ctx.uid),
        opportunity_id=signal_id,
        account_id=ctx.account_id,
        owner_uid=ctx.uid,
        option_symbol=option_symbol,
        option_quantity=qty,
        futures_symbol=str(hedge.get("symbol") or ""),
        target_futures_quantity=int(hedge.get("quantity") or 0),
        max_option_price=limit_price,
        hedge_price_limit=float(hedge.get("limit_price") or 0.0),
        risk_amount=float(levels["risk_amount"]),
        cash_required=premium_cash,
        margin_required=float(hedge_margin or 0.0),
        policy_snapshot_hash=generation_id(),
        runtime_build_sha=ctx.runtime_sha,
        mode="LIVE",
        expires_at=expires_at,
        # ── extras carried for the executor and the exit manager ──
        option_exchange=option_exchange,
        option_instrument_token=option_token,
        option_type=option_type,
        signal_side=side,
        underlying=str(row.get("symbol") or ""),
        lane=lane,
        horizon_mode=_HORIZON_OF_LANE[lane],
        trading_mode=str(cfg.trading_mode),
        lot_size=lot_size,
        lots=lots,
        option_expiry=str(contract.get("expiry") or ""),
        tick_size=tick,
        product="NRML",
        order_type="LIMIT",
        limit_price=limit_price,
        entry_quote={"bid": bid, "ask": ask, "ltp": quote.get("ltp"),
                     "oi": quote.get("oi"), "spread_pct": quote.get("spread_pct"),
                     "quote_ts_ms": quote.get("quote_ts_ms")},
        stop_premium=float(levels["stop_premium"]),
        target_premium=float(levels.get("target_premium") or 0.0),
        rules=levels["rules"],
        signal_ts_ms=int(row.get("timestamp_ms") or 0),
        signal_close_ms=int(row.get("signal_close_ms") or 0),
        strength=str(row.get("strength") or ""),
        config_hash=compute_config_hash(cfg),
        policy_hash=EXECUTION_POLICY.policy_hash(),
        strategy_version="snapback_core_v1",
        available_capital=ctx.available_capital,
        required_capital=capacity.get("required"),
        built_at=now.isoformat(),
    )
    if hedge:
        plan.update(
            futures_exchange=HEDGE_EXCHANGE,
            futures_instrument_token=int(hedge.get("token") or 0),
            futures_lot_size=int(hedge.get("lot_size") or 0),
            futures_expiry=str(hedge.get("expiry") or ""),
            hedge_side=str(hedge.get("side") or ""),
            hedge_order_type="LIMIT",
            hedge_beta=hedge.get("beta"),
            hedge_delta=hedge.get("delta"),
            hedge_error_pct=hedge.get("error_pct"),
        )
    return PlanOutcome(ok=True, plan=plan)


def unsupported_config_reasons(cfg: Any) -> List[str]:
    """Configurations the live path does not implement. Refused, never approximated."""
    out: List[str] = []
    if lane_for(cfg) is None:
        out.append("lane_not_supported")
    if float(getattr(cfg, "short_leg_delta", 0.0) or 0.0) > 0:
        out.append("spreads_not_supported_live")
    if str(getattr(cfg, "trading_mode", "")) == "swing":
        if str(getattr(cfg, "exit_mode", "horizon")) != "horizon":
            out.append("exit_mode_not_supported_live")
        if float(getattr(cfg, "premium_trail_pct", 0.0) or 0.0) > 0:
            out.append("premium_trail_not_supported_live")
        if int(getattr(cfg, "exit_on_regime_flip", 0) or 0) > 0:
            out.append("regime_flip_exit_not_supported_live")
    if str(getattr(cfg, "stop_mode", "both")) == "monitor":
        # A live position needs a broker-side stop. A monitor-only stop dies with
        # the process.
        out.append("broker_stop_required")
    return out


# ------------------------------------------------------------- lane arithmetic

def _swing_levels(cfg: Any, *, ask: float, limit_price: float, lot_size: int, tick: float,
                  row: Dict[str, Any]) -> Dict[str, Any]:
    from app.engines.snapback.contracts import Pick, lots_for

    if str(getattr(cfg, "sizing_mode", "LOTS")) == "LOTS":
        lots = max(1, min(int(cfg.lots), int(cfg.max_lots)))
    else:
        contract = row.get("contract") or {}
        pick = Pick(underlying=str(row.get("symbol") or ""),
                    symbol=str(contract.get("symbol") or ""),
                    option_type=str(row.get("opt_type") or ""),
                    strike=float(contract.get("strike") or 0.0), lot_size=lot_size,
                    exchange=str(contract.get("exchange") or ""),
                    dte=contract.get("dte"), expiry=contract.get("expiry"),
                    premium=float(ask), delta=0.0, moneyness="", modelled=False)
        lots = int(lots_for(pick, cfg))
    if lots < 1:
        return {"ok": False, "reasons": ["premium_budget_below_one_lot"]}
    stop_pct = float(cfg.premium_stop_pct)
    stop = max(tick, round_down(ask * (1.0 - stop_pct / 100.0), tick))
    qty = lots * lot_size
    risk = round((limit_price - stop) * qty, 2)
    rules = {
        "lane": LANE_SWING,
        "premium_stop_pct": stop_pct,
        "hold_days": int(cfg.hold_days),
        "runner_mult": float(cfg.runner_mult),
        "runner_trail_pct": float(cfg.runner_trail_pct),
        "expiry_buffer_calendar_days": _expiry_buffer_days(),
        "exit_mode": str(cfg.exit_mode),
    }
    return {"ok": True, "lots": lots, "stop_premium": stop,
            "target_premium": float(row.get("target_premium") or 0.0),
            "risk_amount": risk, "rules": rules}


def _expiry_buffer_days() -> int:
    from app.engines.snapback.policy import RUNNER_EXPIRY_BUFFER_CALENDAR_DAYS

    return int(RUNNER_EXPIRY_BUFFER_CALENDAR_DAYS)


def _scalp_levels(cfg: Any, *, ask: float, bid: float, limit_price: float, lot_size: int,
                  tick: float) -> Dict[str, Any]:
    from app.engines.snapback.intraday import make_plan

    spread = max(0.0, ask - bid)
    plan = make_plan(float(ask), int(lot_size), cfg, spread_points=spread)
    if not plan.get("accepted"):
        reasons = list(plan.get("rejection_reasons") or ["scalp_plan_rejected"])
        return {"ok": False, "reasons": ["scalp_plan_rejected"], "detail": "; ".join(reasons)}
    lots = min(int(plan["lots"]), int(cfg.max_lots))
    if lots < 1:
        return {"ok": False, "reasons": ["scalp_plan_rejected"],
                "detail": "no lots fit the risk budget"}
    qty = lots * lot_size
    stop = float(plan["stop"])
    stop_points = max(0.0, float(ask) - stop)
    risk = round((limit_price - stop + float(plan["estimated_variable_cost_points"])) * qty
                 + float(plan["estimated_fixed_cost_inr"]), 2)
    rules = {
        "lane": LANE_SCALP,
        "stop_points": round(stop_points, 4),
        "target_premium": float(plan["target"]),
        "profit_lock_premium": float(plan["profit_lock"]),
        "breakeven_premium": float(plan["breakeven"]),
        "trail_points": float(plan["trail_points"]),
        "lock_points": float(cfg.scalp_lock_points),
        "round_trip_cost_points": float(cfg.scalp_round_trip_cost_points),
        "max_hold_bars": int(cfg.scalp_max_hold_bars),
        "runner_max_bars": int(cfg.scalp_runner_max_bars),
        "timeframe_minutes": int(cfg.scalp_timeframe_minutes),
        "square_off_minute": int(cfg.scalp_square_off_minute),
    }
    return {"ok": True, "lots": lots, "stop_premium": stop,
            "target_premium": float(plan["target"]), "risk_amount": risk, "rules": rules}


# ------------------------------------------------------------------------ hedge

def hedge_side_for(option_type: str) -> str:
    """The futures side that offsets the bought option's market delta."""
    return "BUY" if str(option_type).upper() == "PE" else "SELL"


def opposing_hedge(positions: Any, plans: Any, *, futures_symbol: str, side: str,
                   exclude_plan_id: str = "") -> str:
    """Why a hedge of ``side`` on ``futures_symbol`` may not join the book, or "".

    Every swing hedge is the same instrument, and Kite nets by (symbol, product): a
    long and a short hedge on one contract cancel at the broker, so neither position
    could ever prove its own hedge is there to close. Same-direction hedges add up
    and are fine. ``positions`` are Snapback's open positions (a position carries its
    planned hedge from the moment the option fills); ``plans`` are in-flight plans.
    """
    want = str(side or "").upper()
    for pos in positions or []:
        if str(getattr(pos, "position_id", "")) == exclude_plan_id:
            continue
        if (str(getattr(pos, "hedge_symbol", "") or "") == futures_symbol
                and str(getattr(pos, "hedge_side", "") or "").upper() not in ("", want)):
            return f"{getattr(pos, 'option_symbol', '')} is hedged {str(pos.hedge_side).upper()}"
    for plan in plans or []:
        if str(plan.get("plan_id") or "") == exclude_plan_id:
            continue
        if (str(plan.get("futures_symbol") or "") == futures_symbol
                and str(plan.get("hedge_side") or "").upper() not in ("", want)):
            return f"plan {plan.get('plan_id')} is hedged {str(plan.get('hedge_side')).upper()}"
    return ""


def _queue_key(plan: Dict[str, Any]) -> tuple:
    return (str(plan.get("armed_at") or ""), str(plan.get("plan_id") or ""))


def plans_ahead_of(plan: Dict[str, Any], plans: Any) -> List[Dict[str, Any]]:
    """In-flight plans (other than ``plan``) that hold their place before it does.

    A plan in recovery may already have exposure at the broker, so it is always
    ahead. Of two plans merely armed, the one armed first goes first: both asking
    "who is ahead of me?" must not refuse each other, or neither would ever run.
    """
    mine = str(plan.get("plan_id") or "")
    return [p for p in plans or []
            if str(p.get("plan_id") or "") != mine
            and (str(p.get("status") or "") == "RECONCILING" or _queue_key(p) < _queue_key(plan))]


def admission_conflicts(cfg: Any, plan: Dict[str, Any], *, positions: Any,
                        plans: Any) -> List[str]:
    """Why ``plan`` may not join the book NOW, as short codes; empty means it may.

    Capacity (``max_open_positions``, ``one_position_per_underlying``) and hedge
    netting are decided at build time against the book as it was then. A plan that
    waits to be armed and executed is checked again with this, against the open
    positions and the plans ahead of it in the queue that have no position row yet.
    """
    plan_id = str(plan.get("plan_id") or "")
    open_rows = [p for p in positions or [] if str(getattr(p, "position_id", "")) != plan_id]
    held = {str(getattr(p, "position_id", "")) for p in open_rows}
    ahead = plans_ahead_of(plan, plans)
    queued = [p for p in ahead if str(p.get("plan_id") or "") not in held]

    reasons: List[str] = []
    cap = int(getattr(cfg, "max_open_positions", 0) or 0)
    if cap and len(open_rows) + len(queued) >= cap:
        reasons.append("max_open_positions")
    underlying = str(plan.get("underlying") or "").upper()
    if underlying and getattr(cfg, "one_position_per_underlying", True):
        taken = ({str(getattr(p, "underlying", "") or "").upper() for p in open_rows}
                 | {str(p.get("underlying") or "").upper() for p in queued})
        if underlying in taken:
            reasons.append("underlying_already_open")
    side = str(plan.get("hedge_side") or "").upper() or (
        hedge_side_for(str(plan.get("option_type") or "")) if plan.get("option_type") else "")
    symbol = str(plan.get("futures_symbol") or "")
    if symbol and side and opposing_hedge(open_rows, ahead, futures_symbol=symbol, side=side,
                                          exclude_plan_id=plan_id):
        reasons.append("hedge_opposes_open_hedge")
    return reasons


async def _beta(row: Dict[str, Any], ctx: PlanContext) -> Any:
    if ctx.beta_for is not None:
        return await ctx.beta_for(row, ctx.client)
    return await default_beta(row, ctx.client)


async def default_beta(row: Dict[str, Any], client: Any) -> Any:
    """Causal beta as of the signal session, exactly as the prospective path takes it."""
    from app.engines.snapback import to_bars
    from app.services import snapback as _runtime
    from app.services.snapback_beta_provenance import (
        canonical_index_beta, causal_beta_with_provenance,
    )

    symbol = str(row.get("symbol") or "")
    signal_ms = float(row.get("timestamp_ms") or 0)
    signal_day = datetime.fromtimestamp(signal_ms / 1000.0, tz=_IST).date()
    session = signal_day.strftime("%Y-%m-%d")
    if symbol == HEDGE_UNDERLYING:
        return canonical_index_beta(symbol=symbol, signal_session=session)
    cash_exchange = str(row.get("cash_exchange") or "").upper() or (
        "BSE" if str((row.get("contract") or {}).get("exchange") or "") == "BFO" else "NSE")
    cache: dict = {}
    stock = await _runtime._causal_daily_bars(
        client, str(row.get("cash_tradingsymbol") or symbol), signal_day, cache=cache,
        exchange=cash_exchange)
    market = await _runtime._causal_daily_bars(client, "NIFTY 50", signal_day, cache=cache)
    if not market:
        market = await _runtime._causal_daily_bars(client, "NIFTY", signal_day, cache=cache)
    return causal_beta_with_provenance(
        to_bars(stock) if stock else None, to_bars(market) if market else None,
        signal_session=session, symbol=symbol)


async def _spot(row: Dict[str, Any], ctx: PlanContext) -> Optional[float]:
    """The underlying's current price: a fresh quote when its identity is known."""
    cash_symbol = str(row.get("cash_tradingsymbol") or "")
    cash_exchange = str(row.get("cash_exchange") or "").upper()
    if cash_symbol and cash_exchange:
        key = f"{cash_exchange}:{cash_symbol}"
        try:
            quotes = await ctx.client.get_quote([key])
            last = _finite(((quotes or {}).get(key) or {}).get("last_price"))
            if last and last > 0:
                return last
        except Exception as exc:  # noqa: BLE001
            log.warning("snapback plan: spot quote failed for %s: %s", key, exc)
    return _finite(row.get("spot"))


async def _size_hedge(row: Dict[str, Any], ctx: PlanContext, *, contract: Dict[str, Any],
                      option_type: str, option_qty: int) -> Dict[str, Any]:
    from app.engines.snapback.policy import EXECUTION_POLICY
    from app.services.snapback_capacity import observed_hedge_margin
    from app.services.snapback_hedge_contract import HedgeContractError, select_hedge_future
    from app.services.snapback_lifecycle import hedge_requirement_for_fill

    try:
        expiry = date.fromisoformat(str(contract.get("expiry") or "")[:10])
    except ValueError:
        return {"ok": False, "reasons": ["option_expiry_unknown"]}
    try:
        rows = await ctx.instruments(HEDGE_EXCHANGE)
        future = select_hedge_future(rows or [], option_expiry=expiry, name=HEDGE_UNDERLYING,
                                     exchange=HEDGE_EXCHANGE)
    except HedgeContractError as exc:
        return {"ok": False, "reasons": ["hedge_contract_unavailable"], "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reasons": ["hedge_contract_unavailable"], "detail": str(exc)}

    key = f"{HEDGE_EXCHANGE}:{future.tradingsymbol}"
    try:
        quotes = await ctx.client.get_quote([key])
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reasons": ["hedge_quote_unavailable"], "detail": str(exc)}
    fq = (quotes or {}).get(key) or {}
    depth = fq.get("depth") or {}
    fut_bid = _finite((depth.get("buy") or [{}])[0].get("price")) or 0.0
    fut_ask = _finite((depth.get("sell") or [{}])[0].get("price")) or 0.0
    fut_ltp = _finite(fq.get("last_price")) or 0.0
    reference = fut_ask or fut_ltp
    if reference <= 0 or fut_bid <= 0 or fut_ask <= 0:
        return {"ok": False, "reasons": ["hedge_quote_unavailable"],
                "detail": "futures book unavailable"}

    beta = await _beta(row, ctx)
    clamped = getattr(beta, "clamped_beta", None)
    if (getattr(beta, "status", "") != "OK" or clamped is None
            or not math.isfinite(float(clamped)) or float(clamped) <= 0):
        return {"ok": False, "reasons": ["beta_unavailable"],
                "detail": ",".join(getattr(beta, "reason_codes", ()) or ())}

    spot = await _spot(row, ctx)
    if not spot or spot <= 0:
        return {"ok": False, "reasons": ["spot_unavailable"]}
    delta = _finite(contract.get("delta"))
    if delta is None or delta == 0:
        return {"ok": False, "reasons": ["option_delta_unavailable"]}

    req = hedge_requirement_for_fill(
        filled_option_quantity=int(option_qty), option_delta=float(delta),
        causal_beta=float(clamped), spot=float(spot), futures_price=float(reference),
        futures_lot_size=int(future.lot_size))
    if req.lots <= 0:
        return {"ok": False, "reasons": ["hedge_lots_zero"],
                "detail": f"raw hedge {req.raw_quantity:.1f} units is below one lot"}
    error_pct = abs(req.quantity - req.raw_quantity) / (req.raw_quantity + 1e-9) * 100.0
    if error_pct > float(EXECUTION_POLICY.max_hedge_discretization_error_pct):
        return {"ok": False, "reasons": ["hedge_discretization_too_coarse"],
                "detail": f"{error_pct:.1f}% error over the lot size"}

    side = hedge_side_for(option_type)
    clash = opposing_hedge(ctx.open_positions, ctx.inflight_plans,
                           futures_symbol=future.tradingsymbol, side=side,
                           exclude_plan_id=plan_id_for(str(row.get("signal_id") or ""), ctx.uid))
    if clash:
        return {"ok": False, "reasons": ["hedge_opposes_open_hedge"],
                "detail": f"{future.tradingsymbol} {side} would net against an open hedge: {clash}"}
    tick = 0.05
    limit = (round_up(fut_ask * (1 + HEDGE_BUFFER_PCT / 100.0), tick) if side == "BUY"
             else round_down(fut_bid * (1 - HEDGE_BUFFER_PCT / 100.0), tick))

    margin = await observed_hedge_margin(ctx.client, tradingsymbol=future.tradingsymbol,
                                         quantity=req.quantity, exchange=HEDGE_EXCHANGE,
                                         transaction_type=side)
    if margin is None:
        return {"ok": False, "reasons": ["hedge_margin_unavailable"],
                "detail": "the broker did not answer the margin query"}
    return {
        "ok": True, "margin": margin,
        "hedge": {"symbol": future.tradingsymbol, "quantity": int(req.quantity),
                  "lot_size": int(future.lot_size), "token": int(future.instrument_token),
                  "expiry": future.expiry.isoformat(), "side": side, "limit_price": limit,
                  "beta": float(clamped), "delta": float(delta),
                  "error_pct": round(error_pct, 2)},
    }


# --------------------------------------------------------------------- capacity

def _capacity(row: Dict[str, Any], ctx: PlanContext, *, lane: str, contract: Dict[str, Any],
              option_type: str, option_qty: int, premium_cash: float,
              hedge_margin: Optional[float], fee_reserve: float,
              now: datetime) -> Dict[str, Any]:
    """Funding + admission, through the same choke point the evidence path uses."""
    from app.core.exposure import Exposure
    from app.services.snapback_capacity import ObservedHedgeMargin, evaluate_capacity

    cfg = ctx.cfg
    underlying = str(row.get("symbol") or "").upper()
    lane_key = f"snapback:{_HORIZON_OF_LANE[lane]}"
    requested = Exposure(
        lane_key=lane_key, underlying=underlying,
        exchange=str(contract.get("exchange") or ""),
        tradingsymbol=str(contract.get("symbol") or ""), direction="long",
        quantity=int(option_qty), expiry=str(contract.get("expiry") or "")[:10],
        strike=float(contract.get("strike") or 0.0), option_type=option_type)
    held = []
    for pos in ctx.open_positions:
        held.append(Exposure(
            lane_key=f"snapback:{_HORIZON_OF_LANE.get(pos.lane, pos.lane)}",
            underlying=str(pos.underlying or "").upper(), exchange=pos.option_exchange,
            tradingsymbol=pos.option_symbol, direction="long", quantity=int(pos.option_qty),
            expiry=str(pos.option_expiry or "")[:10], option_type=pos.option_type))
    margin = hedge_margin if isinstance(hedge_margin, ObservedHedgeMargin) else (
        ObservedHedgeMargin(float(hedge_margin or 0.0)))
    # Armed plans with no position row yet will take a slot and an underlying too.
    own_plan = plan_id_for(str(row.get("signal_id") or ""), ctx.uid)
    held_ids = {str(p.position_id) for p in ctx.open_positions}
    queued = [p for p in ctx.inflight_plans
              if str(p.get("plan_id") or "") not in held_ids | {own_plan}]
    open_underlyings = ({str(p.underlying or "").upper() for p in ctx.open_positions}
                        | {str(p.get("underlying") or "").upper() for p in queued}
                        if getattr(cfg, "one_position_per_underlying", True) else set())
    decision = evaluate_capacity(
        capital=ctx.available_capital,
        reserved_margin=0.0,
        option_premium_cash=float(premium_cash),
        hedge_margin=margin,
        fee_reserve=float(fee_reserve),
        open_positions=len(ctx.open_positions) + len(queued),
        max_open_positions=int(getattr(cfg, "max_open_positions", 0) or 0),
        underlying=underlying,
        open_underlyings=open_underlyings,
        lane_key=lane_key,
        entry_at=now,
        requested_exposure=requested,
        open_exposures=held,
    )
    if decision.allowed:
        return {"ok": True, "required": decision.required_capital,
                "available": decision.available_capital}
    codes = [decision.status, *[r for r in (decision.reasons or []) if r]]
    return {"ok": False, "reasons": [str(c) for c in codes[:3]],
            "detail": "; ".join(str(r) for r in decision.reasons or [])}
