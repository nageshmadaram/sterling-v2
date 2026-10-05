"""The production wiring of the Snapback live executor.

``SnapbackLiveExecutor`` is deliberately a pure ordering of steps with every
side effect injected. This module supplies the real ones, and each choice here is
a safety decision rather than plumbing:

* **Entry fills are read, not assumed.** The canonical service acknowledges an
  order; an acknowledgement is not a fill. The execution adapter below waits a
  bounded time for broker evidence, cancels an unfilled remainder once, and never
  resubmits. Only confirmed quantity is booked and hedged.
* **Every confirmed fill is booked twice, on purpose**: into the signed fill
  ledger (which the daily-loss breaker and the exit path's inventory proof read)
  and into Snapback's own position registry (which the exit manager and the
  broker-exposure observer read). The canonical journal's projection is only
  acknowledged once both succeeded, so a half-booked fill keeps blocking new
  entries instead of vanishing.
* **Protection is a broker-side stop**, placed from the confirmed fill. If it
  cannot be confirmed the kill switch is engaged (new entries stop; exits never
  do) and the position stays managed by the exit manager.
* **Reconciliation is Snapback-scoped**: Snapback's own book against the broker,
  plus unresolved Snapback intents. The account-wide questions (safe mode,
  recovery state, daily loss, foreign exposure) belong to the SafetySupervisor,
  which the canonical service asks twice.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from app.services import snapback_live_positions as live_positions
from app.services.snapback_live_positions import (
    ENTERING, OPEN, PROTECTION_ACTIVE, PROTECTION_FAILED, PROTECTION_NONE, LivePosition,
    LivePositionStore,
)

log = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))

#: How long an entry order is given to fill before the remainder is cancelled.
ENTRY_FILL_TIMEOUT_S = 5.0


def _plan_id_from_tag(tag: str) -> str:
    text = str(tag or "")
    return text.split(":", 1)[1] if text.startswith("snapback:") else ""


def engage_kill_switch(reason: str, uid: str = "default") -> None:
    """Stop NEW entries everywhere. Exits, protection and reconciliation keep running.

    The kill switch is account-wide and lives in the global control-plane scope
    (the same one every other strategy engages), so the user id is not part of it.
    """
    try:
        from app.services import live_safety

        live_safety.set_kill_switch(True, reason)
    except Exception as exc:  # noqa: BLE001 - the in-memory switch may already be set
        log.critical("Snapback live: kill switch could not be persisted (%s): %s", reason, exc)


def alert(event_type: str, title: str, detail: str, *, key: str, severity: str = "INFO") -> None:
    """One lifecycle alert through the shared durable outbox. Never raises."""
    try:
        from app.services import snapback_lifecycle_alerts as alerts

        alerts.emit(dedupe_key=key, event_type=event_type, title=title, detail=detail,
                    severity=severity)
    except Exception as exc:  # noqa: BLE001 - alerting must never alter trading
        log.warning("Snapback live alert not queued [%s]: %s", event_type, exc)


def detail_line(**fields: Any) -> str:
    try:
        from app.services import snapback_lifecycle_alerts as alerts

        return alerts.build_detail(**fields)
    except Exception:  # noqa: BLE001
        return " · ".join(f"{k} {v}" for k, v in fields.items() if v not in (None, ""))


# --------------------------------------------------------------------------
# Execution adapter: canonical submission + fill resolution + booking
# --------------------------------------------------------------------------

class SnapbackExecutionAdapter:
    """``submit_order`` with the executor's contract, backed by real fills.

    The executor reads ``status`` / ``success`` / ``filled_quantity`` from what
    this returns and never looks at the broker itself, so everything it must be
    able to trust is established here.
    """

    def __init__(self, *, plan_store: Any, positions: Optional[LivePositionStore] = None,
                 canonical: Any = None, fill_timeout_s: Optional[float] = None,
                 resolver: Optional[Callable[..., Any]] = None) -> None:
        self.plan_store = plan_store
        self.positions = positions
        self._canonical = canonical
        self.fill_timeout_s = fill_timeout_s
        self._resolver = resolver

    @property
    def canonical(self) -> Any:
        if self._canonical is None:
            from app.services.execution_service import CanonicalExecutionService

            self._canonical = CanonicalExecutionService()
        return self._canonical

    def _store(self) -> LivePositionStore:
        return self.positions or live_positions.default_store()

    async def submit_order(self, request, broker_client=None, risk_approval=None,
                           risk_approved=False):
        from app.services.execution_service import ExecutionResult

        result = await self.canonical.submit_order(
            request, broker_client=broker_client, risk_approval=risk_approval,
            risk_approved=risk_approved,
        )
        if not result.success or not result.order_id:
            return result

        from app.services import canonical_fill_reconciliation as fills

        resolve = self._resolver or fills.resolve_entry_fill
        reference = float(getattr(request, "price", 0.0) or 0.0) or float(
            (request.payload or {}).get("limit_price") or 0.0) or 1.0
        try:
            resolution = await resolve(
                uid=request.uid, client=broker_client, order_id=result.order_id,
                expected_quantity=int(request.quantity), reference_price=reference,
                timeout_s=(self.fill_timeout_s if self.fill_timeout_s is not None
                           else ENTRY_FILL_TIMEOUT_S),
            )
        except Exception as exc:  # noqa: BLE001 - outcome unknown: never resend
            log.error("Snapback live: fill resolution failed for %s: %s", result.order_id, exc)
            engage_kill_switch(f"Snapback entry fill unresolved for {request.symbol}: {exc}",
                               request.uid)
            return ExecutionResult(
                success=False, status="UNKNOWN", intent_key=result.intent_key,
                order_id=result.order_id, error=f"fill resolution failed: {exc}",
            )

        filled = int(resolution.filled_quantity or 0)
        average = float(resolution.average_price or 0.0)
        booked_ok = True
        if filled > 0:
            booked_ok = self._book_fill(request, resolution, result.order_id, filled, average)
            if booked_ok and resolution.projection_version:
                try:
                    fills.mark_projected(resolution)
                except Exception as exc:  # noqa: BLE001
                    log.error("Snapback live: projection ack failed for %s: %s",
                              result.order_id, exc)

        if resolution.reconciliation_required or (not resolution.settled and filled == 0):
            return ExecutionResult(
                success=False, status="UNKNOWN", intent_key=resolution.intent_key,
                order_id=result.order_id, filled_quantity=filled, filled_price=average,
                error=resolution.reason or "entry outcome unresolved",
            )
        if filled <= 0:
            status = str(resolution.status or "REJECTED").upper()
            return ExecutionResult(
                success=False, status="CANCELLED" if status == "CANCELLED" else "REJECTED",
                intent_key=resolution.intent_key, order_id=result.order_id,
                error=resolution.reason or f"order {status.lower()} with no fill",
            )
        if not resolution.settled:
            # Quantity exists but the order is still working: not terminal.
            return ExecutionResult(
                success=False, status="UNKNOWN", intent_key=resolution.intent_key,
                order_id=result.order_id, filled_quantity=filled, filled_price=average,
                error=resolution.reason or "entry order still working after the fill wait",
            )
        return ExecutionResult(
            success=True, status="FILLED" if filled >= int(request.quantity) else "FILLED_PARTIAL",
            intent_key=resolution.intent_key, order_id=result.order_id,
            filled_quantity=filled, filled_price=average,
        )

    # ------------------------------------------------------------- booking

    def _book_fill(self, request, resolution, order_id: str, filled: int, average: float) -> bool:
        """Ledger + registry. False means one of them did not land."""
        ok = True
        try:
            from app.services.kite_engine import fill_ledger

            applied = fill_ledger.record(
                account_id=request.account_id, uid=request.uid, symbol=request.symbol,
                exchange=request.exchange, side=request.side.upper(), order_id=order_id,
                cumulative_quantity=filled, cumulative_value=filled * average,
                source="entry", lot_size=int((request.payload or {}).get("lot_size") or 0),
            )
            if applied.reconciliation_required:
                ok = False
                log.critical("Snapback live: contradictory fill evidence for %s", request.symbol)
        except Exception as exc:  # noqa: BLE001
            ok = False
            log.critical("Snapback live: fill for %s not booked to the ledger: %s",
                         request.symbol, exc)

        try:
            self._record_leg(request, order_id, filled, average)
        except Exception as exc:  # noqa: BLE001
            ok = False
            log.critical("Snapback live: position registry write failed for %s: %s",
                         request.symbol, exc)
        if not ok:
            engage_kill_switch(
                f"Snapback fill for {request.symbol} could not be fully booked", request.uid)
        return ok

    def _record_leg(self, request, order_id: str, filled: int, average: float) -> None:
        plan_id = _plan_id_from_tag(request.tag)
        plan = self.plan_store.get(plan_id) if plan_id else None
        if plan is None:
            raise LookupError(f"plan for order tag {request.tag!r} not found")
        store = self._store()
        symbol = str(request.symbol)
        now_ms = int(time.time() * 1000)

        if symbol == str(plan.get("option_symbol")):
            rules = dict(plan.get("rules") or {})
            tick = float(plan.get("tick_size") or 0.05)
            stop = _initial_stop(rules, average, tick)
            existing = store.get(plan_id)
            if existing is None:
                today = datetime.now(_IST).date().isoformat()
                pos = LivePosition(
                    position_id=plan_id, uid=request.uid, account_id=request.account_id,
                    signal_id=str(plan.get("opportunity_id") or ""),
                    lane=str(plan.get("lane") or ""), underlying=str(plan.get("underlying") or ""),
                    side=str(plan.get("signal_side") or ""),
                    option_type=str(plan.get("option_type") or ""),
                    option_exchange=str(request.exchange), option_symbol=symbol,
                    option_token=int(plan.get("option_instrument_token") or 0),
                    lot_size=int(plan.get("lot_size") or 0), option_qty=int(filled),
                    option_entry_price=float(average), option_entry_order_id=str(order_id),
                    option_expiry=str(plan.get("option_expiry") or ""),
                    hedge_exchange=str(plan.get("futures_exchange") or ""),
                    hedge_symbol=str(plan.get("futures_symbol") or ""),
                    hedge_side=str(plan.get("hedge_side") or ""),
                    hedge_lot_size=int(plan.get("futures_lot_size") or 0),
                    product=str(plan.get("product") or "NRML"), tick_size=tick,
                    rules=rules, status=ENTERING, stop_premium=stop, desired_stop=stop,
                    high_water=float(average), phase="OPEN_SCALP", opened_ms=now_ms,
                    entry_day=today,
                )
                store.put(pos)
            else:
                def apply(pos: LivePosition) -> None:
                    pos.option_qty = int(filled)
                    pos.option_entry_price = float(average)
                    pos.option_entry_order_id = str(order_id)
                    pos.stop_premium = pos.stop_premium or stop
                    pos.desired_stop = max(pos.desired_stop, stop)

                store.mutate(plan_id, apply)
        elif symbol == str(plan.get("futures_symbol")):
            def apply_hedge(pos: LivePosition) -> None:
                pos.hedge_qty = int(filled)
                pos.hedge_entry_price = float(average)
                pos.hedge_entry_order_id = str(order_id)
                pos.hedge_side = str(request.side).upper()
                pos.hedge_exchange = str(request.exchange)
                pos.hedge_symbol = symbol

            if store.mutate(plan_id, apply_hedge) is None:
                raise LookupError(f"hedge filled before the option leg was recorded: {plan_id}")
        else:
            raise LookupError(f"order symbol {symbol} matches no leg of plan {plan_id}")


def _initial_stop(rules: Dict[str, Any], entry: float, tick: float) -> float:
    """The protective stop implied by the frozen rules and the CONFIRMED fill."""
    import math

    tick = tick if tick > 0 else 0.05
    if str(rules.get("lane")) == "scalp":
        raw = float(entry) - float(rules.get("stop_points") or 0.0)
    else:
        raw = float(entry) * (1.0 - float(rules.get("premium_stop_pct") or 35.0) / 100.0)
    return max(tick, round(math.floor(raw / tick + 1e-9) * tick, 2))


# --------------------------------------------------------------------------
# Protection (broker-side stop)
# --------------------------------------------------------------------------

_PROTECTION_LOCKS: Dict[str, "asyncio.Lock"] = {}


def _protection_lock(position_id: str):
    import asyncio

    return _PROTECTION_LOCKS.setdefault(position_id, asyncio.Lock())


class SnapbackProtection:
    """Places, moves and releases the broker-side stop for a Snapback option leg."""

    def __init__(self, *, positions: Optional[LivePositionStore] = None,
                 broker_client_fn: Optional[Callable[..., Any]] = None) -> None:
        self.positions = positions
        self.broker_client_fn = broker_client_fn

    def _store(self) -> LivePositionStore:
        return self.positions or live_positions.default_store()

    async def _client(self, uid: str, account_id: str):
        resolver = self.broker_client_fn
        if resolver is None:
            from app.services.snapback_live_executor import default_broker_client as resolver
        found = resolver(uid, account_id)
        if hasattr(found, "__await__"):
            found = await found
        return found

    async def place(self, *, plan_id: str, symbol: str, quantity: int, account_id: str) -> Dict[str, Any]:
        """The executor's ``protection_fn``."""
        store = self._store()
        pos = store.get(plan_id)
        if pos is None:
            return {"state": "REQUIRED", "reason": "position_not_recorded"}
        client = await self._client(pos.uid, account_id)
        if client is None:
            return {"state": "REQUIRED", "reason": "broker_client_unavailable"}
        state = await self.ensure(client, pos, int(quantity))
        return {"state": "ACTIVE" if state == PROTECTION_ACTIVE else "REQUIRED",
                "protection_id": store.get(plan_id).gtt_id if store.get(plan_id) else 0}

    async def ensure(self, client, pos: LivePosition, quantity: int) -> str:
        """Make the broker stop exist for ``quantity``; return the protection state.

        Serialised per position: the executor's recovery pass and the exit
        manager's retry can both arrive here, and two concurrent placements would
        arm two stops over one position (and sell it twice).
        """
        async with _protection_lock(pos.position_id):
            fresh = self._store().get(pos.position_id)
            return await self._ensure_locked(client, fresh or pos, quantity)

    async def _ensure_locked(self, client, pos: LivePosition, quantity: int) -> str:
        from app.services.kite_engine import protective_stop
        from app.services.snapback_family_mode import INTENT_PROTECT, canonical_broker_capability

        store = self._store()
        if str(getattr(client, "_account_id", "") or "") != str(pos.account_id):
            return self._record(store, pos, PROTECTION_FAILED, note="account_identity_mismatch")
        if quantity <= 0 or pos.desired_stop <= 0:
            return self._record(store, pos, PROTECTION_FAILED, note="nothing_to_protect")

        stop = float(pos.desired_stop)
        if pos.gtt_id and pos.gtt_qty == quantity and pos.protection_state == PROTECTION_ACTIVE:
            if abs(float(pos.gtt_stop) - stop) < 1e-9 or stop < float(pos.gtt_stop):
                return PROTECTION_ACTIVE   # never loosen a resting stop
            return await self._move(client, pos, quantity, stop)

        last = float(pos.option_entry_price or stop + pos.tick_size)
        try:
            with canonical_broker_capability(
                f"snapback-protect:{pos.account_id}:{pos.option_symbol}",
                intent=INTENT_PROTECT,
            ):
                if pos.gtt_id:
                    ok = await protective_stop.move_stop(
                        client, trigger_id=int(pos.gtt_id), tradingsymbol=pos.option_symbol,
                        exchange=pos.option_exchange, qty=quantity, trigger_premium=stop,
                        last_price=last, direction="long", target_premium=0.0,
                        product=pos.product)
                    gid = int(pos.gtt_id) if ok else 0
                else:
                    gid = await protective_stop.place_stop(
                        client, tradingsymbol=pos.option_symbol, exchange=pos.option_exchange,
                        qty=quantity, trigger_premium=stop, last_price=last,
                        direction="long", target_premium=0.0, product=pos.product)
        except Exception as exc:  # noqa: BLE001
            log.error("Snapback protection failed for %s: %s", pos.option_symbol, exc)
            gid = 0
        if not gid:
            return self._record(store, pos, PROTECTION_FAILED, note="broker_stop_not_confirmed")

        def apply(p: LivePosition) -> None:
            p.gtt_id = int(gid)
            p.gtt_stop = stop
            p.gtt_qty = int(quantity)
            p.protection_state = PROTECTION_ACTIVE
            if "unprotected" in p.flags:
                p.flags.remove("unprotected")

        store.mutate(pos.position_id, apply)
        alert("SL ARMED", pos.option_symbol,
              detail_line(entry=pos.option_entry_price, sl=stop, qty=quantity,
                          notes=[f"GTT #{int(gid)}"]),
              key=f"snapback:{pos.position_id}:SL:{stop:.2f}:{quantity}")
        return PROTECTION_ACTIVE

    async def _move(self, client, pos: LivePosition, quantity: int, stop: float) -> str:
        from app.services.kite_engine import protective_stop
        from app.services.snapback_family_mode import INTENT_PROTECT, canonical_broker_capability

        store = self._store()
        try:
            with canonical_broker_capability(
                f"snapback-protect:{pos.account_id}:{pos.option_symbol}",
                intent=INTENT_PROTECT,
            ):
                ok = await protective_stop.move_stop(
                    client, trigger_id=int(pos.gtt_id), tradingsymbol=pos.option_symbol,
                    exchange=pos.option_exchange, qty=quantity, trigger_premium=stop,
                    last_price=float(pos.high_water or pos.option_entry_price or stop),
                    direction="long", target_premium=0.0, product=pos.product)
        except Exception as exc:  # noqa: BLE001
            log.error("Snapback stop move failed for %s: %s", pos.option_symbol, exc)
            ok = False
        if not ok:
            # The OLD stop is still resting and still protects, just not at the
            # tighter level. That is degraded, not unprotected.
            alert("TSL NOT SYNCED", pos.option_symbol,
                  detail_line(sl=stop, qty=quantity, notes=["broker stop not moved; old stop stands"]),
                  key=f"snapback:{pos.position_id}:TSLFAIL:{stop:.2f}", severity="WARNING")
            return PROTECTION_ACTIVE

        def apply(p: LivePosition) -> None:
            p.gtt_stop = stop

        store.mutate(pos.position_id, apply)
        alert("TSL UPDATED", pos.option_symbol,
              detail_line(sl=stop, qty=quantity), key=f"snapback:{pos.position_id}:TSL:{stop:.2f}")
        return PROTECTION_ACTIVE

    @staticmethod
    def _record(store: LivePositionStore, pos: LivePosition, state: str, *, note: str = "") -> str:
        def apply(p: LivePosition) -> None:
            p.protection_state = state
            if state != PROTECTION_ACTIVE and "unprotected" not in p.flags:
                p.flags.append("unprotected")
            if note:
                p.note = note

        store.mutate(pos.position_id, apply)
        if state != PROTECTION_ACTIVE:
            alert("PROTECTION FAILED", pos.option_symbol,
                  detail_line(entry=pos.option_entry_price, qty=pos.option_qty,
                              notes=[f"broker stop not confirmed ({note or 'unknown'})",
                                     "new entries halted"]),
                  key=f"snapback:{pos.position_id}:PROTFAIL:{int(time.time() // 300)}",
                  severity="CRITICAL")
            engage_kill_switch(
                f"Snapback protection not confirmed for {pos.option_symbol}", pos.uid)
        return state


