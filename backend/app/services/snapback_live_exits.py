"""Exit management for live Snapback positions.

Three kinds of exit exist and they must never race each other:

* the **broker-side stop** (a GTT placed at entry), which survives this process;
* **rule exits** this manager decides (premium stop backstop, scalp target / time /
  square-off, swing horizon, runner trail, approaching expiry);
* **operator / safety exits** that arrive as an explicit request.

Whichever fires, the position closes through ONE resumable procedure:

    release the broker stop  ->  sell the option  ->  close the hedge  ->  CLOSED

Every step persists before the next begins, order ids are stored the instant they
are known, and a restart resumes from the stored step. A retry never resubmits a
leg the journal already holds: the canonical service's idempotency key carries the
attempt number, and a new attempt is only taken after a leg is proven to have
terminated with no fill.

The hedge is closed only once the option leg is confirmed gone, and the position
is CLOSED only once BOTH legs are flat. A hedge leg that fails to close is never
dropped: it is retried every tick, alerted as CRITICAL, and after repeated failure
engages the kill switch (which stops new entries; exits are never gated).

Exits are exempt from every entry halt: the canonical service admits CLOSE_POSITION
under safe mode, the kill switch and a daily-loss halt, because a safety state that
refused to close a position would be a way to lose money.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

from app.services import snapback_live_positions as live_positions
from app.services.snapback_live_positions import (
    CLOSED, ENTERING, EXITING, OPEN, PROTECTION_ACTIVE, PROTECTION_FAILED, PROTECTION_NONE,
    RECONCILING, LivePosition, LivePositionStore,
)
from app.services.snapback_live_wiring import alert, detail_line, engage_kill_switch

log = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))

#: The end-of-day rule pass (horizon, runner, expiry) runs once per session inside
#: this window, early enough that its exit order still has the closing minutes.
EOD_PASS_START = dtime(15, 15)
EOD_PASS_END = dtime(15, 25)

#: A leg that cannot be closed this many times in a row is alerted as CRITICAL and
#: engages the kill switch (new entries stop; exits are never gated).
EXIT_FAILURE_ESCALATION = 3
#: A position the executor is still establishing is left alone for this long.
ENTERING_GRACE_S = 180
#: How long an exit order is given to fill before the remainder is cancelled once.
EXIT_FILL_TIMEOUT_S = 5.0
#: Seconds between exit retries for one position after a failed attempt.
EXIT_RETRY_BACKOFF_S = 20.0
#: How often a resting broker stop is asked whether it is still alive.
STOP_CHECK_INTERVAL_S = 60.0
#: How long the broker stop's own SELL may rest unfilled (the book gapped through its
#: limit) before it is cancelled and the position is sold at market instead.
STOP_FILL_GRACE_S = 30.0
#: Broker order states that can no longer fill.
_ORDER_TERMINAL = frozenset({"COMPLETE", "CANCELLED", "REJECTED"})


# =========================================================================
# Pure decisions
# =========================================================================

@dataclass
class ExitDecision:
    reason: str = ""
    #: Field updates to persist on the position whether or not it exits.
    updates: Dict[str, Any] = field(default_factory=dict)
    #: A runner was armed / its peak rose (for the alert).
    events: List[str] = field(default_factory=list)

    @property
    def exit(self) -> bool:
        return bool(self.reason)


def _tick_round_down(price: float, tick: float) -> float:
    import math

    tick = tick if tick and tick > 0 else 0.05
    return round(math.floor(price / tick + 1e-9) * tick, 2)


def _is_session_day(day: date) -> bool:
    """Is ``day`` a trading session, as far as the verified calendar can say?

    For a year the calendar does not cover (it ends with 2026) this is the weekday
    test, never an exception: the management of a position that is already open must
    not depend on the entry calendar. The weekday test treats an unlisted exchange
    holiday as a session, so it can only count MORE sessions than really happened and
    bring a time exit forward; it can never leave a position unmanaged.
    """
    from app.services.navigator.calendar import CalendarUnknownError, is_trading_day

    try:
        return is_trading_day(day)
    except CalendarUnknownError:
        return day.weekday() < 5


def _calendar_verified(day: date) -> bool:
    from app.services.navigator.calendar import COVERED_YEARS

    return day.year in COVERED_YEARS


def sessions_held(entry_day: str, today: date) -> int:
    """Trading sessions from the entry day to ``today`` inclusive, at least one."""
    try:
        start = date.fromisoformat(entry_day[:10])
    except ValueError:
        return 1
    if today < start:
        return 1
    count, cursor = 0, start
    while cursor <= today:
        if _is_session_day(cursor):
            count += 1
        cursor += timedelta(days=1)
    return max(1, count)


def _previous_trading_day(today: date) -> date:
    cursor = today - timedelta(days=1)
    for _ in range(10):
        if _is_session_day(cursor):
            return cursor
        cursor -= timedelta(days=1)
    return cursor


def _eod_pass_missed(last: str, today: date) -> bool:
    """Did a session's end-of-day pass go by without running?"""
    if not last:
        return False
    previous = _previous_trading_day(today)
    if _calendar_verified(today) and _calendar_verified(previous):
        return last[:10] < previous.isoformat()
    # Holidays are unverified here, so ``previous`` may have been a closed day on
    # which no pass could run. Call the pass missed only when two weekdays went by;
    # a one-session miss is then caught by the next end-of-day window instead of at
    # the open, which the window does anyway.
    return last[:10] < _previous_trading_day(previous).isoformat()


