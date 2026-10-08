"""The Snapback live dispatcher: the one loop that turns signals into live orders.

Before this, ``SnapbackLiveExecutor`` had no caller and ``PlanStore.publish`` had
no producer, so a Snapback signal could be armed on the board and nothing would
ever happen. The dispatcher closes that loop and owns, in one place:

    scan rows  ->  fresh signal?  ->  plan (sized, hedged, funded)  ->  publish
        ->  arm  ->  executor (revalidate, reconcile, risk, option, hedge, stop)
        ->  position registry  ->  exit manager (stop, trail, target, time, hedge)

Origination is allowed only when ALL of these hold, each reported as its own short
reason code so "why did nothing trade?" is always answerable:

    engine_disabled      the strategy switch is off
    auto_off             per-strategy AUTO or the shared master AUTO is off
    kill_switch          STERLING_LIVE_EXECUTION is off
    lane_*               the (snapback, mode) lane may not originate
    safe_mode / ...      the SafetySupervisor refuses new exposure (safe mode, recovery
                         state, unresolved intents, daily-loss halt, external exposure)
    session_* / entry_*  outside the market session or the entry cutoff
    system_not_healthy / broker_not_reconciled   the retained family gate
    no_broker_session    no connected, authenticated Kite account

Exits, protection and reconciliation run on every cycle regardless of any of the
above: a halt must never leave a position unmanaged.

Manual arming from the UI goes through the same plan producer and the same
executor, so a click and the loop can never take different paths.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))

#: Seconds between dispatcher cycles. Exits and protection are checked this often.
CYCLE_INTERVAL_S = 15.0
#: A row set older than this is rescanned before the dispatcher trades from it.
RESCAN_AGE_S = 300.0
#: Cached instrument masters are reused for this long.
INSTRUMENT_TTL_S = 3600.0
#: Plans are tried this many at most per cycle.
MAX_ORIGINATIONS_PER_CYCLE = 1


# --------------------------------------------------------------------------
# Switches and gates
# --------------------------------------------------------------------------

def auto_execute(uid: str, cfg: Any = None) -> bool:
    """Per-strategy AUTO and the shared master AUTO, both required."""
    try:
        from app.services import snapback
        from app.services.kite_engine import state as engine_state

        cfg = cfg or snapback.get_config(uid)
        shared = bool(getattr(engine_state.get_config(uid), "auto_execute", False))
        return shared and bool(getattr(cfg, "auto_execute", False))
    except Exception as exc:  # noqa: BLE001
        log.error("snapback dispatcher: auto state unavailable for %s: %s", uid, exc)
        return False


def origination_blockers(uid: str, cfg: Any, *, account_id: str = "", auto: bool = True,
                         now: Optional[datetime] = None) -> List[str]:
    """Every reason new exposure may not be opened right now, most fundamental first.

    Empty means origination may proceed. Reads durable state only; never the broker.
    """
    from app.core import lane_registry
    from app.services.snapback_plan_producer import _HORIZON_OF_LANE, lane_for

    out: List[str] = []
    if not bool(getattr(cfg, "enabled", False)):
        out.append("engine_disabled")
    if auto and not auto_execute(uid, cfg):
        out.append("auto_off")
    if not lane_registry.LIVE_EXECUTION_ENABLED:
        out.append("kill_switch")

    lane = lane_for(cfg)
    if lane is None:
        out.append("lane_not_supported")
    else:
        try:
            decision = lane_registry.may_originate("snapback", _HORIZON_OF_LANE[lane])
            if not decision.allowed:
                out.append(f"lane_{decision.reason.lower()}")
        except Exception as exc:  # noqa: BLE001
            out.append(f"lane_unreadable:{type(exc).__name__}")

    try:
        from app.services.snapback_readiness import safety_blockers

        out.extend(b for b in safety_blockers(uid, account_id or "default") if b not in out)
    except Exception as exc:  # noqa: BLE001
        out.append(f"safety_unreadable:{type(exc).__name__}")

    try:
        from app.services.kite_engine.market_hours import entry_block_reason

        blocked = entry_block_reason(now or datetime.now(_IST), exchange="NFO",
                                     cash_signal=True, buffer_minutes=5)
        if blocked:
            out.append(blocked)
    except Exception as exc:  # noqa: BLE001
        out.append(f"session_unreadable:{type(exc).__name__}")

    try:
        from app.services.snapback_readiness import gate_blockers

        out.extend(b for b in gate_blockers(uid) if b not in out)
    except Exception as exc:  # noqa: BLE001
        out.append(f"gate_unreadable:{type(exc).__name__}")
    return out


# --------------------------------------------------------------------------
# Dispatcher
# --------------------------------------------------------------------------

class SnapbackDispatcher:
    def __init__(self, *, plan_store: Any = None, positions: Any = None,
                 exit_manager: Any = None, executor_factory: Optional[Callable[..., Any]] = None,
                 client_factory: Optional[Callable[[str], Any]] = None,
                 rows_provider: Optional[Callable[[str], List[Dict[str, Any]]]] = None,
                 scanner: Optional[Callable[[str], Any]] = None,
                 clock: Optional[Callable[[], datetime]] = None) -> None:
        self._plan_store = plan_store
        self._positions = positions
        self._exit_manager = exit_manager
        self._executor_factory = executor_factory
        self._client_factory = client_factory
        self._rows_provider = rows_provider
        self._scanner = scanner
        self._clock = clock
        self._locks: Dict[str, asyncio.Lock] = {}
        self._instruments: Dict[str, tuple] = {}
        self._status: Dict[str, Dict[str, Any]] = {}
        self._refusals: Dict[tuple, float] = {}
        self._task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------- plumbing

    @property
    def plan_store(self):
        if self._plan_store is None:
            from app.services.snapback_plan_store import shared_store

            self._plan_store = shared_store()
        return self._plan_store

    @property
    def positions(self):
        if self._positions is None:
            from app.services import snapback_live_positions

            self._positions = snapback_live_positions.default_store()
        return self._positions

    @property
    def exit_manager(self):
        if self._exit_manager is None:
            from app.services.snapback_live_exits import SnapbackExitManager

            self._exit_manager = SnapbackExitManager(positions=self.positions)
        return self._exit_manager

    def now(self) -> datetime:
        return self._clock() if self._clock else datetime.now(_IST)

    def _lock(self, uid: str) -> asyncio.Lock:
        return self._locks.setdefault(uid, asyncio.Lock())

    def _executor(self, uid: str, client: Any = None):
        if self._executor_factory is not None:
            return self._executor_factory(uid)
        from app.services.snapback_live_wiring import build_production_executor

        def resolve(_uid: str, account_id: str):
            """The client this cycle already authenticated, only for its own account."""
            if client is not None and str(getattr(client, "_account_id", "")) == str(account_id):
                return client
            from app.services.snapback_live_executor import default_broker_client

            return default_broker_client(_uid, account_id)

        return build_production_executor(uid, plan_store=self.plan_store,
                                         positions=self.positions, clock=self._clock,
                                         broker_client_fn=resolve)

    async def _client(self, uid: str):
        if self._client_factory is not None:
            found = self._client_factory(uid)
            return (await found) if hasattr(found, "__await__") else found
        from app.services.exchanges.kite import accounts

        acct = accounts.get_active(uid)
        if not acct or not getattr(acct, "connected", False):
            return None
        return await accounts.acquire_client(acct)

    def _rows(self, uid: str) -> List[Dict[str, Any]]:
        if self._rows_provider is not None:
            return list(self._rows_provider(uid) or [])
        from app.services import snapback

        return list(snapback.status(uid).rows or [])

    def _record(self, uid: str, state: str, **info: Any) -> Dict[str, Any]:
        entry = {"state": state, "at_ms": int(time.time() * 1000), **info}
        self._status[uid] = {**self._status.get(uid, {}), **entry}
        return entry

    def status(self, uid: str) -> Dict[str, Any]:
        return dict(self._status.get(uid, {"state": "not_started"}))

    async def _instrument_rows(self, client: Any, exchange: str) -> list:
        cached = self._instruments.get(exchange)
        if cached and time.time() - cached[0] < INSTRUMENT_TTL_S:
            return cached[1]
        rows = await client.search_instruments("", exchange, limit=1_000_000)
        self._instruments[exchange] = (time.time(), list(rows or []))
        return self._instruments[exchange][1]

    # ---------------------------------------------------------------- cycle

    async def cycle(self, uid: str) -> Dict[str, Any]:
        """One pass: reconcile, manage exits, recover in-flight plans, originate."""
        async with self._lock(uid):
            return await self._cycle_locked(uid)

    async def _cycle_locked(self, uid: str) -> Dict[str, Any]:
        from app.services import snapback
        from app.services.snapback_live_wiring import reconcile_book

        cfg = snapback.get_config(uid)
        has_positions = bool(self.positions.open_positions(uid))
        client = await self._client(uid)
        if client is None:
            return self._record(uid, "no_broker_session", blockers=["no_broker_session"],
                                managed=len(self.positions.open_positions(uid)))
        account_id = str(getattr(client, "_account_id", "") or "")
        if not account_id:
            return self._record(uid, "blocked", blockers=["account_identity_unavailable"])

        if has_positions:
            await reconcile_book(client, uid, account_id, store=self.positions)

        # Exits, protection and recovery never wait for a gate.
        exits = await self.exit_manager.tick(uid, client)
        await self._recover(uid, client, cfg)

        if not cfg.enabled:
            return self._record(uid, "engine_disabled", blockers=["engine_disabled"],
                                managed=exits["managed"])
        if not auto_execute(uid, cfg):
            return self._record(uid, "auto_off", blockers=["auto_off"], managed=exits["managed"])

        blockers = origination_blockers(uid, cfg, account_id=account_id, auto=True, now=self.now())
        if blockers:
            return self._record(uid, "blocked", blockers=blockers, managed=exits["managed"])

        await self._rescan_if_stale(uid, cfg)
        entries = 0
        rows = [r for r in self._rows(uid) if r.get("state") == "armed"]
        rows.sort(key=lambda r: (str(r.get("strength")) != "STRONG", -abs(float(r.get("stretch") or 0))))
        refusals: Dict[str, str] = {}
        for row in rows:
            if entries >= MAX_ORIGINATIONS_PER_CYCLE:
                break
            outcome = await self._originate(uid, client, cfg, row, manual=False)
            if outcome.get("entered"):
                entries += 1
            elif outcome.get("reason"):
                refusals[str(row.get("signal_id"))] = outcome["reason"]
        return self._record(uid, "ready", blockers=[], managed=exits["managed"],
                            entries=entries, candidates=len(rows), refusals=refusals)

    async def _rescan_if_stale(self, uid: str, cfg: Any) -> None:
        from app.services import snapback
        from app.services.snapback_plan_producer import LANE_SCALP, lane_for

        st = snapback.status(uid)
        age_s = (snapback.ist_now_ms() - int(st.last_scan_ms or 0)) / 1000.0
        limit = (max(60.0, float(cfg.scalp_timeframe_minutes) * 60.0)
                 if lane_for(cfg) == LANE_SCALP else RESCAN_AGE_S)
        if st.scanning or age_s < limit:
            return
        try:
            scan = self._scanner or snapback.scan_once
            await scan(uid)
        except Exception as exc:  # noqa: BLE001
            log.warning("snapback dispatcher: rescan failed for %s: %s", uid, exc)

    # ------------------------------------------------------------- origination

    async def _originate(self, uid: str, client: Any, cfg: Any, row: Dict[str, Any], *,
                         manual: bool) -> Dict[str, Any]:
        from app.services.canonical_strategy_entry import available_entry_capital
        from app.services.snapback_identity import build_sha
        from app.services.snapback_plan_producer import PlanContext, build_plan, plan_id_for
        from app.services.snapback_plan_store import PlanConflictError

        signal_id = str(row.get("signal_id") or "")
        plan_id = plan_id_for(signal_id)
        existing = self.plan_store.get(plan_id)
        if existing is not None:
            # One plan per signal, forever. A restart or rescan lands here.
            return {"entered": False, "reason": f"plan_exists:{existing.get('status')}",
                    "plan_id": plan_id}

        account_id = str(getattr(client, "_account_id", "") or "")

        async def instruments(exchange: str) -> list:
            return await self._instrument_rows(client, exchange)

        ctx = PlanContext(
            uid=uid, account_id=account_id, client=client, cfg=cfg, now=self.now(),
            available_capital=await available_entry_capital(client) or None,
            instruments=instruments,
            open_positions=self.positions.open_positions(uid, account_id),
            runtime_sha=build_sha(), manual=manual,
        )
        outcome = await build_plan(row, ctx)
        if not outcome.ok or outcome.plan is None:
            self._note_refusal(signal_id, outcome.reason, outcome.detail)
            return {"entered": False, "reason": outcome.reason, "reasons": outcome.reasons,
                    "detail": outcome.detail}

        try:
            published = self.plan_store.publish(**outcome.plan)
        except PlanConflictError:
            return {"entered": False, "reason": "plan_exists:race", "plan_id": plan_id}
        armed_by = f"manual:{uid}" if manual else "auto:snapback-dispatcher"
        try:
            self.plan_store.arm(
                plan_id, expected_revision=int(published.get("revision") or 1),
                idempotency_key=f"{'manual' if manual else 'auto'}:{signal_id}",
                armed_by=armed_by, mode="LIVE", now=self.now())
        except Exception as exc:  # noqa: BLE001
            log.error("snapback dispatcher: arming %s failed: %s", plan_id, exc)
            return {"entered": False, "reason": "arm_failed", "plan_id": plan_id}

        result = await self._execute(uid, client, plan_id)
        return {"entered": result.status == "OPEN", "plan_id": plan_id, "status": result.status,
                "reason": result.reasons[0] if result.reasons else "", "result": result}

    def _note_refusal(self, signal_id: str, reason: str, detail: str) -> None:
        key = (signal_id, reason)
        if key in self._refusals:
            return
        self._refusals[key] = time.time()
        log.info("snapback dispatcher: %s not traded: %s %s", signal_id, reason, detail)

    async def publish_from_signal(self, uid: str, signal_id: str) -> Dict[str, Any]:
        """Manual path, step one: build and publish (not arm) the plan for a board row."""
        from app.services import snapback
        from app.services.canonical_strategy_entry import available_entry_capital
        from app.services.snapback_identity import build_sha
        from app.services.snapback_plan_producer import PlanContext, build_plan, plan_id_for
        from app.services.snapback_plan_store import PlanConflictError

        async with self._lock(uid):
            plan_id = plan_id_for(signal_id)
            existing = self.plan_store.get(plan_id)
            if existing is not None:
                return {"ok": True, "plan_id": plan_id, "status": existing.get("status"),
                        "revision": existing.get("revision"), "existing": True}
            row = next((r for r in self._rows(uid) if r.get("signal_id") == signal_id), None)
            if row is None:
                return {"ok": False, "reasons": ["signal_not_found"]}
            client = await self._client(uid)
            if client is None:
                return {"ok": False, "reasons": ["no_broker_session"]}
            account_id = str(getattr(client, "_account_id", "") or "")
            cfg = snapback.get_config(uid)

            async def instruments(exchange: str) -> list:
                return await self._instrument_rows(client, exchange)

            ctx = PlanContext(
                uid=uid, account_id=account_id, client=client, cfg=cfg, now=self.now(),
                available_capital=await available_entry_capital(client) or None,
                instruments=instruments,
                open_positions=self.positions.open_positions(uid, account_id),
                runtime_sha=build_sha(), manual=True)
            outcome = await build_plan(row, ctx)
            if not outcome.ok or outcome.plan is None:
                return {"ok": False, "reasons": outcome.reasons, "detail": outcome.detail}
            try:
                published = self.plan_store.publish(**outcome.plan)
            except PlanConflictError:
                return {"ok": False, "reasons": ["plan_exists"]}
            return {"ok": True, "plan_id": plan_id, "status": published.get("status"),
                    "revision": published.get("revision"),
                    "preview": plan_preview(published)}

    # ---------------------------------------------------------------- execution

    async def execute_plan(self, uid: str, plan_id: str) -> Any:
        """Run one ARMED (or RECONCILING) plan through the executor. Shared by the
        loop, by recovery and by the manual arm route."""
        async with self._lock(uid):
            client = await self._client(uid)
            if client is None:
                from app.services.snapback_live_executor import LiveExecutionResult

                return LiveExecutionResult(status="REFUSED", plan_id=plan_id,
                                           reasons=["no_broker_session"])
            return await self._execute(uid, client, plan_id)

    async def _execute(self, uid: str, client: Any, plan_id: str):
        from app.services.snapback_live_wiring import reconcile_book

        account_id = str(getattr(client, "_account_id", "") or "")
        # Fresh book evidence immediately before the executor asks for it.
        await reconcile_book(client, uid, account_id, store=self.positions)
        executor = self._executor(uid, client)
        try:
            result = await executor.execute(plan_id)
        except Exception as exc:  # noqa: BLE001 - an exception after a send is an unknown outcome
            log.exception("snapback dispatcher: executor raised for %s: %s", plan_id, exc)
            from app.services.snapback_live_executor import LiveExecutionResult
            from app.services.snapback_live_wiring import engage_kill_switch

            engage_kill_switch(f"Snapback executor failed for {plan_id}: {exc}", uid)
            return LiveExecutionResult(status="RECONCILING", plan_id=plan_id,
                                       reasons=[f"executor_exception:{type(exc).__name__}"],
                                       reconcile_first=True, stop_new_entries=True)
        self._after_execution(uid, plan_id, result)
        return result

    def _after_execution(self, uid: str, plan_id: str, result: Any) -> None:
        from app.services.snapback_live_wiring import alert, detail_line, engage_kill_switch

        plan = self.plan_store.get(plan_id) or {}
        title = str(plan.get("option_symbol") or plan_id)
        self._record(uid, "executed", last_execution={
            "plan_id": plan_id, "status": result.status, "reasons": list(result.reasons)})
        if result.status == "OPEN":
            pos = self.positions.get(plan_id)
            if pos is not None:
                notes = []
                if pos.has_hedge:
                    notes.append(f"Hedge: {pos.hedge_side.lower()} {pos.hedge_qty} {pos.hedge_symbol}"
                                 f" @ ₹{pos.hedge_entry_price:,.2f}")
                if pos.lane == "swing":
                    notes.append(f"Time stop after {int(pos.rules.get('hold_days') or 0)} sessions")
                else:
                    notes.append(f"Target ₹{float(pos.rules.get('target_premium') or 0):,.2f}")
                alert("ENTRY FILLED", pos.option_symbol,
                      detail_line(entry=pos.option_entry_price, sl=pos.stop_premium,
                                  qty=pos.option_qty, capital=pos.option_entry_price * pos.option_qty,
                                  notes=notes),
                      key=f"snapback:{plan_id}:ENTRY")
            return
        if result.status in ("HEDGE_FAILED", "PROTECTION_FAILED", "RECONCILING"):
            alert("ENTRY INCOMPLETE", title,
                  detail_line(qty=int(plan.get("option_quantity") or 0),
                              notes=[f"{result.status.replace('_', ' ').lower()}: "
                                     f"{', '.join(result.reasons) or 'see log'}",
                                     "new entries halted" if result.stop_new_entries else ""]),
                  key=f"snapback:{plan_id}:INCOMPLETE:{result.status}:{int(time.time() // 300)}",
                  severity="CRITICAL")
            if result.stop_new_entries:
                engage_kill_switch(f"Snapback entry {plan_id} incomplete: {result.status}", uid)

    # ------------------------------------------------------------------ recovery

    async def _recover(self, uid: str, client: Any, cfg: Any) -> None:
        """Finish or retire plans a restart or a transient refusal left in flight."""
        account_id = str(getattr(client, "_account_id", "") or "")
        for plan in self._plans_in_flight(uid, account_id):
            plan_id = str(plan.get("plan_id"))
            try:
                status = str(plan.get("status"))
                if status == "ARMED":
                    if self._submission_started(plan):
                        # The process died (or a leg raised) after an order may have
                        # gone out. Only the recovery path reads that evidence.
                        self.plan_store.mark_reconciling(
                            plan_id, reason="restart_after_submission")
                    elif self._expired(plan):
                        self.plan_store.cancel(plan_id, reason="expired_unexecuted",
                                               actor="snapback-dispatcher")
                        continue
                await self._execute(uid, client, plan_id)
            except Exception as exc:  # noqa: BLE001
                log.error("snapback dispatcher: recovery of %s failed: %s", plan_id, exc)

    def _plans_in_flight(self, uid: str, account_id: str) -> List[Dict[str, Any]]:
        lister = getattr(self.plan_store, "list_in_flight", None)
        if not callable(lister):
            return []
        return [p for p in lister(owner_uid=uid, account_id=account_id)]

    @staticmethod
    def _expired(plan: Dict[str, Any]) -> bool:
        try:
            return datetime.now(timezone.utc) >= datetime.fromisoformat(str(plan.get("expires_at")))
        except (TypeError, ValueError):
            return True

    @staticmethod
    def _submission_started(plan: Dict[str, Any]) -> bool:
        try:
            from app.services.kite_engine import order_journal
            from app.services.snapback_plan_producer import generation_id

            key = order_journal.make_key(
                uid=str(plan.get("owner_uid") or ""), account_id=str(plan.get("account_id") or ""),
                strategy_id="snapback", generation_id=str(plan.get("policy_snapshot_hash") or generation_id()),
                signal_id=str(plan.get("opportunity_id") or ""),
                exchange=str(plan.get("option_exchange") or ""),
                symbol=str(plan.get("option_symbol") or ""), side="BUY")
            return order_journal.get(key) is not None
        except Exception:  # noqa: BLE001 - unreadable evidence is not "nothing was sent"
            return True

    # ------------------------------------------------------------------- loop

    async def run_forever(self, interval: float = CYCLE_INTERVAL_S) -> None:
        log.info("snapback live dispatcher started (every %ss)", interval)
        try:
            while True:
                try:
                    for uid in _kite_user_ids():
                        try:
                            await self.cycle(uid)
                        except Exception as exc:  # noqa: BLE001 - never kill the loop
                            log.exception("snapback dispatcher cycle failed for %s: %s", uid, exc)
                except Exception as exc:  # noqa: BLE001
                    log.warning("snapback dispatcher iteration error: %s", exc)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            log.info("snapback live dispatcher stopped")
            raise


def plan_preview(plan: Dict[str, Any]) -> Dict[str, Any]:
    """What an operator may see of a plan. Identity and numbers, never authority."""
    return {
        "plan_id": plan.get("plan_id"), "revision": plan.get("revision"),
        "underlying": plan.get("underlying"), "lane": plan.get("lane"),
        "option_symbol": plan.get("option_symbol"), "quantity": plan.get("option_quantity"),
        "limit_price": plan.get("limit_price"), "stop_premium": plan.get("stop_premium"),
        "target_premium": plan.get("target_premium") or None,
        "hedge_symbol": plan.get("futures_symbol") or None,
        "hedge_quantity": plan.get("target_futures_quantity") or None,
        "cash_required": plan.get("cash_required"), "margin_required": plan.get("margin_required"),
        "risk_amount": plan.get("risk_amount"), "expires_at": plan.get("expires_at"),
    }


def _kite_user_ids() -> List[str]:
    try:
        from app.services.exchanges.kite import accounts

        accounts.bootstrap()
        return sorted({a.user_id for a in accounts.all_accounts() if a.is_active})
    except Exception:  # noqa: BLE001
        return []


# --------------------------------------------------------------------------
# Process-wide instance and lifecycle
# --------------------------------------------------------------------------

_dispatcher: Optional[SnapbackDispatcher] = None


def get_dispatcher() -> SnapbackDispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = SnapbackDispatcher()
    return _dispatcher


def start(interval: float = CYCLE_INTERVAL_S) -> asyncio.Task:
    """Start the dispatcher loop if it is not already running."""
    dispatcher = get_dispatcher()
    if dispatcher._task is None or dispatcher._task.done():
        dispatcher._task = asyncio.create_task(
            dispatcher.run_forever(interval), name="snapback-live-dispatcher")
        log.info("Started snapback-live-dispatcher background task")
    return dispatcher._task


def is_running() -> bool:
    """Is the live dispatcher loop alive in this process?"""
    task = get_dispatcher()._task
    return task is not None and not task.done()


def stop() -> None:
    dispatcher = get_dispatcher()
    task = dispatcher._task
    if task is not None and not task.done():
        task.cancel()
    dispatcher._task = None