# --------------------------------------------------------------------------
# Snapback-scoped book reconciliation
# --------------------------------------------------------------------------

BOOK_TTL_S = 60.0


@dataclass
class BookSnapshot:
    account_id: str
    clean: bool
    observed_at: float
    mismatches: List[str] = field(default_factory=list)

    def fresh(self, now: Optional[float] = None, ttl: float = BOOK_TTL_S) -> bool:
        return 0 <= (now or time.time()) - self.observed_at <= ttl


_BOOKS: Dict[str, BookSnapshot] = {}


def latest_book(account_id: str) -> Optional[BookSnapshot]:
    return _BOOKS.get(str(account_id))


def live_book_state(account_id: str) -> Dict[str, Any]:
    """What the executor's ``reconciliation_fn`` reads. Unknown is neither clean nor fresh."""
    snap = latest_book(account_id)
    if snap is None:
        return {"clean": False, "fresh": False, "reason": "never_reconciled"}
    return {"clean": snap.clean, "fresh": snap.fresh(), "mismatches": list(snap.mismatches)}


def _net_by_key(raw: Any) -> Optional[Dict[tuple, int]]:
    if not isinstance(raw, dict) or not isinstance(raw.get("net"), list):
        return None
    out: Dict[tuple, int] = {}
    for row in raw["net"]:
        if not isinstance(row, dict):
            continue
        key = (str(row.get("exchange") or "").upper(),
               str(row.get("product") or "").upper(),
               str(row.get("tradingsymbol") or row.get("symbol") or "").upper())
        try:
            out[key] = out.get(key, 0) + int(float(row.get("quantity") or 0))
        except (TypeError, ValueError):
            return None
    return out