def decide_swing(pos: LivePosition, *, bid: float, now: datetime) -> ExitDecision:
    """The frozen swing exit rules, applied to a live position.

    Rule A (the premium stop) is checked on every observation. Rules B and C (the
    horizon, the runner and its give-back trail) and the expiry buffer advance only
    on a completed end-of-day mark, exactly as the validated rule does.
    """
    rules = pos.rules
    entry = float(pos.option_entry_price)
    decision = ExitDecision()
    if bid > 0 and entry > 0:
        decision.updates["high_water"] = max(float(pos.high_water or 0.0), bid)

    stop_pct = float(rules.get("premium_stop_pct") or 35.0)
    if bid > 0 and bid <= entry * (1.0 - stop_pct / 100.0):
        decision.reason = "PREMIUM_STOP"
        return decision

    local = now.astimezone(_IST)
    today = local.date()
    in_window = EOD_PASS_START <= local.time() <= EOD_PASS_END
    last = pos.last_eod_session or pos.entry_day
    missed = _eod_pass_missed(last, today)
    due = (in_window and pos.last_eod_session != today.isoformat()) or missed
    if not due or bid <= 0:
        return decision

    decision.updates["last_eod_session"] = today.isoformat()
    held = sessions_held(pos.entry_day, today)
    hold_days = int(rules.get("hold_days") or 15)
    runner_mult = float(rules.get("runner_mult") or 0.0)
    trail_pct = float(rules.get("runner_trail_pct") or 25.0)
    runner = bool(pos.is_runner)
    peak = float(pos.peak_close or 0.0)

    if not runner and held >= hold_days:
        if runner_mult > 0 and bid >= entry * runner_mult:
            decision.updates.update(is_runner=True, peak_close=bid)
            decision.events.append("runner_armed")
        else:
            decision.reason = "HOLDING_HORIZON_EXPIRED"
    elif runner:
        raised = max(peak, bid)
        if raised > peak:
            decision.updates["peak_close"] = raised
            decision.events.append("peak_raised")
        elif bid <= peak * (1.0 - trail_pct / 100.0):
            decision.reason = "RUNNER_TRAIL_STOP"

    if not decision.reason:
        try:
            expiry = date.fromisoformat(str(pos.option_expiry)[:10])
            buffer_days = int(rules.get("expiry_buffer_calendar_days") or 6)
            if (expiry - today).days <= buffer_days:
                decision.reason = "EXPIRY_APPROACHING"
        except ValueError:
            pass
    return decision


