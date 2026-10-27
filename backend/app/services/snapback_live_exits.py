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
import math
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

from app.services import snapback_live_positions as live_positions
from app.services.simulation.seam import is_instant_paper
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
#: A leg the broker's positions book does not show is treated as gone only after the shortfall
#: has been read this many times AND for at least this long (see ``LivePosition.shortfall_*``).
SHORTFALL_CONFIRMATIONS = 3
SHORTFALL_CONFIRM_S = 60.0
#: A position is closed only after the broker's book shows its option units gone on this many
#: consecutive readings (a lagging or partial positions reply must not hold a closed trade).
OPTION_RESIDUAL_CONFIRMATIONS = 2
#: Broker order states that can no longer fill.
_ORDER_TERMINAL = frozenset({"COMPLETE", "CANCELLED", "REJECTED"})
#: One operator flatten makes this many passes at a position that is progressing
#: (a fired stop to settle, a partial fill to finish) before it reports what is left.
FLATTEN_MAX_PASSES = 3
#: Outcomes after which an immediate second pass makes progress rather than waits.
_FLATTEN_RETRY_NOW = frozenset({"stop_fired", "partial", "unfilled"})


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
    trail_pct = float(rules.get("runner_trail_pct") if rules.get("runner_trail_pct") is not None else 25.0)
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
        elif trail_pct > 0 and bid <= peak * (1.0 - trail_pct / 100.0):
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
    if (rules.get('mode_policy') or {}).get('allow_overnight'):
        from app.core.mode_execution import trading_minutes_held
        bars_held = trading_minutes_held(int(pos.opened_ms or now_ms), now_ms) // max(1, timeframe_ms // 60_000)
    else:
        bars_held = max(0, (now_ms - int(pos.opened_ms or now_ms)) // timeframe_ms)
    minute = local.hour * 60 + local.minute
    session_end = (not (rules.get('mode_policy') or {}).get('allow_overnight')
                   and minute >= int(rules.get("square_off_minute") or 915))

    if bid <= 0 and not session_end:
        # No observable bid: the broker stop stands, but nothing else may be inferred.
        return decision

    cfg = SimpleNamespace(
        scalp_max_hold_bars=int(rules.get("max_hold_bars") or 20),
        scalp_runner_max_bars=int(rules.get("runner_max_bars") or 60),
        scalp_round_trip_cost_points=float(rules.get("round_trip_cost_points") if rules.get("round_trip_cost_points") is not None else 1.0),
        scalp_lock_points=float(rules.get("lock_points") if rules.get("lock_points") is not None else 1.0),
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
    from app.core.mode_execution import policy_exit_reason
    reason = policy_exit_reason(pos.rules.get('mode_policy', {}), now)
    if reason:
        return ExitDecision(reason=reason)
    if 'stop_points' in pos.rules or str(pos.rules.get("lane") or pos.lane) == "scalp":
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
        self._residual_seen: Dict[str, int] = {}

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
            self._alert_calendar_unverified(now, pos.uid)
        elif phase != "continuous":
            return ""
        establishing = (pos.status == ENTERING
                        and int(now.timestamp() * 1000) - int(pos.opened_ms or 0)
                        < ENTERING_GRACE_S * 1000)
        if establishing and pos.protection_state == PROTECTION_ACTIVE:
            # The executor is still establishing this position (hedge leg).
            return ""

        # 1. the broker stop must exist; if it does not, make it. The grace period
        # never covers a position with no confirmed stop: a crash or a hang between
        # the option fill and the hedge result must not leave it naked for minutes.
        if pos.option_qty > 0 and pos.protection_state != PROTECTION_ACTIVE:
            await self.protection.ensure(client, pos, int(pos.option_qty))
            pos = self.store.get(pos.position_id) or pos
        elif now.timestamp() - self._stop_checked.get(pos.position_id, 0.0) >= STOP_CHECK_INTERVAL_S:
            self._stop_checked[pos.position_id] = now.timestamp()
            verdict = await self._check_stop(client, pos)
            if verdict == "triggered":
                return await self._begin_exit(client, pos, "BROKER_STOP")
            pos = self.store.get(pos.position_id) or pos
        if establishing:
            return ""          # protection only; the hedge and the rules wait for the executor

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

    async def exit_position(self, client: Any, position_id: str, reason: str, *,
                            forced: bool = False) -> str:
        """An explicit request (operator, safety) to close one position now.

        ``forced`` is an operator / safety flatten: it is not held up by evidence the broker
        cannot supply (an unreadable trigger book) the way a rule exit is.
        """
        pos = self.store.get(position_id)
        if pos is None or not pos.is_open:
            return "not_open"
        if pos.status == EXITING:
            return await self._run_exit(client, pos, forced=forced)
        return await self._begin_exit(client, pos, reason, forced=forced)

    async def flatten_positions(self, uid: str, client: Any, *,
                                reason: str = "operator") -> Dict[str, Any]:
        """Close every live position of ``uid`` on ``client``'s account, now.

        Each position goes through :meth:`exit_position`, i.e. the one resumable
        procedure every exit uses (release the broker stop, sell the option through
        the canonical service, close the hedge, finalise), never a raw order. It is
        idempotent: a closed position is not open, and a position already EXITING
        resumes from its stored step rather than starting over. The caller holds the
        dispatcher's per-account lock so this never overlaps a normal tick.

        Returns ``{"closed": int, "failed": [...], "skipped": [...]}``. ``failed``
        positions are left exiting (their broker stop is restored if the sell never
        went out) and are finished by the next tick or the next flatten. ``skipped``
        positions were not attempted: the exchange is closed, or the position belongs
        to another account.
        """
        from app.services.kite_engine.market_hours import is_management_session

        label = "FLATTEN_" + (re.sub(r"[^A-Z0-9]+", "_", str(reason or "").upper()).strip("_")
                              or "OPERATOR")
        account_id = str(getattr(client, "_account_id", "") or "")
        closed, failed, skipped = 0, [], []
        for pos in self.store.open_positions(uid):
            tag = f"{pos.option_symbol} ({pos.position_id})"
            if not account_id:
                failed.append(f"{tag}: account_identity_unavailable")
                continue
            if pos.account_id != account_id:
                skipped.append(f"{tag}: other_account")
                continue
            if pos.status != EXITING and not is_management_session(
                    self.now(), exchange=pos.option_exchange):
                # No order can trade now; its broker stop keeps protecting it.
                skipped.append(f"{tag}: market_closed")
                continue
            try:
                outcome = await self._flatten_one(client, pos, label)
            except Exception as exc:  # noqa: BLE001 - one position must not strand the rest
                log.exception("Snapback flatten failed for %s: %s", pos.position_id, exc)
                failed.append(f"{tag}: error:{type(exc).__name__}")
                continue
            if outcome == "closed":
                closed += 1
            elif outcome != "not_open":
                failed.append(f"{tag}: {outcome}")
        if closed or failed:
            alert("FLATTEN", "Snapback",
                  detail_line(reason=str(reason or "operator").replace("_", " ").capitalize(),
                              notes=[f"{closed} closed, {len(failed)} not closed, {len(skipped)} skipped"]),
                  key=f"snapback:FLATTEN:{uid}:{int(time.time())}",
                  severity="CRITICAL" if failed else "WARNING",
                  uid=uid)
        return {"closed": closed, "failed": failed, "skipped": skipped}

    async def _flatten_one(self, client: Any, pos: LivePosition, label: str) -> str:
        outcome = ""
        for _ in range(FLATTEN_MAX_PASSES):
            outcome = await self.exit_position(client, pos.position_id, label, forced=True)
            if outcome == "stop_fill_pending":
                # The broker's stop sell is resting unfilled and the operator wants
                # out: skip the grace period, cancel it and sell at market.
                self._expire_stop_grace(pos.position_id)
            elif outcome not in _FLATTEN_RETRY_NOW:
                break
        return outcome

    def _expire_stop_grace(self, position_id: str) -> None:
        past = int(time.time() * 1000) - int(STOP_FILL_GRACE_S * 1000) - 1

        def apply(p: LivePosition) -> bool:
            if not p.stop_fill_pending_ms:
                return False
            p.stop_fill_pending_ms = min(int(p.stop_fill_pending_ms), past)

        self.store.mutate(position_id, apply)

    # ------------------------------------------------------------------ helpers

    def _alert_calendar_unverified(self, now: datetime, uid: str = "") -> None:
        day = now.astimezone(_IST).date().isoformat()
        alert("CALENDAR UNVERIFIED", "Snapback",
              detail_line(notes=[f"no verified exchange holiday list for {day[:4]}",
                                 "open positions are managed on weekday hours",
                                 "new entries stay refused until the list is added"]),
              key=f"snapback:CALENDAR:UNVERIFIED:{day}", severity="CRITICAL", uid=uid)

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
                  key=f"snapback:{pos.position_id}:RUNNER",
                  uid=pos.uid)
        elif "peak_raised" in decision.events:
            trail = float(pos.rules.get("runner_trail_pct") or 25.0)
            alert("TSL UPDATED", pos.option_symbol,
                  detail_line(tsl=round(pos.peak_close * (1.0 - trail / 100.0), 2), qty=pos.option_qty,
                              notes=[f"Peak ₹{pos.peak_close:,.2f}"]),
                  key=f"snapback:{pos.position_id}:PEAK:{self.now().date().isoformat()}",
                  uid=pos.uid)

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
                      key=f"snapback:{pos.position_id}:GAP:{gaps}", severity="WARNING",
                      uid=pos.uid)
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
                  key=f"snapback:{pos.position_id}:LOST:{int(time.time() // 300)}", severity="CRITICAL",
                  uid=pos.uid)
            return "absent"
        return "active" if status == pstop.STOP_ACTIVE else "unverified"

    # ---------------------------------------------------------------- exit flow

    async def _begin_exit(self, client: Any, pos: LivePosition, reason: str, *,
                          forced: bool = False) -> str:
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
        return await self._run_exit(client, stored, forced=forced)

    async def _run_exit(self, client: Any, pos: LivePosition, *, forced: bool = False) -> str:
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
            outcome = await self._close_leg(client, pos, leg="option", forced=forced)
            if outcome != "done":
                return self._after_failure(pos, "option", outcome)
            pos = self.store.get(pos.position_id) or pos

        if pos.has_hedge and pos.hedge_exit_qty < pos.hedge_qty:
            outcome = await self._close_leg(client, pos, leg="hedge")
            if outcome != "done":
                return self._after_failure(pos, "hedge", outcome)
            pos = self.store.get(pos.position_id) or pos

        flat = await self._confirm_option_flat(client, pos)
        if flat != "flat":
            return self._after_failure(pos, "option", flat)
        self._failures.pop(pos.position_id, None)
        return self._finalise(pos)

    async def _confirm_option_flat(self, client: Any, pos: LivePosition) -> str:
        """``flat`` once the broker confirms none of this position's option units remain.

        Both legs being booked as exited is Sterling's belief; the broker's book is the
        fact. A position is never closed while units that no other Sterling position owns
        are still held (or while the book cannot be read): it is held for a person, with
        a stop restored over what is proven held, instead of being forgotten.
        """
        if is_instant_paper(client):
            return "flat"
        net = await _broker_net(client)
        if net is None:
            return "broker_quantity_unverified"
        held = self._held_beyond_siblings(net, pos)
        if held is None:
            return "broker_quantity_unverified"
        if held == 0:
            self._residual_seen.pop(pos.position_id, None)
            return "flat"
        seen = self._residual_seen.get(pos.position_id, 0) + 1
        self._residual_seen[pos.position_id] = seen
        if seen < OPTION_RESIDUAL_CONFIRMATIONS:
            return "option_residual_unconfirmed"     # one reading is not enough: ask again
        self._residual_seen.pop(pos.position_id, None)
        if held > 0:
            await self._restore_stop_over_held(client, pos)
            self._flag_reconciling(self.store.get(pos.position_id) or pos,
                                   f"option_still_held_at_broker:{held}")
        else:
            self._flag_reconciling(pos, f"option_net_short_at_broker:{-held}")
        return "reconciling"

    def _after_failure(self, pos: LivePosition, leg: str, outcome: str) -> str:
        self._retry_at[pos.position_id] = time.monotonic() + EXIT_RETRY_BACKOFF_S
        if outcome in ("reconciling", "stop_fired", "partial", "option_residual_unconfirmed",
                       "option_missing_unconfirmed", "hedge_missing_unconfirmed"):
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
                  key=f"snapback:{pos.position_id}:EXITFAIL:{leg}", severity="CRITICAL",
                  uid=pos.uid)
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
              severity="WARNING" if total < 0 else "INFO",
              uid=pos.uid)
        return "closed"

    # ------------------------------------------------------------------- legs

    async def _close_leg(self, client: Any, pos: LivePosition, *, leg: str,
                         forced: bool = False) -> str:
        """Close one leg through the canonical service. ``done`` once it is flat."""
        is_option = leg == "option"
        order_attr = "option_exit_order_id" if is_option else "hedge_exit_order_id"
        requested_attr = "option_exit_requested" if is_option else "hedge_exit_requested"
        filled_attr = "option_exit_qty" if is_option else "hedge_exit_qty"
        total = pos.option_qty if is_option else pos.hedge_qty
        exited = getattr(pos, filled_attr)
        order_id = getattr(pos, order_attr)
        requested = getattr(pos, requested_attr)

        # Check durability *before* broker inventory reads or stop cancellation.
        # An orphan request can be a real broker order whose ACK was lost.
        # Clearing its stop first would leave the remaining option unprotected
        # even though sending a replacement exit is correctly prohibited.
        if (leg not in {"option", "hedge"}
                or type(total) is not int or type(exited) is not int
                or total < 0 or exited < 0 or exited > total
                or type(order_id) is not str
                or type(requested) is not int or requested < 0
                or bool(order_id) != bool(requested)
                or (order_id and (not order_id.strip() or order_id != order_id.strip()))):
            self._flag_reconciling(pos, "exit_registry_state_unverified")
            return "exit_state_unverified"
        remaining = total - exited
        if remaining <= 0:
            if order_id:
                self._flag_reconciling(pos, "exit_order_still_working_after_flat")
                return "exit_state_unverified"
            return "done"
        if not order_id:
            # Prove the broker quantity belongs to this logical position before
            # removing its resting stop. Kite aggregates same-contract positions
            # across strategies; unknown ownership must leave protection intact
            # and must never fall through to a SELL.
            if is_option:
                held = await self._broker_holds(
                    client, pos, leg=leg, quantity=remaining
                )
                if held is None:
                    return "broker_quantity_unverified"
                ready = await self._release_stop(client, pos, forced=forced)
                if ready != "released":
                    return ready
            else:
                option_flat = await self._option_is_flat(client, pos)
                if not option_flat:
                    return "option_not_flat"
                held = await self._broker_holds(
                    client, pos, leg=leg, quantity=remaining
                )
                if held is None:
                    return "broker_quantity_unverified"
            if held is False:
                if not is_option:
                    return await self._hedge_gone(client, pos)
                # The broker holds less of the option than Sterling believes (closed or
                # part-closed in the broker app, or a reply that lost part of the book). Sell
                # what is proven to exist now; the missing units are booked as an unpriced
                # exit only once the shortfall has persisted, and the hedge closes after.
                step, sellable = await self._option_shortfall_step(client, pos, remaining)
                if step != "sell":
                    return step
                pos = self.store.get(pos.position_id) or pos
                remaining = min(int(sellable), pos.option_qty - int(pos.option_exit_qty))
                if remaining <= 0:
                    return "done"
            elif held is True:
                self._clear_shortfall(pos)
            order_id = await self._submit_exit(client, pos, leg=leg, quantity=remaining)
            if order_id is None:
                # Submission may have reached the broker; a second SELL or
                # restoring a prior stop can double-close this position.
                try:
                    current = self.store.get(pos.position_id) or pos
                except Exception as exc:  # noqa: BLE001 - disk may also be full
                    log.critical("Snapback exit hold cannot read registry: %s", exc)
                    current = pos
                self._flag_reconciling(
                    current, f"{leg}_exit_submission_identity_unknown")
                return "submit_unknown"
            if not order_id:
                if is_option:
                    # The stop was released and no sell went out: protect exactly
                    # what is still held. Restoring the original entry quantity
                    # after a prior partial exit would over-size the stop and could
                    # sell the account short if it later fired.
                    fresh = self.store.get(pos.position_id) or pos
                    remaining_held = max(
                        0,
                        int(fresh.option_qty or 0) - int(fresh.option_exit_qty or 0),
                    )
                    if remaining_held > 0:
                        await self.protection.ensure(client, fresh, remaining_held)
                return "submit_failed"

        return await self._resolve_exit(client, self.store.get(pos.position_id) or pos,
                                        leg=leg, order_id=order_id)

    async def _release_stop(self, client: Any, pos: LivePosition, *, forced: bool = False) -> str:
        """Take the resting broker stop out of the way, or report what became of it."""
        if not pos.gtt_id:
            if pos.gtt_pending or pos.protection_state == PROTECTION_FAILED:
                # No id on file, yet a stop may rest at the broker: a placement whose reply
                # was lost. It must be found and removed before the sell, or it fires later
                # on a position the account no longer holds.
                return await self._release_unrecorded_stop(client, pos, forced=forced)
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

    async def _release_unrecorded_stop(self, client: Any, pos: LivePosition, *,
                                       forced: bool = False) -> str:
        """Find and remove a broker stop this position never recorded, before selling.

        ``released`` when none remains; ``stop_fired`` when one already fired (it is adopted
        so the broker-stop settlement books its sell); ``stop_not_released`` when the broker
        cannot say (an unreadable trigger book, a send that may still land, a cancel that did
        not come back). Forced flatten keeps the same uncertainty fence: a rival sell
        could otherwise open a short when the stop child lands.
        """
        from app.services.kite_engine import protective_stop as pstop
        from app.services.snapback_family_mode import INTENT_REDUCE, canonical_broker_capability

        reader = getattr(self.protection, "unrecorded_stops", None)
        if not callable(reader):
            return "stop_not_released"
        # A flatten that is still finishing on a later tick is as forced as its first pass.
        forced = forced or str(pos.exit_reason or "").upper().startswith("FLATTEN")
        try:
            info = await reader(client, pos)
        except Exception as exc:
            log.warning("Snapback exit: unrecorded stop lookup failed: %s", exc)
            return "stop_not_released"
        if info is None or info.unclear or (info.in_flight and not info.ours):
            if forced:
                alert("STRAY STOP POSSIBLE", pos.option_symbol,
                      detail_line(qty=pos.option_qty,
                                  notes=["the broker's trigger book could not prove no stop rests",
                                         "exit held; reconcile remaining GTTs and child orders "
                                         "for this contract in Kite before retrying"]),
                      key=f"snapback:{pos.position_id}:STRAY:{int(time.time() // 300)}",
                      severity="CRITICAL",
                      uid=pos.uid)
            return "stop_not_released"
        fired = [r for r in info.ours if r["verdict"] == pstop.STOP_TRIGGERED]
        if fired:
            keep = min(int(r["id"]) for r in fired)
            row = next(r for r in fired if int(r["id"]) == keep)

            def adopt(p: LivePosition) -> None:
                p.gtt_id = keep
                p.gtt_qty = int(row["qty"])
                p.exit_reason = "BROKER_STOP"
                p.gtt_pending = False

            self.store.mutate(pos.position_id, adopt)
            return "stop_fired"
        for row in info.ours:
            gid = int(row["id"])
            try:
                with canonical_broker_capability(
                    f"snapback-disarm:{pos.account_id}:{pos.option_symbol}:{gid}",
                    intent=INTENT_REDUCE,
                ):
                    result = await pstop.cancel_stop_result(client, gid)
            except Exception as exc:  # noqa: BLE001
                log.warning("Snapback exit: stray stop #%s not cancelled: %s", gid, exc)
                return "stop_not_released"
            if result == pstop.UNKNOWN:
                return "stop_not_released"

        def clear(p: LivePosition) -> None:
            p.gtt_id = 0
            p.gtt_stop = 0.0
            p.gtt_qty = 0
            p.protection_state = PROTECTION_NONE
            p.gtt_pending = False
            p.gtt_pending_ms = 0
            p.gtt_pending_qty = 0
            p.gtt_pending_trigger = 0.0

        self.store.mutate(pos.position_id, clear)
        return "released"

    async def _broker_holds(self, client: Any, pos: LivePosition, *, leg: str,
                            quantity: int) -> Optional[bool]:
        """True/False when the broker proves the quantity is/isn't there; None when it cannot."""
        if is_instant_paper(client):
            return True
        net = await _broker_net(client)
        if net is None:
            return None
        if leg == "option":
            held = self._held_beyond_siblings(net, pos)
            return None if held is None else held >= quantity
        key = (pos.hedge_exchange.upper(), pos.product.upper(), pos.hedge_symbol.upper())
        held = net.get(key, 0)
        return (held >= quantity) if str(pos.hedge_side).upper() == "BUY" else (held <= -quantity)

    async def _option_is_flat(self, client: Any, pos: LivePosition) -> bool:
        return pos.option_exit_qty >= pos.option_qty

    def _note_shortfall(self, pos: LivePosition, leg: str) -> bool:
        """Record one more reading that the broker does not show ``leg``; True once it is proven.

        Proven means seen on ``SHORTFALL_CONFIRMATIONS`` readings spanning at least
        ``SHORTFALL_CONFIRM_S``. The count is durable, so a restart does not restart the clock.
        """
        now_ms = int(time.time() * 1000)

        def apply(p: LivePosition) -> None:
            if p.shortfall_leg != leg:
                p.shortfall_leg, p.shortfall_obs, p.shortfall_first_ms = leg, 0, 0
            p.shortfall_obs += 1
            if not p.shortfall_first_ms:
                p.shortfall_first_ms = now_ms

        updated = self.store.mutate(pos.position_id, apply) or pos
        return (int(updated.shortfall_obs) >= SHORTFALL_CONFIRMATIONS
                and now_ms - int(updated.shortfall_first_ms) >= SHORTFALL_CONFIRM_S * 1000)

    def _clear_shortfall(self, pos: LivePosition) -> None:
        if not (pos.shortfall_leg or pos.shortfall_obs or pos.shortfall_first_ms):
            return

        def apply(p: LivePosition) -> None:
            p.shortfall_leg, p.shortfall_obs, p.shortfall_first_ms = "", 0, 0

        self.store.mutate(pos.position_id, apply)

    async def _option_shortfall_step(self, client: Any, pos: LivePosition,
                                     remaining: int) -> "tuple[str, int]":
        """The broker shows fewer option units than ``remaining``. ``(step, sellable)``.

        ``("sell", n)``  sell ``n`` units (what is proven held; ``n`` may be 0 once the
                         shortfall is proven and booked, which then ends the leg);
        anything else    an outcome to retry: the book cannot be read, or the shortfall has
                         not yet persisted (the exit stays EXITING, nothing is booked).
        """
        net = await _broker_net(client)
        if net is None:
            return "broker_quantity_unverified", 0
        held = self._held_beyond_siblings(net, pos)
        if held is None:
            return "broker_quantity_unverified", 0
        available = max(0, held)
        if available >= remaining:
            self._clear_shortfall(pos)          # the broker holds it after all
            return "sell", remaining
        if not self._note_shortfall(pos, "option"):
            if available > 0:
                return "sell", available
            # Broker currently proves ZERO option holdings, not 'remaining'.
            # Restoring a SELL GTT sized from our stale local registry could
            # create a NAKED short if the broker book is right. The next
            # observation will either find positive holdings (which are then
            # sold), or confirm the disappearance and reconcile it. Never
            # arm a new protective SELL based solely on local inventory
            # against a contradictory broker-zero observation.
            log.warning(
                "Snapback exit: option holdings temporarily unverified for %s; "
                "stop restoration withheld pending positive broker inventory",
                pos.position_id,
            )
            return "option_missing_unconfirmed", 0
        self._book_unpriced_option_exit(pos, quantity=remaining - available, available=available)
        return "sell", available

    def _book_unpriced_option_exit(self, pos: LivePosition, *, quantity: int, available: int) -> None:
        """Count ``quantity`` option units as exited with no price, flag it, alert, halt entries.

        Only ever called for a shortfall the broker's book has shown repeatedly (see
        ``_note_shortfall``). The broker stop is released first in every exit, so nothing is
        left armed over units that no longer exist (a later trigger would open a naked short).
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
            p.shortfall_leg, p.shortfall_obs, p.shortfall_first_ms = "", 0, 0

        self.store.mutate(pos.position_id, apply)
        alert("RECONCILIATION REQUIRED", pos.option_symbol,
              detail_line(qty=int(quantity),
                          notes=["option units are not at the broker (closed outside Sterling?)",
                                 "closing the hedge; check the broker statement for the price"]),
              key=f"snapback:{pos.position_id}:RECON:option_missing:{int(quantity)}",
              severity="CRITICAL",
              uid=pos.uid)
        engage_kill_switch(
            f"Snapback option {pos.option_symbol} is missing at the broker "
            f"({int(quantity)} units, {int(available)} held)", pos.uid)

    async def _hedge_gone(self, client: Any, pos: LivePosition) -> str:
        """The broker does not show the hedge Sterling believes it holds.

        A reconciliation hold is a stop to all automatic handling, so it follows proof, not a
        single reading; until then the leg is simply not closed yet. When it is proven, any
        option units still held get their stop back first: a position held for a person is
        never left with its option unprotected.
        """
        if not self._note_shortfall(pos, "hedge"):
            return "hedge_missing_unconfirmed"
        await self._restore_stop_over_held(client, pos)
        self._flag_reconciling(self.store.get(pos.position_id) or pos, "hedge_missing_at_broker")
        return "reconciling"

    async def _restore_stop_over_held(self, client: Any, pos: LivePosition) -> None:
        """Best effort: a broker stop over whatever option units the broker proves are held."""
        if is_instant_paper(client):
            return
        try:
            net = await _broker_net(client)
            if net is None:
                return
            held = self._held_beyond_siblings(net, pos)
            if held is None:
                return
            fresh = self.store.get(pos.position_id) or pos
            remaining = max(0, int(fresh.option_qty) - int(fresh.option_exit_qty))
            protected_quantity = min(int(held), remaining)
            if protected_quantity > 0 and fresh.protection_state != PROTECTION_ACTIVE:
                await self.protection.ensure(client, fresh, protected_quantity, escalate=False)
        except Exception as exc:  # noqa: BLE001 - the hold itself must still happen
            log.warning("Snapback exit: stop not restored over held units: %s", exc)

    def _flag_reconciling(self, pos: LivePosition, note: str) -> None:
        def apply(p: LivePosition) -> None:
            p.status = RECONCILING
            p.protection_state = PROTECTION_FAILED
            p.note = note
            if note not in p.flags:
                p.flags.append(note)

        try:
            self.store.mutate(pos.position_id, apply)
        except Exception as exc:  # noqa: BLE001 - never skip the safety halt
            log.critical("Snapback cannot persist reconciliation hold for %s: %s",
                         pos.position_id, exc)
        engage_kill_switch(
            f"Snapback position {pos.option_symbol} needs reconciliation: {note}",
            pos.uid)
        alert("RECONCILIATION REQUIRED", pos.option_symbol,
              detail_line(qty=pos.option_qty, notes=[note.replace("_", " "),
                                                    "automatic handling stopped; check the broker"]),
              key=f"snapback:{pos.position_id}:RECON:{note}", severity="CRITICAL",
              uid=pos.uid)

    async def _submit_exit(self, client: Any, pos: LivePosition, *, leg: str, quantity: int) -> Optional[str]:
        """One canonical CLOSE_POSITION order. Returns its order id, or ``""``."""
        from app.services.execution_service import ExecutionRequest, ExposureEffect
        from app.services.snapback_plan_producer import generation_id

        # Risk-reducing exits must never default to an arbitrary futures
        # direction when durable hedge metadata are incomplete/corrupt.
        # An invented BUY could increase exposure rather than close it.
        if leg not in {"option", "hedge"} or (
                leg == "hedge" and str(pos.hedge_side).upper() not in {"BUY", "SELL"}):
            log.critical("Snapback exit: unknown leg/hedge side for %s", pos.position_id)
            engage_kill_switch("Snapback exit leg or hedge direction unverified", pos.uid)
            return None
        # A real broker transport may not be switched to a different account
        # since the entry was booked; ledger and registry are account-bound.
        if getattr(client, "_is_paper", None) is False and (
                not pos.account_id or
                str(getattr(client, "_account_id", "") or "") != pos.account_id):
            log.critical("Snapback exit: broker account changed for %s", pos.position_id)
            engage_kill_switch("Snapback exit authenticated account mismatch", pos.uid)
            return None
        # Validate the durable quantity and order slot BEFORE submitting to Kite.
        # A prior request without its order ID is an unresolved broker attempt;
        # submitting another exit could create a naked opposite-side position.
        raw_total = pos.option_qty if leg == "option" else pos.hedge_qty
        raw_exited = pos.option_exit_qty if leg == "option" else pos.hedge_exit_qty
        old_order_id = pos.option_exit_order_id if leg == "option" else pos.hedge_exit_order_id
        old_requested = pos.option_exit_requested if leg == "option" else pos.hedge_exit_requested
        if (type(quantity) is not int or quantity <= 0
                or type(raw_total) is not int or type(raw_exited) is not int
                or raw_total <= 0 or not 0 <= raw_exited < raw_total
                or quantity > raw_total - raw_exited
                or type(old_order_id) is not str or type(old_requested) is not int
                or old_requested < 0 or bool(old_order_id) != bool(old_requested)
                or (old_order_id and old_requested != quantity)):
            log.critical("Snapback exit: untrusted quantity/order slot for %s", pos.position_id)
            engage_kill_switch("Snapback exit quantity/order state needs reconciliation", pos.uid)
            return None
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
        except Exception as exc:  # noqa: BLE001 - request may already exist at broker
            log.critical("Snapback exit submit outcome unknown for %s: %s", symbol, exc)
            return None

        status = str(getattr(result, "status", "") or "").upper()
        broker_order_id = str(getattr(result, "order_id", "") or "")
        if not broker_order_id:
            if (status not in {"HALTED", "REJECTED_PLAN", "REJECTED_RELEASE_AUTHORITY",
                                "REJECTED_RISK", "REJECTED_BINDING", "REJECTED_INPUT"}
                    or bool(getattr(result, "success", False))
                    or getattr(result, "filled_quantity", 0) != 0):
                # No proof the broker did not receive this request. Persist an
                # owner-scoped reconciliation hold; never place another sell.
                return None
            log.error("Snapback exit refused for %s: %s %s", symbol, result.status, result.error)
            if status in {"REJECTED", "CANCELLED"} and int(
                    getattr(result, "filled_quantity", 0) or 0) == 0:
                self.store.mutate(
                    pos.position_id,
                    lambda p: setattr(p, "exit_attempts", p.exit_attempts + 1))
            alert("EXIT NOT SENT", pos.option_symbol,
                  detail_line(qty=quantity, notes=[f"{leg} leg: {result.status} {result.error}"[:160]]),
                  key=f"snapback:{pos.position_id}:EXITREFUSED:{leg}:{int(time.time() // 120)}",
                  severity="CRITICAL", uid=pos.uid)
            return ""

        # An order ID is recovery authority, regardless of a response's
        # optimistic/negative success flag. The resolver must prove whether
        # the known order actually reduced inventory and is terminal.
        from app.services.snapback_exit_order_binding import bind_acknowledged_exit

        def apply(p: LivePosition) -> None:
            bind_acknowledged_exit(
                p, pos, leg=leg, broker_order_id=broker_order_id,
                requested=int(quantity),
            )

        try:
            stored = self.store.mutate(pos.position_id, apply)
            if stored is None:
                raise RuntimeError("exit position vanished before order ID persistence")
        except Exception as exc:  # noqa: BLE001 - SELL/BUY may already exist at Kite
            log.critical(
                "Snapback exit: broker order %s ACKed but durable registry failed: %s",
                broker_order_id, exc,
            )
            engage_kill_switch(
                f"Snapback exit broker order {broker_order_id} not persisted; reconcile",
                pos.uid,
            )
            # Caller enters a reconciliation hold. No blind resend and no
            # competing option stop may be rearmed while ACK is uncertain.
            return None
        return broker_order_id

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

        # Once an exit SELL/BUY reached the broker, malformed evidence must
        # never clear its order ID or authorize another exit attempt. In
        # particular, an unsigned/int-truncated or over-requested cumulative
        # quantity could accidentally count more closes than broker holdings.
        try:
            raw_qty = getattr(resolution, "filled_quantity", None)
            raw_px = getattr(resolution, "average_price", None)
            if type(raw_qty) is not int or not 0 <= raw_qty <= requested:
                raise ValueError("invalid_exit_filled_quantity")
            if (type(raw_px) not in (int, float) or not math.isfinite(raw_px)
                    or raw_px < 0 or (raw_qty > 0 and raw_px <= 0)):
                raise ValueError("invalid_exit_average_price")
            filled, average = raw_qty, float(raw_px)
        except (ValueError, TypeError, OverflowError) as exc:
            log.error("Snapback exit: %s has invalid broker fill receipt: %s", order_id, exc)
            engage_kill_switch(
                f"Snapback exit {order_id} cannot verify broker fill quantity/price",
                pos.uid)
            return "unresolved"
        if filled > 0:
            if not self._book_exit(pos, leg=leg, order_id=order_id, filled=filled, average=average,
                                   exchange_ts_ms=int(getattr(resolution, "exchange_ts_ms", 0) or 0)):
                return "accounting_pending"

        # A complete requested quantity is NOT broker-terminal proof:
        # OPEN/UPDATE working orders can still change. Keep their durable
        # identity and never submit a replacement before terminal evidence.
        if (resolution.reconciliation_required or not resolution.settled
                or getattr(resolution, "terminal", None) is not True
                or str(getattr(resolution, "status", "") or "").upper()
                   not in _ORDER_TERMINAL):
            return "unresolved"
        # A terminal no-fill CANCELLED/REJECTED receipt has nothing to
        # project and may legitimately carry version zero. COMPLETE/zero-fill
        # instead conflicts with the claimed broker completion.
        terminal_status = str(getattr(resolution, "status", "") or "").upper()
        if filled == 0 and terminal_status not in {"CANCELLED", "REJECTED"}:
            return "unresolved"
        if filled > 0:
            version = getattr(resolution, "projection_version", None)
            if type(version) is not int or version <= 0:
                return "projection_pending"

        def apply(p: LivePosition) -> None:
            booked = p.option_exit_booked_ids if is_option else p.hedge_exit_booked_ids
            if str(order_id) in booked:
                return
            if is_option:
                p.option_exit_qty += filled
                p.option_exit_value += filled * average
                p.option_exit_booked_ids.append(str(order_id))
            else:
                p.hedge_exit_qty += filled
                p.hedge_exit_value += filled * average
                p.hedge_exit_booked_ids.append(str(order_id))
            if filled == 0:
                p.exit_attempts += 1

        updated = self.store.mutate(pos.position_id, apply)
        if updated is not None and filled > 0:
            try:
                if not fills.mark_projected(resolution):
                    from app.services.kite_engine import order_journal
                    intent = order_journal.get(resolution.intent_key)
                    if not intent or intent.projection_pending or intent.projection_version != resolution.projection_version:
                        return "projection_pending"
            except Exception as exc:
                log.error("Snapback exit: projection ack failed for %s: %s", order_id, exc)
                return "projection_pending"
        if updated is not None:
            field_name = "option_exit_order_id" if is_option else "hedge_exit_order_id"
            requested_field = "option_exit_requested" if is_option else "hedge_exit_requested"

            def retire_terminal_exit(p: LivePosition) -> None:
                # A broker-terminal ACK must be retired atomically. Keeping a
                # nonzero request after dropping its ID makes recovery unable
                # to distinguish a completed leg from an unknown prior send.
                if getattr(p, field_name) != str(order_id):
                    raise ValueError("exit working order changed during resolution")
                setattr(p, field_name, "")
                setattr(p, requested_field, 0)

            try:
                updated = self.store.mutate(pos.position_id, retire_terminal_exit)
                if updated is None:
                    raise RuntimeError("position vanished while retiring exit ACK")
            except Exception as exc:  # noqa: BLE001 - do not resubmit an uncertain exit
                log.critical("Snapback cannot retire terminal exit %s: %s", order_id, exc)
                engage_kill_switch(
                    f"Snapback terminal exit {order_id} needs reconciliation", pos.uid)
                return "unresolved"
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
                   average: float, source: str = "exit", exchange_ts_ms: int = 0) -> bool:
        try:
            from app.services.kite_engine import fill_ledger

            if leg not in {"option", "hedge"} or (
                    leg == "hedge" and str(pos.hedge_side).upper() not in {"BUY", "SELL"}):
                raise ValueError("unknown hedged exit direction; cannot book ledger")
            is_option = leg == "option"
            symbol = pos.option_symbol if is_option else pos.hedge_symbol
            exchange = pos.option_exchange if is_option else pos.hedge_exchange
            side = "SELL" if is_option else ("SELL" if str(pos.hedge_side).upper() == "BUY" else "BUY")
            applied = fill_ledger.record(
                account_id=pos.account_id, uid=pos.uid, symbol=symbol, exchange=exchange, side=side,
                order_id=order_id, cumulative_quantity=int(filled),
                cumulative_value=float(filled) * float(average), source=source,
                exchange_ts_ms=exchange_ts_ms,
                lot_size=int((pos.lot_size if is_option else pos.hedge_lot_size) or 0))
            ledger_reason = str(getattr(applied, "reason", "") or "")
            if (applied.reconciliation_required
                    or ledger_reason not in {"accepted", "duplicate"}):
                log.critical("Snapback exit: fill ledger refuses %s for %s",
                             ledger_reason or "unknown", symbol)
                engage_kill_switch(
                    f"Snapback exit fill for {symbol} needs ledger reconciliation",
                    pos.uid)
                return False
            return True
        except Exception as exc:  # noqa: BLE001
            log.critical("Snapback exit: fill for %s not booked to the ledger: %s", pos.option_symbol, exc)
            engage_kill_switch(f"Snapback exit fill for {pos.option_symbol} not booked: {exc}", pos.uid)
            return False

    # ---------------------------------------------------- broker-triggered stop

    @staticmethod
    def _booked_exit_ids(pos: LivePosition) -> frozenset:
        """Every option exit order whose fill this position already counts (or is waiting on)."""
        ids = {str(i) for i in (pos.option_exit_booked_ids or []) if i}
        if pos.option_exit_order_id:
            ids.add(str(pos.option_exit_order_id))
        return frozenset(ids)

    @staticmethod
    def _is_sell_of(order: Dict[str, Any], pos: LivePosition) -> bool:
        return (str(order.get("tradingsymbol") or "").upper() == pos.option_symbol.upper()
                and str(order.get("exchange") or "").upper() == pos.option_exchange.upper()
                and str(order.get("transaction_type") or "").upper() == "SELL")

    @staticmethod
    async def _gtt_child_order_id(client: Any, trigger_id: int) -> str:
        """The order id the broker's own trigger reports for the order it placed ("" if none)."""
        try:
            row = await client.get_gtt(int(trigger_id))
        except Exception:  # noqa: BLE001 - the order book is read next; this is only a hint
            return ""
        try:
            legs = row.get("orders") if isinstance(row, dict) else None
            if isinstance(legs, str):
                import json

                legs = json.loads(legs)
            for leg in legs if isinstance(legs, list) else []:
                result = leg.get("result") if isinstance(leg, dict) else None
                if isinstance(result, str):
                    import json

                    result = json.loads(result)
                inner = (result or {}).get("order_result") if isinstance(result, dict) else None
                if isinstance(inner, str):
                    import json

                    inner = json.loads(inner)
                oid = str((inner or {}).get("order_id") or "").strip() if isinstance(inner, dict) else ""
                if oid:
                    return oid
        except (TypeError, ValueError):
            return ""
        return ""

    async def _stop_child_orders(self, client: Any, pos: LivePosition,
                                 orders: Any) -> Optional[List[Dict[str, Any]]]:
        """The order the broker's own stop submitted, and nothing else.

        ``[x]`` the stop's own order; ``[]`` the stop provably has no order (it was rejected
        and never placed one); ``None`` the stop fired but its order cannot be identified
        with certainty, or the book cannot be read. Every other SELL on the contract (this
        position's earlier exit orders, another strategy's, a manual one) is not the stop's:
        counting one as the stop's books it twice, cancels an order that is not ours, and
        closes the position with units still held.
        """
        from app.services.kite_engine import protective_stop as pstop

        if not pos.gtt_id or not isinstance(orders, list):
            return None
        booked = self._booked_exit_ids(pos)
        remaining = max(0, int(pos.option_qty) - int(pos.option_exit_qty))
        try:
            state = await pstop.stop_state(
                client, int(pos.gtt_id), tradingsymbol=pos.option_symbol, direction="long",
                exchange=pos.option_exchange, product=pos.product,
                qty=int(pos.gtt_qty or remaining or pos.option_qty), ignore_order_ids=booked)
        except Exception as exc:  # noqa: BLE001
            log.warning("Snapback exit: stop state unreadable for %s: %s", pos.option_symbol, exc)
            return None
        child_id = str(state.child_order_id or "")
        if not child_id:
            return [] if state.verdict == pstop.STOP_ABSENT else None
        if child_id in booked:
            return []                     # already counted: nothing new to book
        order = next((o for o in orders if isinstance(o, dict)
                      and str(o.get("order_id") or "") == child_id), None)
        if order is None or not self._is_sell_of(order, pos):
            return None
        if child_id != await self._gtt_child_order_id(client, int(pos.gtt_id)):
            # Found by matching the book, not by the trigger's own record: a second SELL of
            # the same size on this contract makes the match a guess.
            for other in orders:
                if (isinstance(other, dict) and other is not order
                        and str(other.get("order_id") or "") not in booked | {child_id}
                        and self._is_sell_of(other, pos)
                        and str(other.get("product") or "").upper() == pos.product.upper()
                        and str(other.get("status") or "").upper() not in ("REJECTED", "CANCELLED")
                        and str(other.get("quantity") or "") == str(order.get("quantity") or "")):
                    log.warning("Snapback exit: stop order for %s is ambiguous (%s / %s)",
                                pos.option_symbol, child_id, other.get("order_id"))
                    return None
        return [order]

    @staticmethod
    def _filled(order: Dict[str, Any]) -> "tuple[int, float]":
        """Read a broker fill without truncating quantity or inventing fills.

        Bad or missing broker data is UNKNOWN, never a safe zero fill.
        The caller must halt and reconcile rather than resell that exposure.
        """
        from decimal import Decimal, InvalidOperation

        raw_qty = order.get("filled_quantity")
        raw_price = order.get("average_price")
        if (type(raw_qty) not in (str, int, float) or raw_qty == ""
                or type(raw_price) not in (str, int, float, type(None))):
            raise ValueError("broker stop fill quantity or price unavailable")
        try:
            decimal_qty = Decimal(str(raw_qty))
            decimal_price = Decimal(str(raw_price if raw_price not in (None, "") else 0))
        except InvalidOperation as exc:
            raise ValueError("broker stop fill is not numeric") from exc
        if (not decimal_qty.is_finite() or decimal_qty < 0
                or decimal_qty != decimal_qty.to_integral_value()
                or decimal_qty > 9_223_372_036_854_775_807
                or not decimal_price.is_finite() or decimal_price < 0
                or (decimal_qty > 0 and decimal_price <= 0)):
            raise ValueError("broker stop fill quantity or price is inconsistent")
        parsed_price = float(decimal_price)
        if not math.isfinite(parsed_price):
            raise ValueError("broker stop fill price overflows numeric range")
        return int(decimal_qty), parsed_price

    def _held_beyond_siblings(
        self, net: Dict[tuple, int], pos: LivePosition
    ) -> Optional[int]:
        """Broker quantity attributable to this Snapback position, or unknown."""
        from app.services import snapback_reconciliation as recon

        key = (pos.option_exchange.upper(), pos.product.upper(), pos.option_symbol.upper())
        broker_held = int(net.get(key, 0))
        if broker_held < 0:
            # A net-short broker bucket is already a critical reconciliation
            # failure. Preserve the negative reading for Snapback's reconciliation
            # paths, but never let it authorize another SELL (callers clamp
            # sellable quantity at zero or treat it as not held).
            return broker_held
        current_remaining = max(0, int(pos.option_qty) - int(pos.option_exit_qty))
        return recon.attributable_long_quantity(
            pos.uid,
            pos.account_id,
            exchange=pos.option_exchange,
            product=pos.product,
            symbol=pos.option_symbol,
            current_quantity=current_remaining,
            broker_quantity=broker_held,
        )

    def _book_stop_fills(self, pos: LivePosition, fills: List["tuple[str, int, float]"],
                         *, exchange_ts_by_order: Optional[Dict[str, int]] = None) -> LivePosition:
        """Count the stop's own filled order(s) once, and hand what is left to the market exit.

        The broker stop is spent whatever it filled, so it is cleared here. Units beyond what
        this position still held are not booked into it (they surface as a short at the broker).
        """
        # A triggered broker stop cannot erase a possibly outstanding
        # canonical market-exit order. That order may still execute after
        # this stop and must retain its recovery identity.
        if pos.option_exit_order_id or pos.option_exit_requested:
            self._flag_reconciling(pos, "stop_and_market_exit_overlap")
            raise RuntimeError("broker stop overlaps unresolved canonical exit")

        # Never silently cap an actual broker SELL at our logical position size.
        # Any excess means the account may already be short; pausing for
        # reconciliation is mandatory before clearing this stop's identity.
        pending = [(oid, qty) for oid, qty, _ in fills
                   if oid not in pos.option_exit_booked_ids]
        try:
            total = pos.option_qty
            exited = pos.option_exit_qty
            if (type(total) is not int or type(exited) is not int
                    or not 0 <= exited <= total
                    or any(not isinstance(oid, str) or not oid.strip()
                           or type(qty) is not int or qty <= 0
                           for oid, qty in pending)
                    or len({oid for oid, _ in pending}) != len(pending)
                    or sum(qty for _, qty in pending) > total - exited):
                raise ValueError("stop fill may exceed position's remaining option units")
        except (TypeError, ValueError) as exc:
            self._flag_reconciling(pos, "broker_stop_exceeds_owned_option_units")
            raise RuntimeError("broker stop fill requires short exposure reconciliation") from exc

        for oid, qty, price in fills:
            if not self._book_exit(pos, leg="option", order_id=oid, filled=qty, average=price, source="gtt",
                                   exchange_ts_ms=(exchange_ts_by_order or {}).get(oid, 0)):
                self._flag_reconciling(pos, "stop_fill_accounting_pending")
                raise RuntimeError("stop fill accounting must commit before position projection")

        def apply(p: LivePosition) -> None:
            if p.option_exit_order_id or p.option_exit_requested:
                raise ValueError("canonical exit started while booking broker stop")
            for oid, qty, price in fills:
                if oid in p.option_exit_booked_ids:
                    continue
                if (type(p.option_qty) is not int or type(p.option_exit_qty) is not int
                        or qty > p.option_qty - p.option_exit_qty):
                    raise ValueError("concurrent broker stop overfill: short exposure possible")
                p.option_exit_qty += qty
                p.option_exit_value += float(qty) * float(price)
                p.option_exit_booked_ids.append(oid)
            p.gtt_id = 0
            p.gtt_qty = 0
            p.gtt_stop = 0.0
            p.protection_state = PROTECTION_NONE
            p.option_exit_order_id = ""
            p.option_exit_requested = 0
            p.stop_fill_pending_ms = 0
            if p.option_exit_qty < p.option_qty:
                p.exit_reason = "BROKER_STOP_UNFILLED"

        try:
            return self.store.mutate(pos.position_id, apply) or pos
        except Exception as exc:  # noqa: BLE001 - do not discard a concurrent broker order
            self._flag_reconciling(pos, "stop_fill_registry_conflict")
            log.critical("Snapback broker-stop booking requires reconciliation: %s", exc)
            raise

    async def _settle_broker_stop(self, client: Any, pos: LivePosition) -> str:
        """The broker's stop fired: find ITS order, book it, then close the hedge.

        ``done``        the stop's SELL filled and is booked;
        ``market_exit`` the stop's SELL never (fully) filled (the book gapped through its
                        limit), it was cancelled, and the normal MARKET exit takes over;
        anything else   is a state to retry (and, repeated, to escalate).

        Only the stop's own child order is read: a position's earlier exit orders and other
        people's SELLs on the same contract are not evidence of what the stop sold.
        """
        try:
            orders = await client.get_orders()
        except Exception as exc:  # noqa: BLE001
            log.warning("Snapback exit: order book unreadable: %s", exc)
            return "stop_fill_unverified"
        own = await self._stop_child_orders(client, pos, orders)
        net = await _broker_net(client)
        if own is None:
            # The trigger fired and its order cannot be identified. Nothing is booked on a
            # guess; an option that is demonstrably gone is the one case that still ends.
            if net is not None:
                held = self._held_beyond_siblings(net, pos)
                if held == 0:
                    return self._book_if_proven_gone(pos)
            return "stop_fill_unverified"

        fills = []
        for order in own:
            status = str(order.get("status") or "").upper()
            try:
                qty, price = self._filled(order)
                if status == "COMPLETE" and qty == 0:
                    raise ValueError("COMPLETE stop has no confirmed filled quantity")
            except ValueError as exc:
                log.critical("Snapback cannot verify broker stop fill: %s", exc)
                engage_kill_switch("Snapback broker stop fill evidence requires reconciliation", pos.uid)
                return "stop_fill_unverified"
            if qty > 0 and price > 0 and (status == "COMPLETE" or status in _ORDER_TERMINAL):
                fills.append((str(order.get("order_id") or ""), qty, price))
        live = [o for o in own if str(o.get("status") or "").upper() not in _ORDER_TERMINAL]
        if live:
            return await self._stop_fill_pending(client, pos, own)
        if not fills and net is not None:
            held = self._held_beyond_siblings(net, pos)
            if held == 0:
                # The stop sold nothing and this position's option is gone all the
                # same: an unpriced exit, and the hedge still has to close, once
                # the broker book has said so repeatedly.
                return self._book_if_proven_gone(pos)
        from app.services.kite_engine.execution_lifecycle import exchange_ms
        updated = self._book_stop_fills(pos, fills, exchange_ts_by_order={
            str(o.get("order_id") or ""): exchange_ms(o) for o in own
        })
        return "done" if updated.option_exit_qty >= updated.option_qty else "market_exit"

    def _book_if_proven_gone(self, pos: LivePosition) -> str:
        """The broker's book shows none of the option and no fill explains it.

        An unpriced booking cannot be undone, so it waits for the shortfall to persist.
        """
        if not self._note_shortfall(pos, "option"):
            return "option_missing_unconfirmed"
        self._book_unpriced_option_exit(
            pos, quantity=pos.option_qty - int(pos.option_exit_qty), available=0)
        return "done"

    async def _stop_fill_pending(self, client: Any, pos: LivePosition,
                                 own: List[Dict[str, Any]]) -> str:
        """The stop fired and its own SELL has not filled. Never wait on it forever.

        A GTT stop sells with a LIMIT a little under its trigger; if the book gapped
        through that limit the order rests unfilled while the position keeps losing.
        Alert at once; after the grace period cancel that order (confirmed) and let
        the ordinary MARKET exit sell what is still held. Only the stop's own order is
        ever cancelled.
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
                  key=f"snapback:{pos.position_id}:STOPPENDING", severity="CRITICAL",
                  uid=pos.uid)
            return "stop_fill_pending"
        if (now_ms - started) / 1000.0 < STOP_FILL_GRACE_S:
            return "stop_fill_pending"

        from app.services.snapback_family_mode import INTENT_CANCEL, canonical_broker_capability

        own_ids = {str(o.get("order_id") or "") for o in own} - {""}
        live = [o for o in own if str(o.get("status") or "").upper() not in _ORDER_TERMINAL]
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
            book = await client.get_orders()
        except Exception as exc:  # noqa: BLE001
            log.warning("Snapback exit: order book unreadable after cancel: %s", exc)
            return "stop_cancel_unconfirmed"
        after = [o for o in book or [] if isinstance(o, dict)
                 and str(o.get("order_id") or "") in own_ids]
        if (len(after) < len(own_ids)
                or any(str(o.get("status") or "").upper() not in _ORDER_TERMINAL for o in after)):
            return "stop_cancel_unconfirmed"

        # Everything the stop's own order did fill is real: book it before selling the rest.
        fills = []
        for order in after:
            try:
                qty, price = self._filled(order)
                if str(order.get("status") or "").upper() == "COMPLETE" and qty == 0:
                    raise ValueError("COMPLETE stop has no confirmed filled quantity")
            except ValueError as exc:
                log.critical("Snapback stop fill after cancel is unverified: %s", exc)
                engage_kill_switch("Snapback cancelled stop fill requires reconciliation", pos.uid)
                return "stop_cancel_unconfirmed"
            if qty > 0 and price > 0:
                fills.append((str(order.get("order_id") or ""), qty, price))
        from app.services.kite_engine.execution_lifecycle import exchange_ms
        updated = self._book_stop_fills(pos, fills, exchange_ts_by_order={
            str(o.get("order_id") or ""): exchange_ms(o) for o in after
        })
        alert("STOP ORDER CANCELLED, SELLING AT MARKET", pos.option_symbol,
              detail_line(qty=max(0, updated.option_qty - updated.option_exit_qty),
                          notes=["the broker stop's sell did not fill; exiting at market"]),
              key=f"snapback:{pos.position_id}:STOPMARKET", severity="WARNING",
              uid=pos.uid)
        return "done" if updated.option_exit_qty >= updated.option_qty else "market_exit"


# =========================================================================
# Operator / safety entry point
# =========================================================================

async def flatten_all(uid: str, client: Any, *, reason: str = "operator") -> Dict[str, Any]:
    """Square off every Snapback position (and retire entries not yet sent).

    The shared flatten API for Square-Off All and the kill switch. Serialised with
    the dispatcher's own cycle for ``uid`` so it never races a normal exit; see
    :meth:`SnapbackDispatcher.flatten_all`. Returns
    ``{"closed": int, "failed": list[str], "skipped": list[str]}``.
    """
    from app.services.snapback_dispatcher import get_dispatcher

    return await get_dispatcher().flatten_all(uid, client, reason=reason)