async def reconcile_book(client: Any, uid: str, account_id: str, *,
                         store: Optional[LivePositionStore] = None) -> BookSnapshot:
    """Snapback's own positions against the broker, plus unresolved Snapback intents."""
    store = store or live_positions.default_store()
    mismatches: List[str] = []
    try:
        reader = getattr(client, "get_positions_raw", None)
        if not callable(reader):
            # A transport with no position book (the in-process test double) has
            # nothing to compare against; only the journal can be checked.
            net: Optional[Dict[tuple, int]] = {}
            if getattr(client, "_is_paper", None) is not True:
                mismatches.append("broker_positions_unreadable")
        else:
            net = _net_by_key(await reader())
            if net is None:
                mismatches.append("broker_positions_unreadable")
    except Exception as exc:  # noqa: BLE001
        net = None
        mismatches.append(f"broker_positions_unreadable:{type(exc).__name__}")

    if net is not None and getattr(client, "_is_paper", None) is not True:
        for pos in store.open_positions(uid, account_id):
            if pos.status == live_positions.RECONCILING:
                mismatches.append(f"position_reconciling:{pos.option_symbol}")
                continue
            if pos.status == live_positions.EXITING:
                continue    # quantities are in flight; the exit manager owns this
            key = (pos.option_exchange.upper(), pos.product.upper(), pos.option_symbol.upper())
            held = net.get(key, 0)
            if held < pos.option_qty:
                mismatches.append(f"option_missing_at_broker:{pos.option_symbol}:{held}/{pos.option_qty}")
            if pos.has_hedge:
                hkey = (pos.hedge_exchange.upper(), pos.product.upper(), pos.hedge_symbol.upper())
                want = pos.hedge_qty if str(pos.hedge_side).upper() == "BUY" else -pos.hedge_qty
                got = net.get(hkey, 0)
                if (want > 0 and got < want) or (want < 0 and got > want):
                    mismatches.append(f"hedge_mismatch:{pos.hedge_symbol}:{got}/{want}")

    try:
        from app.services.kite_engine import order_journal

        for intent in order_journal.unresolved(uid, account_id) or []:
            if str(getattr(intent, "strategy_id", "") or "").lower() == "snapback":
                mismatches.append(f"unresolved_intent:{getattr(intent, 'symbol', '')}")
    except Exception as exc:  # noqa: BLE001
        mismatches.append(f"journal_unreadable:{type(exc).__name__}")

    snap = BookSnapshot(account_id=str(account_id), clean=not mismatches,
                        observed_at=time.time(), mismatches=mismatches)
    _BOOKS[str(account_id)] = snap
    return snap