def decide_scalp(pos: LivePosition, *, bid: float, now: datetime) -> ExitDecision:
    """The scalp lifecycle (lock, target, trail, hold limits, square-off)."""
    from app.engines.snapback.intraday_lifecycle import advance_position_lifecycle
    from app.engines.snapback.intraday_models import PositionState

    rules = pos.rules
    local = now.astimezone(_IST)
    now_ms = int(local.timestamp() * 1000)
    decision = ExitDecision()
    if bid > 0:
        decision.updates["high_water"] = max(float(pos.high_water or 0.0), bid)

    hard = int(rules.get("hard_exit_ms") or 0)
    if hard and now_ms >= hard:
        decision.reason = "horizon_hard_exit"
        return decision

    timeframe_ms = max(1, int(rules.get("timeframe_minutes") or 1)) * 60_000
    bars_held = max(0, (now_ms - int(pos.opened_ms or now_ms)) // timeframe_ms)
    minute = local.hour * 60 + local.minute
    session_end = minute >= int(rules.get("square_off_minute") or 915)

    if bid <= 0 and not session_end:
        # No observable bid: the broker stop stands, but nothing else may be inferred.
        return decision

    cfg = SimpleNamespace(
        scalp_max_hold_bars=int(rules.get("max_hold_bars") or 20),
        scalp_runner_max_bars=int(rules.get("runner_max_bars") or 60),
        scalp_round_trip_cost_points=float(rules.get("round_trip_cost_points") or 1.0),
        scalp_lock_points=float(rules.get("lock_points") or 1.0),
        scalp_trail_points=float(rules.get("trail_points") or 2.0),
    )
    state = PositionState(
        position_id=pos.position_id, account_id=pos.account_id, contract_symbol=pos.option_symbol,
        side=pos.option_type, confirmed_quantity=pos.option_qty, remaining_quantity=pos.option_qty,
        entry_vwap=float(pos.option_entry_price), active_phase=pos.phase or "OPEN_SCALP",
        desired_stop=float(pos.desired_stop or pos.stop_premium),
        active_stop=float(pos.gtt_stop or pos.stop_premium),
        target_price=float(rules.get("target_premium") or 0.0) or 1e12,
        high_water_mark=float(pos.high_water or pos.option_entry_price),
        bars_held=int(bars_held), created_at_ms=int(pos.opened_ms or now_ms),
    )
    # With no completed option bar to judge continuation, a touch of the target is
    # taken as the exit rather than promoted to a runner.
    new_state, reason = advance_position_lifecycle(
        state, current_bid=float(bid or 0.0), completed_bar=None, cfg=cfg, now_ms=now_ms,
        is_session_end=session_end, tick_size=float(pos.tick_size or 0.05),
    )
    decision.updates.update(
        phase=new_state.active_phase,
        desired_stop=max(float(pos.desired_stop or 0.0), float(new_state.desired_stop)),
        high_water=max(float(pos.high_water or 0.0), float(new_state.high_water_mark)),
    )
    if reason:
        decision.reason = reason
    return decision


def decide_exit(pos: LivePosition, *, bid: float, now: datetime) -> ExitDecision:
    if str(pos.rules.get("lane") or pos.lane) == "scalp":
        return decide_scalp(pos, bid=bid, now=now)
    return decide_swing(pos, bid=bid, now=now)


# =========================================================================
# Broker helpers
# =========================================================================

def _top_of_book(quote: Dict[str, Any]) -> Dict[str, float]:
    depth = (quote or {}).get("depth") or {}

    def price(side: str) -> float:
        try:
            return float(((depth.get(side) or [{}])[0] or {}).get("price") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    try:
        ltp = float((quote or {}).get("last_price") or 0.0)
    except (TypeError, ValueError):
        ltp = 0.0
    return {"bid": price("buy"), "ask": price("sell"), "ltp": ltp}


async def _broker_net(client: Any) -> Optional[Dict[tuple, int]]:
    reader = getattr(client, "get_positions_raw", None)
    if not callable(reader):
        return None
    try:
        raw = await reader()
    except Exception as exc:  # noqa: BLE001
        log.warning("Snapback exit: broker positions unreadable: %s", exc)
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("net"), list):
        return None
    out: Dict[tuple, int] = {}
    for row in raw["net"]:
        if not isinstance(row, dict):
            continue
        key = (str(row.get("exchange") or "").upper(), str(row.get("product") or "").upper(),
               str(row.get("tradingsymbol") or row.get("symbol") or "").upper())
        try:
            out[key] = out.get(key, 0) + int(float(row.get("quantity") or 0))
        except (TypeError, ValueError):
            return None
    return out


def _stop_fired(pstop: Any, status: Any) -> bool:
    """Did the trigger fire, whatever became of the order it submitted?

    ``triggered`` alone used to mean that. The shared stop reader now also reports a
    fired trigger whose order still rests (``triggered_resting`` / ``triggered_stale``);
    all of them hand over to the broker-stop settlement, which reads the order book.
    """
    fired = getattr(pstop, "FIRED_VERDICTS", None) or frozenset({pstop.STOP_TRIGGERED})
    return status in fired


# =========================================================================
# The manager
# =========================================================================

class SnapbackExitManager:
    def __init__(self, *, positions: Optional[LivePositionStore] = None, protection: Any = None,
                 canonical: Any = None, resolver: Optional[Callable[..., Any]] = None,
                 clock: Optional[Callable[[], datetime]] = None) -> None:
        self._positions = positions
        self._protection = protection
        self._canonical = canonical
        self._resolver = resolver
        self._clock = clock
        self._retry_at: Dict[str, float] = {}
        self._stop_checked: Dict[str, float] = {}
        self._gap_alerted: Dict[str, int] = {}
        self._failures: Dict[str, int] = {}

    # ---------------------------------------------------------------- plumbing

    @property
    def store(self) -> LivePositionStore:
        return self._positions or live_positions.default_store()

    @property
    def protection(self):
        if self._protection is None:
            from app.services.snapback_live_wiring import SnapbackProtection

            self._protection = SnapbackProtection(positions=self.store)
        return self._protection

    @property
    def canonical(self):
        if self._canonical is None:
            from app.services.execution_service import CanonicalExecutionService

            self._canonical = CanonicalExecutionService()
        return self._canonical

    def now(self) -> datetime:
        return self._clock() if self._clock else datetime.now(_IST)

    # --------------------------------------------------------------------- tick

    async def tick(self, uid: str, client: Any) -> Dict[str, Any]:
        """One management pass over this account's live Snapback positions."""
        account_id = str(getattr(client, "_account_id", "") or "")
        summary: Dict[str, Any] = {"managed": 0, "exits": [], "errors": 0}
        if not account_id:
            return summary
        for pos in self.store.open_positions(uid, account_id):
            summary["managed"] += 1
            try:
                outcome = await self.manage(client, pos)
                if outcome:
                    summary["exits"].append({"position": pos.position_id, "outcome": outcome})
            except Exception as exc:  # noqa: BLE001 - one position must not starve the rest
                summary["errors"] += 1
                log.exception("Snapback exit manager failed for %s: %s", pos.position_id, exc)
        return summary

    async def manage(self, client: Any, pos: LivePosition) -> str:
        from app.services.kite_engine.market_hours import (
            PHASE_CONTINUOUS_UNVERIFIED, management_session_phase,
        )

        now = self.now()
        if pos.status == RECONCILING:
            return ""
        if pos.status == EXITING:
            if self._backing_off(pos.position_id):
                return "backoff"
            return await self._run_exit(client, pos)

        # Management of a position that exists does not depend on the entry calendar:
        # for a year with no verified holiday list it runs on weekday hours, loudly.
        phase = management_session_phase(now, exchange=pos.option_exchange)
        if phase == PHASE_CONTINUOUS_UNVERIFIED:
            self._alert_calendar_unverified(now)
        elif phase != "continuous":
            return ""
        if (pos.status == ENTERING
                and int(now.timestamp() * 1000) - int(pos.opened_ms or 0) < ENTERING_GRACE_S * 1000):
            # The executor is still establishing this position (hedge, protection).
            return ""

        # 1. the broker stop must exist; if it does not, make it
        if pos.option_qty > 0 and pos.protection_state != PROTECTION_ACTIVE:
            await self.protection.ensure(client, pos, int(pos.option_qty))
            pos = self.store.get(pos.position_id) or pos
        elif now.timestamp() - self._stop_checked.get(pos.position_id, 0.0) >= STOP_CHECK_INTERVAL_S:
            self._stop_checked[pos.position_id] = now.timestamp()
            verdict = await self._check_stop(client, pos)
            if verdict == "triggered":
                return await self._begin_exit(client, pos, "BROKER_STOP")
            pos = self.store.get(pos.position_id) or pos

        # 2. observe. Without a quote the broker stop stands and only the clock can
        # still end the trade (scalp square-off, hard time limit).
        book = await self._observe(client, pos)
        decision = decide_exit(pos, bid=(book["bid"] if book else 0.0), now=now)
        self._apply_updates(pos, decision)
        fresh = self.store.get(pos.position_id) or pos
        await self._announce(fresh, decision)

        # 3. ratchet the broker stop with the strategy's own stop (never loosens)
        if (not decision.exit and fresh.desired_stop > fresh.gtt_stop + float(fresh.tick_size or 0.05) - 1e-9
                and fresh.protection_state == PROTECTION_ACTIVE and fresh.option_qty > 0):
            await self.protection.ensure(client, fresh, int(fresh.option_qty))

        if decision.exit:
            return await self._begin_exit(client, fresh, decision.reason)
        return ""

    async def exit_position(self, client: Any, position_id: str, reason: str) -> str:
        """An explicit request (operator, safety) to close one position now."""
        pos = self.store.get(position_id)
        if pos is None or not pos.is_open:
            return "not_open"
        if pos.status == EXITING:
            return await self._run_exit(client, pos)
        return await self._begin_exit(client, pos, reason)

    # ------------------------------------------------------------------ helpers

    def _alert_calendar_unverified(self, now: datetime) -> None:
        day = now.astimezone(_IST).date().isoformat()
        alert("CALENDAR UNVERIFIED", "Snapback",
              detail_line(notes=[f"no verified exchange holiday list for {day[:4]}",
                                 "open positions are managed on weekday hours",
                                 "new entries stay refused until the list is added"]),
              key=f"snapback:CALENDAR:UNVERIFIED:{day}", severity="CRITICAL")

    def _backing_off(self, position_id: str) -> bool:
        return self._retry_at.get(position_id, 0.0) > time.monotonic()

    def _apply_updates(self, pos: LivePosition, decision: ExitDecision) -> None:
        if not decision.updates:
            return

        def apply(p: LivePosition) -> None:
            for key, value in decision.updates.items():
                setattr(p, key, value)

        self.store.mutate(pos.position_id, apply)

    async def _announce(self, pos: LivePosition, decision: ExitDecision) -> None:
        if "runner_armed" in decision.events:
            alert("TSL ARMED", pos.option_symbol,
                  detail_line(entry=pos.option_entry_price,
                              tsl=round(pos.peak_close * (1.0 - float(pos.rules.get("runner_trail_pct") or 25.0) / 100.0), 2),
                              qty=pos.option_qty,
                              notes=[f"Peak ₹{pos.peak_close:,.2f}; trails below the closing peak",
                                     "Time stop no longer applies"]),
                  key=f"snapback:{pos.position_id}:RUNNER")
        elif "peak_raised" in decision.events:
            trail = float(pos.rules.get("runner_trail_pct") or 25.0)
            alert("TSL UPDATED", pos.option_symbol,
                  detail_line(tsl=round(pos.peak_close * (1.0 - trail / 100.0), 2), qty=pos.option_qty,
                              notes=[f"Peak ₹{pos.peak_close:,.2f}"]),
                  key=f"snapback:{pos.position_id}:PEAK:{self.now().date().isoformat()}")

    async def _observe(self, client: Any, pos: LivePosition) -> Optional[Dict[str, float]]:
        key = f"{pos.option_exchange}:{pos.option_symbol}"
        try:
            quotes = await client.get_quote([key])
        except Exception as exc:  # noqa: BLE001
            quotes = None
            log.warning("Snapback exit: quote unavailable for %s: %s", key, exc)
        book = _top_of_book((quotes or {}).get(key) or {})
        if book["bid"] <= 0 and book["ltp"] <= 0:
            gaps = self._gap_alerted.get(pos.position_id, 0) + 1
            self._gap_alerted[pos.position_id] = gaps
            if gaps in (4, 40):
                alert("MARKET DATA GAP", pos.option_symbol,
                      detail_line(qty=pos.option_qty,
                                  notes=["no quote; the broker stop still protects the position"]),
                      key=f"snapback:{pos.position_id}:GAP:{gaps}", severity="WARNING")
            return None
        self._gap_alerted.pop(pos.position_id, None)
        # Exits are judged on what we could SELL for: the bid. A last price with no
        # bid is the best available mark but never lowers the bar for an exit.
        return {"bid": book["bid"] or book["ltp"], "ltp": book["ltp"]}

    async def _check_stop(self, client: Any, pos: LivePosition) -> str:
        """Ask the broker whether the resting stop is alive, fired or gone."""
        if not pos.gtt_id:
            return "none"
        from app.services.kite_engine import protective_stop as pstop

        try:
            status = await pstop.stop_status(
                client, int(pos.gtt_id), tradingsymbol=pos.option_symbol, direction="long",
                exchange=pos.option_exchange, product=pos.product, qty=int(pos.gtt_qty or pos.option_qty))
        except Exception as exc:  # noqa: BLE001
            log.warning("Snapback exit: stop status unreadable for %s: %s", pos.option_symbol, exc)
            return "unverified"
        if _stop_fired(pstop, status):
            return "triggered"
        if status == pstop.STOP_ABSENT:
            # Nothing at the broker will exit this position: protection is lost.
            def apply(p: LivePosition) -> None:
                p.gtt_id = 0
                p.gtt_qty = 0
                p.protection_state = PROTECTION_FAILED

            self.store.mutate(pos.position_id, apply)
            alert("PROTECTION LOST", pos.option_symbol,
                  detail_line(qty=pos.option_qty, notes=["broker stop no longer exists; re-arming"]),
                  key=f"snapback:{pos.position_id}:LOST:{int(time.time() // 300)}", severity="CRITICAL")
            return "absent"
        return "active" if status == pstop.STOP_ACTIVE else "unverified"

    # ---------------------------------------------------------------- exit flow

    async def _begin_exit(self, client: Any, pos: LivePosition, reason: str) -> str:
        def apply(p: LivePosition) -> bool:
            if p.status == EXITING:
                return False
            p.status = EXITING
            p.exit_reason = reason
            p.exit_started_ms = int(time.time() * 1000)

        stored = self.store.mutate(pos.position_id, apply)
        if stored is None:
            return "gone"
        log.warning("Snapback exit begins for %s: %s", pos.option_symbol, reason)
        return await self._run_exit(client, stored)

    async def _run_exit(self, client: Any, pos: LivePosition) -> str:
        """Advance the stored exit as far as it can go. Returns the outcome."""
        pos = self.store.get(pos.position_id) or pos
        if pos.exit_reason == "BROKER_STOP" and pos.option_exit_qty < pos.option_qty:
            settled = await self._settle_broker_stop(client, pos)
            if settled not in ("done", "market_exit"):
                return self._after_failure(pos, "option", settled)
            # "market_exit": the stop's own order is gone and the option is still held,
            # so the normal MARKET exit below takes it from here.
            pos = self.store.get(pos.position_id) or pos

        if pos.option_exit_qty < pos.option_qty:
            outcome = await self._close_leg(client, pos, leg="option")
            if outcome != "done":
                return self._after_failure(pos, "option", outcome)
            pos = self.store.get(pos.position_id) or pos

        if pos.has_hedge and pos.hedge_exit_qty < pos.hedge_qty:
            outcome = await self._close_leg(client, pos, leg="hedge")
            if outcome != "done":
                return self._after_failure(pos, "hedge", outcome)
            pos = self.store.get(pos.position_id) or pos

        self._failures.pop(pos.position_id, None)
        return self._finalise(pos)

    def _after_failure(self, pos: LivePosition, leg: str, outcome: str) -> str:
        self._retry_at[pos.position_id] = time.monotonic() + EXIT_RETRY_BACKOFF_S
        if outcome in ("reconciling", "stop_fired", "partial"):
            return outcome      # progress or a state with its own handling
        count = self._failures.get(pos.position_id, 0) + 1
        self._failures[pos.position_id] = count
        if count == EXIT_FAILURE_ESCALATION:
            what = ("hedge leg" if leg == "hedge" else "option leg")
            alert("EXIT FAILING", pos.option_symbol,
                  detail_line(qty=pos.option_qty if leg == "option" else pos.hedge_qty,
                              reason=str(pos.exit_reason).replace("_", " ").capitalize(),
                              notes=[f"{what} did not close after {count} attempts ({outcome})",
                                     "retrying; new entries halted"]),
                  key=f"snapback:{pos.position_id}:EXITFAIL:{leg}", severity="CRITICAL")
            engage_kill_switch(
                f"Snapback {what} for {pos.option_symbol} is not closing ({outcome})", pos.uid)
        return outcome

    def _finalise(self, pos: LivePosition) -> str:
        # Units that left without an observed price carry no value: price and P&L are
        # computed over the units Sterling actually sold.
        priced_qty = max(0, pos.option_exit_qty - pos.option_exit_unknown_qty)
        opt_price = pos.option_exit_value / priced_qty if priced_qty else 0.0
        option_pnl = (opt_price - pos.option_entry_price) * priced_qty
        unpriced = pos.option_exit_unknown_qty > 0
        hedge_pnl = 0.0
        hedge_price = 0.0
        if pos.hedge_exit_qty:
            hedge_price = pos.hedge_exit_value / pos.hedge_exit_qty
            sign = 1.0 if str(pos.hedge_side).upper() == "BUY" else -1.0
            hedge_pnl = sign * (hedge_price - pos.hedge_entry_price) * pos.hedge_exit_qty
        total = round(option_pnl + hedge_pnl, 2)

        def apply(p: LivePosition) -> None:
            p.status = CLOSED
            p.option_exit_price = round(opt_price, 4)
            p.hedge_exit_price = round(hedge_price, 4)
            p.realised_pnl = total
            p.closed_ms = int(time.time() * 1000)
            p.option_exit_order_id = ""
            p.hedge_exit_order_id = ""
            # A position closed with an unpriced option exit keeps that flag: its P&L
            # is partial and the operator has to reconcile it with the broker.
            p.flags = [f for f in p.flags if f == "option_exit_price_unknown"]

        self.store.mutate(pos.position_id, apply)
        self._retry_at.pop(pos.position_id, None)
        self._stop_checked.pop(pos.position_id, None)
        notes = []
        if pos.has_hedge:
            notes.append(f"Option {option_pnl:+,.2f} · Hedge {hedge_pnl:+,.2f} (before charges)")
        if unpriced:
            notes.append(f"{pos.option_exit_unknown_qty} option units left without a price Sterling "
                         "saw; P&L is partial, check the broker statement")
        alert("POSITION CLOSED", pos.option_symbol,
              detail_line(entry=pos.option_entry_price, exit=round(opt_price, 2), pnl=total,
                          qty=pos.option_qty,
                          reason=str(pos.exit_reason).replace("_", " ").capitalize(), notes=notes),
              key=f"snapback:{pos.position_id}:EXIT",
              severity="WARNING" if total < 0 else "INFO")
        return "closed"

    # ------------------------------------------------------------------- legs

    async def _close_leg(self, client: Any, pos: LivePosition, *, leg: str) -> str:
        """Close one leg through the canonical service. ``done`` once it is flat."""
        is_option = leg == "option"
        order_attr = "option_exit_order_id" if is_option else "hedge_exit_order_id"
        total = pos.option_qty if is_option else pos.hedge_qty
        filled_attr = "option_exit_qty" if is_option else "hedge_exit_qty"
        remaining = total - int(getattr(pos, filled_attr))
        if remaining <= 0:
            return "done"

        order_id = str(getattr(pos, order_attr) or "")
        if not order_id:
            if is_option:
                ready = await self._release_stop(client, pos)
                if ready != "released":
                    return ready
            else:
                option_flat = await self._option_is_flat(client, pos)
                if not option_flat:
                    return "option_not_flat"
            held = await self._broker_holds(client, pos, leg=leg, quantity=remaining)
            if held is False:
                if not is_option:
                    return await self._hedge_gone(pos)
                # The broker holds less of the option than Sterling believes (closed
                # or part-closed in the broker app). Book what is missing as an
                # unpriced exit, sell only what exists, and carry on to the hedge:
                # a stranded futures leg is naked directional risk.
                booked = await self._book_missing_option(client, pos, remaining)
                if booked == "unverified":
                    return "broker_quantity_unverified"
                pos = self.store.get(pos.position_id) or pos
                remaining = pos.option_qty - int(pos.option_exit_qty)
                if remaining <= 0:
                    return "done"
            order_id = await self._submit_exit(client, pos, leg=leg, quantity=remaining)
            if not order_id:
                if is_option:
                    # The stop was released and no sell went out: put the stop back.
                    fresh = self.store.get(pos.position_id) or pos
                    await self.protection.ensure(client, fresh, int(fresh.option_qty))
                return "submit_failed"

        return await self._resolve_exit(client, self.store.get(pos.position_id) or pos,
                                        leg=leg, order_id=order_id)

    async def _release_stop(self, client: Any, pos: LivePosition) -> str:
        """Take the resting broker stop out of the way, or report what became of it."""
        if not pos.gtt_id:
            return "released"
        from app.services.kite_engine import protective_stop as pstop
        from app.services.snapback_family_mode import INTENT_REDUCE, canonical_broker_capability

        gid = int(pos.gtt_id)
        try:
            with canonical_broker_capability(
                f"snapback-disarm:{pos.account_id}:{pos.option_symbol}:{gid}", intent=INTENT_REDUCE,
            ):
                result = await pstop.cancel_stop_result(client, gid)
            if result != pstop.CANCELLED:
                status = await pstop.stop_status(
                    client, gid, tradingsymbol=pos.option_symbol, direction="long",
                    exchange=pos.option_exchange, product=pos.product, qty=int(pos.gtt_qty or pos.option_qty))
                if _stop_fired(pstop, status):
                    def mark(p: LivePosition) -> None:
                        p.exit_reason = "BROKER_STOP"

                    self.store.mutate(pos.position_id, mark)
                    return "stop_fired"
                if status != pstop.STOP_ABSENT:
                    return "stop_not_released"
        except Exception as exc:  # noqa: BLE001
            log.warning("Snapback exit: stop release failed for %s: %s", pos.option_symbol, exc)
            return "stop_not_released"

        def apply(p: LivePosition) -> None:
            p.gtt_id = 0
            p.gtt_stop = 0.0
            p.gtt_qty = 0
            p.protection_state = PROTECTION_NONE

        self.store.mutate(pos.position_id, apply)
        return "released"

    async def _broker_holds(self, client: Any, pos: LivePosition, *, leg: str,
                            quantity: int) -> Optional[bool]:
        """True/False when the broker proves the quantity is/isn't there; None when it cannot."""
        if getattr(client, "_is_paper", None) is True:
            return True
        net = await _broker_net(client)
        if net is None:
            return None
        if leg == "option":
            key = (pos.option_exchange.upper(), pos.product.upper(), pos.option_symbol.upper())
            return net.get(key, 0) >= quantity
        key = (pos.hedge_exchange.upper(), pos.product.upper(), pos.hedge_symbol.upper())
        held = net.get(key, 0)
        return (held >= quantity) if str(pos.hedge_side).upper() == "BUY" else (held <= -quantity)

    async def _option_is_flat(self, client: Any, pos: LivePosition) -> bool:
        return pos.option_exit_qty >= pos.option_qty

    async def _book_missing_option(self, client: Any, pos: LivePosition, remaining: int) -> str:
        """The option leg is (partly) not at the broker: book the shortfall as unpriced.

        Re-reads the broker so one bad read cannot declare a held option gone. Returns
        ``held`` (the broker does hold it after all, nothing booked), ``unverified``
        (the book cannot be read) or ``booked``.
        """
        net = await _broker_net(client)
        if net is None:
            return "unverified"
        key = (pos.option_exchange.upper(), pos.product.upper(), pos.option_symbol.upper())
        available = max(0, int(net.get(key, 0)))
        if available >= remaining:
            return "held"
        self._book_unpriced_option_exit(pos, quantity=remaining - available, available=available)
        return "booked"

    def _book_unpriced_option_exit(self, pos: LivePosition, *, quantity: int, available: int) -> None:
        """Count ``quantity`` option units as exited with no price, flag it, alert, halt entries.

        The broker stop is released first in every exit, so nothing is left armed over
        units that no longer exist (a later trigger would open a naked short).
        """
        def apply(p: LivePosition) -> None:
            p.option_exit_qty += int(quantity)
            p.option_exit_unknown_qty += int(quantity)
            if "option_exit_price_unknown" not in p.flags:
                p.flags.append("option_exit_price_unknown")
            p.note = "option_missing_at_broker"
            p.gtt_id = 0
            p.gtt_qty = 0
            p.gtt_stop = 0.0
            p.protection_state = PROTECTION_NONE

        self.store.mutate(pos.position_id, apply)
        alert("RECONCILIATION REQUIRED", pos.option_symbol,
              detail_line(qty=int(quantity),
                          notes=["option units are not at the broker (closed outside Sterling?)",
                                 "closing the hedge; check the broker statement for the price"]),
              key=f"snapback:{pos.position_id}:RECON:option_missing:{int(quantity)}",
              severity="CRITICAL")
        engage_kill_switch(
            f"Snapback option {pos.option_symbol} is missing at the broker "
            f"({int(quantity)} units, {int(available)} held)", pos.uid)

    async def _hedge_gone(self, pos: LivePosition) -> str:
        """The broker does not hold the hedge Sterling believes it holds."""
        self._flag_reconciling(pos, "hedge_missing_at_broker")
        return "reconciling"

    def _flag_reconciling(self, pos: LivePosition, note: str) -> None:
        def apply(p: LivePosition) -> None:
            p.status = RECONCILING
            p.note = note
            if note not in p.flags:
                p.flags.append(note)

        self.store.mutate(pos.position_id, apply)
        alert("RECONCILIATION REQUIRED", pos.option_symbol,
              detail_line(qty=pos.option_qty, notes=[note.replace("_", " "),
                                                    "automatic handling stopped; check the broker"]),
              key=f"snapback:{pos.position_id}:RECON:{note}", severity="CRITICAL")
        engage_kill_switch(f"Snapback position {pos.option_symbol} needs reconciliation: {note}", pos.uid)

    async def _submit_exit(self, client: Any, pos: LivePosition, *, leg: str, quantity: int) -> str:
        """One canonical CLOSE_POSITION order. Returns its order id, or ``""``."""
        from app.services.execution_service import ExecutionRequest, ExposureEffect
        from app.services.snapback_plan_producer import generation_id

        is_option = leg == "option"
        symbol = pos.option_symbol if is_option else pos.hedge_symbol
        exchange = pos.option_exchange if is_option else pos.hedge_exchange
        if is_option:
            side = "SELL"
        else:
            side = "SELL" if str(pos.hedge_side).upper() == "BUY" else "BUY"
        lot = pos.lot_size if is_option else pos.hedge_lot_size
        await self._ensure_ledger_inventory(pos, leg=leg)
        attempt = int(pos.exit_attempts)
        request = ExecutionRequest(
            uid=pos.uid, account_id=pos.account_id, strategy_id="snapback",
            generation_id=generation_id(),
            signal_id=f"{pos.signal_id}:exit:{leg}:{attempt}",
            exchange=exchange, symbol=symbol, side=side, quantity=int(quantity),
            exposure_effect=ExposureEffect.CLOSE_POSITION, order_type="MARKET",
            tag=f"snapback:{pos.position_id}",
            payload={"product": pos.product, **({"lot_size": int(lot)} if lot else {})},
        )
        try:
            result = await self.canonical.submit_order(request, broker_client=client,
                                                       risk_approval=None)
        except Exception as exc:  # noqa: BLE001
            log.error("Snapback exit submit raised for %s: %s", symbol, exc)
            return ""
        if not result.success or not result.order_id:
            log.error("Snapback exit refused for %s: %s %s", symbol, result.status, result.error)
            if str(result.status or "").upper() == "REJECTED":
                # Nothing is working at the broker for this key and the key is spent,
                # so the next try must be a NEW attempt. An UNKNOWN outcome is
                # different: it keeps its key, so a retry can never send twice.
                def bump(p: LivePosition) -> None:
                    p.exit_attempts += 1

                self.store.mutate(pos.position_id, bump)
            alert("EXIT NOT SENT", pos.option_symbol,
                  detail_line(qty=quantity, notes=[f"{leg} leg: {result.status} {result.error}"[:160]]),
                  key=f"snapback:{pos.position_id}:EXITREFUSED:{leg}:{int(time.time() // 120)}",
                  severity="CRITICAL")
            return ""

        field_name = "option_exit_order_id" if is_option else "hedge_exit_order_id"
        req_name = "option_exit_requested" if is_option else "hedge_exit_requested"

        def apply(p: LivePosition) -> None:
            setattr(p, field_name, str(result.order_id))
            setattr(p, req_name, int(quantity))

        self.store.mutate(pos.position_id, apply)
        return str(result.order_id)

    async def _ensure_ledger_inventory(self, pos: LivePosition, *, leg: str) -> None:
        """The canonical exit proves a reduction against the fill ledger. Re-book the
        entry (idempotent) so a missing row can never turn an exit into an entry."""
        try:
            from app.services.kite_engine import fill_ledger

            if leg == "option":
                symbol, exchange, side = pos.option_symbol, pos.option_exchange, "BUY"
                oid, qty, px = pos.option_entry_order_id, pos.option_qty, pos.option_entry_price
                lot = pos.lot_size
            else:
                symbol, exchange = pos.hedge_symbol, pos.hedge_exchange
                side = str(pos.hedge_side).upper() or "BUY"
                oid, qty, px = pos.hedge_entry_order_id, pos.hedge_qty, pos.hedge_entry_price
                lot = pos.hedge_lot_size
            if oid and qty > 0 and px > 0:
                fill_ledger.record(
                    account_id=pos.account_id, uid=pos.uid, symbol=symbol, exchange=exchange,
                    side=side, order_id=oid, cumulative_quantity=int(qty),
                    cumulative_value=float(qty) * float(px), source="entry", lot_size=int(lot or 0))
        except Exception as exc:  # noqa: BLE001 - the canonical proof will say if it matters
            log.warning("Snapback exit: ledger inventory not re-booked for %s: %s", pos.option_symbol, exc)

    async def _resolve_exit(self, client: Any, pos: LivePosition, *, leg: str, order_id: str) -> str:
        from app.services import canonical_fill_reconciliation as fills

        is_option = leg == "option"
        requested = int(pos.option_exit_requested if is_option else pos.hedge_exit_requested)
        reference = float(pos.option_entry_price if is_option else pos.hedge_entry_price) or 1.0
        resolve = self._resolver or fills.resolve_entry_fill
        try:
            resolution = await resolve(
                uid=pos.uid, client=client, order_id=order_id, expected_quantity=requested,
                reference_price=reference, timeout_s=EXIT_FILL_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001
            log.error("Snapback exit: resolution failed for %s: %s", order_id, exc)
            return "resolution_failed"

        filled = int(resolution.filled_quantity or 0)
        average = float(resolution.average_price or 0.0)
        if filled > 0:
            self._book_exit(pos, leg=leg, order_id=order_id, filled=filled, average=average)
            if resolution.projection_version:
                try:
                    fills.mark_projected(resolution)
                except Exception as exc:  # noqa: BLE001
                    log.error("Snapback exit: projection ack failed for %s: %s", order_id, exc)

        if resolution.reconciliation_required or not resolution.settled:
            return "unresolved"

        def apply(p: LivePosition) -> None:
            if is_option:
                p.option_exit_qty += filled
                p.option_exit_value += filled * average
                p.option_exit_order_id = ""
            else:
                p.hedge_exit_qty += filled
                p.hedge_exit_value += filled * average
                p.hedge_exit_order_id = ""
            if filled == 0:
                p.exit_attempts += 1

        updated = self.store.mutate(pos.position_id, apply)
        if filled == 0:
            # The order ended with no fill: the leg is provably still open, so a
            # NEW attempt (new journal key) is safe. Restore protection first.
            if is_option and updated is not None and updated.option_qty > updated.option_exit_qty:
                await self.protection.ensure(client, updated, int(updated.option_qty - updated.option_exit_qty))
            return "unfilled"
        if updated is not None:
            done = (updated.option_exit_qty >= updated.option_qty if is_option
                    else updated.hedge_exit_qty >= updated.hedge_qty)
            if done:
                return "done"
            # Partial: the remainder is still open. Protect it and try again.
            def bump(p: LivePosition) -> None:
                p.exit_attempts += 1

            after = self.store.mutate(pos.position_id, bump) or updated
            if is_option and after.option_qty > after.option_exit_qty:
                await self.protection.ensure(client, after, int(after.option_qty - after.option_exit_qty))
            return "partial"
        return "unresolved"

    def _book_exit(self, pos: LivePosition, *, leg: str, order_id: str, filled: int,
                   average: float, source: str = "exit") -> None:
        try:
            from app.services.kite_engine import fill_ledger

            is_option = leg == "option"
            symbol = pos.option_symbol if is_option else pos.hedge_symbol
            exchange = pos.option_exchange if is_option else pos.hedge_exchange
            side = "SELL" if is_option else ("SELL" if str(pos.hedge_side).upper() == "BUY" else "BUY")
            applied = fill_ledger.record(
                account_id=pos.account_id, uid=pos.uid, symbol=symbol, exchange=exchange, side=side,
                order_id=order_id, cumulative_quantity=int(filled),
                cumulative_value=float(filled) * float(average), source=source,
                lot_size=int((pos.lot_size if is_option else pos.hedge_lot_size) or 0))
            if applied.reconciliation_required:
                log.critical("Snapback exit: contradictory fill evidence for %s", symbol)
                engage_kill_switch(f"Snapback exit fill for {symbol} is contradictory", pos.uid)
        except Exception as exc:  # noqa: BLE001
            log.critical("Snapback exit: fill for %s not booked to the ledger: %s", pos.option_symbol, exc)
            engage_kill_switch(f"Snapback exit fill for {pos.option_symbol} not booked: {exc}", pos.uid)

    # ---------------------------------------------------- broker-triggered stop

    @staticmethod
    def _stop_sells(orders: Any, pos: LivePosition) -> List[Dict[str, Any]]:
        """SELL orders for this option that are not one of Sterling's own exit orders."""
        mine = {str(pos.option_exit_order_id or "")} - {""}
        out: List[Dict[str, Any]] = []
        for order in orders or []:
            if not isinstance(order, dict):
                continue
            if (str(order.get("tradingsymbol") or "").upper() != pos.option_symbol.upper()
                    or str(order.get("exchange") or "").upper() != pos.option_exchange.upper()
                    or str(order.get("transaction_type") or "").upper() != "SELL"
                    or str(order.get("order_id") or "") in mine):
                continue
            out.append(order)
        return out

    @staticmethod
    def _filled(order: Dict[str, Any]) -> "tuple[int, float]":
        try:
            qty = int(float(order.get("filled_quantity") or 0))
            price = float(order.get("average_price") or 0.0)
        except (TypeError, ValueError):
            return 0, 0.0
        return qty, price

    async def _settle_broker_stop(self, client: Any, pos: LivePosition) -> str:
        """The broker's stop fired: find its SELL, book it, then close the hedge.

        ``done``        the stop's SELL filled and is booked;
        ``market_exit`` the stop's SELL never filled (the book gapped through its limit),
                        it was cancelled, and the normal MARKET exit takes over;
        anything else   is a state to retry (and, repeated, to escalate).
        """
        try:
            orders = await client.get_orders()
        except Exception as exc:  # noqa: BLE001
            log.warning("Snapback exit: order book unreadable: %s", exc)
            return "stop_fill_unverified"
        sells = self._stop_sells(orders, pos)
        match = None
        for order in sells:
            if str(order.get("status") or "").upper() != "COMPLETE":
                continue
            qty, price = self._filled(order)
            qty = qty or int(float(order.get("quantity") or 0))
            if qty >= pos.option_qty and price > 0:
                match = (str(order.get("order_id") or ""), qty, price)
        if match is None:
            net = await _broker_net(client)
            key = (pos.option_exchange.upper(), pos.product.upper(), pos.option_symbol.upper())
            if net is not None and net.get(key, 0) <= 0:
                # The option is gone but no fill record explains it: unpriced exit,
                # and the hedge still has to close.
                self._book_unpriced_option_exit(
                    pos, quantity=pos.option_qty - int(pos.option_exit_qty), available=0)
                return "done"
            return await self._stop_fill_pending(client, pos, sells)
        oid, qty, price = match
        self._book_exit(pos, leg="option", order_id=oid, filled=qty, average=price, source="gtt")

        def apply(p: LivePosition) -> None:
            p.option_exit_qty = int(qty)
            p.option_exit_value = float(qty) * float(price)
            p.gtt_id = 0
            p.gtt_qty = 0
            p.protection_state = PROTECTION_NONE
            p.option_exit_order_id = ""
            p.stop_fill_pending_ms = 0

        self.store.mutate(pos.position_id, apply)
        return "done"

    async def _stop_fill_pending(self, client: Any, pos: LivePosition,
                                 sells: List[Dict[str, Any]]) -> str:
        """The stop fired and its SELL has not filled. Never wait on it forever.

        A GTT stop sells with a LIMIT a little under its trigger; if the book gapped
        through that limit the order rests unfilled while the position keeps losing.
        Alert at once; after the grace period cancel that order (confirmed) and let
        the ordinary MARKET exit sell what is still held.
        """
        now_ms = int(time.time() * 1000)
        started = int(pos.stop_fill_pending_ms or 0)
        if not started:
            def stamp(p: LivePosition) -> None:
                p.stop_fill_pending_ms = now_ms

            self.store.mutate(pos.position_id, stamp)
            alert("STOP TRIGGERED, SELL NOT FILLED", pos.option_symbol,
                  detail_line(qty=pos.option_qty,
                              notes=["the broker stop fired but its sell order has not filled",
                                     f"cancelling it and selling at market after {int(STOP_FILL_GRACE_S)}s"]),
                  key=f"snapback:{pos.position_id}:STOPPENDING", severity="CRITICAL")
            return "stop_fill_pending"
        if (now_ms - started) / 1000.0 < STOP_FILL_GRACE_S:
            return "stop_fill_pending"

        from app.services.snapback_family_mode import INTENT_CANCEL, canonical_broker_capability

        live = [o for o in sells if str(o.get("status") or "").upper() not in _ORDER_TERMINAL]
        for order in live:
            oid = str(order.get("order_id") or "")
            try:
                with canonical_broker_capability(
                    f"snapback-stop-child-cancel:{pos.account_id}:{pos.option_symbol}:{oid}",
                    intent=INTENT_CANCEL,
                ):
                    await client.cancel_order(oid)
            except Exception as exc:  # noqa: BLE001
                log.warning("Snapback exit: stop order %s not cancelled: %s", oid, exc)
        try:
            after = self._stop_sells(await client.get_orders(), pos)
        except Exception as exc:  # noqa: BLE001
            log.warning("Snapback exit: order book unreadable after cancel: %s", exc)
            return "stop_cancel_unconfirmed"
        if any(str(o.get("status") or "").upper() not in _ORDER_TERMINAL for o in after):
            return "stop_cancel_unconfirmed"

        # Everything the stop's orders did fill is real: book it before selling the rest.
        filled_qty, filled_value, first_oid = 0, 0.0, ""
        for order in after:
            qty, price = self._filled(order)
            if qty > 0 and price > 0:
                oid = str(order.get("order_id") or "")
                self._book_exit(pos, leg="option", order_id=oid, filled=qty, average=price,
                                source="gtt")
                filled_qty += qty
                filled_value += qty * price
                first_oid = first_oid or oid

        def convert(p: LivePosition) -> None:
            p.option_exit_qty += int(filled_qty)
            p.option_exit_value += float(filled_value)
            p.exit_reason = "BROKER_STOP_UNFILLED"
            p.gtt_id = 0
            p.gtt_qty = 0
            p.gtt_stop = 0.0
            p.protection_state = PROTECTION_NONE
            p.stop_fill_pending_ms = 0

        updated = self.store.mutate(pos.position_id, convert) or pos
        alert("STOP ORDER CANCELLED, SELLING AT MARKET", pos.option_symbol,
              detail_line(qty=max(0, updated.option_qty - updated.option_exit_qty),
                          notes=["the broker stop's sell did not fill; exiting at market"]),
              key=f"snapback:{pos.position_id}:STOPMARKET", severity="WARNING")
        return "done" if updated.option_exit_qty >= updated.option_qty else "market_exit"