def live_book_clean(uid: str = "default") -> bool:
    """Is Snapback's own book known to agree with the broker? Unknown is False.

    With no open Snapback position the book is vacuously clean unless a Snapback
    intent is unresolved, so a quiet machine is not stuck unready merely because
    nothing has been reconciled since the last close. With positions open, only a
    clean AND fresh snapshot for each of their accounts counts.
    """
    try:
        open_rows = live_positions.default_store().open_positions(uid)
        if not open_rows:
            from app.services.kite_engine import order_journal

            return not any(
                str(getattr(i, "strategy_id", "") or "").lower() == "snapback"
                for i in order_journal.unresolved(uid) or []
            )
        for account_id in {p.account_id for p in open_rows}:
            snap = latest_book(account_id)
            if snap is None or not (snap.clean and snap.fresh()):
                return False
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("Snapback book state unreadable: %s", exc)
        return False


# --------------------------------------------------------------------------
# Risk approval and revalidation
# --------------------------------------------------------------------------

class SnapbackRiskApprover:
    """Builds the request-bound RiskApproval from FRESH broker capital."""

    def __init__(self, *, plan_store: Any, broker_client_fn: Callable[..., Any]) -> None:
        self.plan_store = plan_store
        self.broker_client_fn = broker_client_fn

    async def __call__(self, *, uid: str, account_id: str, symbol: str, side: str,
                       quantity: int, generation_id: str, strategy_id: str, signal_id: str,
                       **_ignored: Any):
        from app.services.execution_service import RiskApproval

        def verdict(approved: bool, reason: str, *, available=None, required=None):
            return RiskApproval(
                approval_id=f"snapback-{int(time.time() * 1000)}", uid=uid,
                account_id=account_id, symbol=symbol, side=side, quantity=int(quantity),
                generation_id=generation_id, approved=approved, reason=reason,
                timestamp_ms=int(time.time() * 1000), available_capital=available,
                capital_required=required, strategy_id=strategy_id, signal_id=signal_id)

        plan = self._plan_for(signal_id)
        required = self._required(plan, symbol, int(quantity))
        client = self.broker_client_fn(uid, account_id)
        if hasattr(client, "__await__"):
            client = await client
        if client is None:
            return verdict(False, "broker_client_unavailable")

        from app.services.canonical_strategy_entry import available_entry_capital

        available = await available_entry_capital(client)
        if available <= 0:
            return verdict(False, "fresh_broker_capital_unavailable")
        if required is not None and required > available:
            return verdict(False, "insufficient_capital", available=available, required=required)

        from app.services.safety_supervisor import SafetySupervisor

        admitted = SafetySupervisor().authorize(
            "INCREASE_EXPOSURE", uid=uid, account_id=account_id, strategy_id="snapback")
        if not admitted.allowed:
            return verdict(False, admitted.code or "safety_refused",
                           available=available, required=required)
        return verdict(True, "Snapback live admission, fresh capital and safety passed",
                       available=available, required=required)

    def _plan_for(self, signal_id: str) -> Optional[Dict[str, Any]]:
        base = str(signal_id).split(":hedge-recovery:")[0]
        try:
            from app.services.snapback_plan_producer import plan_id_for

            return self.plan_store.get(plan_id_for(base))
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _required(plan: Optional[Dict[str, Any]], symbol: str, quantity: int) -> Optional[float]:
        if not plan:
            return None
        if symbol == plan.get("futures_symbol"):
            return float(plan.get("margin_required") or 0.0) or None
        price = float(plan.get("limit_price") or plan.get("max_option_price") or 0.0)
        return price * quantity if price > 0 else None


async def revalidate_plan(plan: Dict[str, Any], *, uid: str, client_fn: Callable[..., Any],
                          now: Optional[datetime] = None) -> Dict[str, Any]:
    """Is this still the right trade, now? Cheap checks first, one quote last."""
    from app.core import lane_registry

    reasons: List[str] = []
    moment = (now or datetime.now(_IST)).astimezone(timezone.utc)
    if not lane_registry.LIVE_EXECUTION_ENABLED:
        return {"ok": False, "reasons": ["kill_switch"]}

    expires = plan.get("expires_at")
    if expires:
        try:
            if moment >= datetime.fromisoformat(str(expires)):
                reasons.append("plan_expired")
        except ValueError:
            reasons.append("plan_expiry_unreadable")

    lane = str(plan.get("lane") or "")
    try:
        decision = lane_registry.may_originate("snapback", str(plan.get("horizon_mode") or lane))
        if not decision.allowed:
            reasons.append(decision.reason.lower() or "lane_not_originating")
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"lane_unreadable:{type(exc).__name__}")

    try:
        from app.services.kite_engine.market_hours import entry_block_reason

        blocked = entry_block_reason(
            moment.astimezone(_IST), exchange=str(plan.get("option_exchange") or "NFO"),
            cash_signal=True, buffer_minutes=5)
        if blocked:
            reasons.append(blocked)
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"session_unreadable:{type(exc).__name__}")
    if reasons:
        return {"ok": False, "reasons": reasons}

    client = client_fn(uid, str(plan.get("account_id") or ""))
    if hasattr(client, "__await__"):
        client = await client
    if client is None:
        return {"ok": False, "reasons": ["broker_client_unavailable"]}
    from app.services import snapback as runtime

    contract = {"exchange": plan.get("option_exchange"), "symbol": plan.get("option_symbol")}
    quote = await runtime._quote_for(client, contract, runtime.get_config(uid))
    if not quote or quote.get("blockers"):
        return {"ok": False, "reasons": ["quote_not_executable"],
                "detail": "; ".join((quote or {}).get("blockers") or [])}
    ask = float(quote.get("ask") or 0.0)
    limit = float(plan.get("limit_price") or plan.get("max_option_price") or 0.0)
    if limit > 0 and ask > limit + 1e-9:
        return {"ok": False, "reasons": ["price_moved_past_limit"],
                "detail": f"ask {ask:.2f} is above the plan's limit {limit:.2f}"}
    return {"ok": True, "reasons": []}


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------

def build_production_executor(uid: str, *, plan_store: Any,
                              positions: Optional[LivePositionStore] = None,
                              broker_client_fn: Optional[Callable[..., Any]] = None,
                              intent_store: Any = None,
                              clock: Optional[Callable[[], datetime]] = None):
    """The one place the live executor is assembled for real money."""
    from app.services.snapback_live_executor import build_live_executor, default_broker_client

    client_fn = broker_client_fn or default_broker_client
    store = positions or live_positions.default_store()
    protection = SnapbackProtection(positions=store, broker_client_fn=client_fn)
    adapter = SnapbackExecutionAdapter(plan_store=plan_store, positions=store)

    async def revalidate(plan: Dict[str, Any]) -> Dict[str, Any]:
        return await revalidate_plan(plan, uid=uid, client_fn=client_fn,
                                     now=clock() if clock else None)

    executor = build_live_executor(
        plan_store=plan_store, execution_service=adapter,
        reconciliation_fn=live_book_state,
        risk_approval_fn=SnapbackRiskApprover(plan_store=plan_store, broker_client_fn=client_fn),
        protection_fn=protection.place, revalidate_fn=revalidate, uid=uid,
        intent_store=intent_store, broker_client_fn=client_fn,
    )
    executor.position_store = store
    return executor
