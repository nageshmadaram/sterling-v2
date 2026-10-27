"""1.5 P0-LIVE: the one path from an ARMED plan to live exposure.

Before this, arming moved a durable plan to ARMED and nothing consumed it.
`submit_plan()` existed with no production caller, submitted only the option leg,
and passed no RiskApproval — the canonical service would have refused it. Saying
live execution was "complete" was too strong.

The ordering is the safety argument:

    revalidate → reconcile → risk approve → option fill
    → hedge sized to the CONFIRMED fill → protection → OPEN

Each step is gated on the previous one having actually happened, never on its
acknowledgement. Nothing here places an order: every order goes through the
canonical execution service, which is the only thing that talks to a broker, and
the broker-write capability guard sits under that.

The method is deliberately resumable and refuses to run twice for one plan: a
retry that re-enters here must not produce a second position.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from app.services.snapback_protection import (
    ProtectionState, protection_deadline_action, protection_required_quantity,
)

log = logging.getLogger(__name__)

#: The status a fully established position takes in the live-position registry.
OPEN_STATUS = "OPEN"

#: A hedge LIMIT that ends with nothing filled proves no hedge exists, so a new
#: attempt is safe. It is retried this many times (each re-priced from a fresh futures
#: quote) before a human is asked to look and new entries are halted.
HEDGE_ZERO_FILL_RETRIES = 3
_ZERO_FILL_PREFIX = "hedge_rejected_zero_fill:"


@dataclass
class LiveExecutionResult:
    status: str
    plan_id: str = ""
    reasons: List[str] = field(default_factory=list)
    option_filled: int = 0
    hedge_filled: int = 0
    protection_state: str = ProtectionState.NONE.value
    emergency_exit_quantity: int = 0
    reconcile_first: bool = False
    stop_new_entries: bool = False


#: Where the durable intent record lives. Overridable so a drill or a second
#: account can use its own file, never so it can be switched off.
INTENT_DB_ENV = "STERLING_INTENT_DB"
DEFAULT_INTENT_DB = "data/intents.db"


def default_intent_store():
    """The durable intent store this deployment should use.

    Kept separate from the executor so there is one answer to "where do intents
    live", and so `build_live_executor` cannot be given a different one by
    accident.
    """
    import os
    from pathlib import Path

    from app.core.state_paths import state_root
    from app.core.trade_intent import IntentStore

    configured = (os.environ.get(INTENT_DB_ENV) or "").strip()
    path = Path(configured) if configured else state_root() / DEFAULT_INTENT_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    return IntentStore(path)


async def default_broker_client(uid: str, account_id: str):
    """Resolve the exact authenticated Kite client for a durable plan account.

    The plan's account identity is part of execution authority.  Never substitute
    the user's currently-active account: changing a UI selection after arming must
    not redirect an already-authorized plan to different capital.
    """
    from app.services.exchanges.kite import accounts

    acct = accounts.get(uid, account_id)
    if acct is None or not getattr(acct, "connected", False):
        return None
    client = await accounts.acquire_client(acct)
    if str(getattr(client, "_account_id", "") or "") != str(account_id):
        return None
    return client


def build_live_executor(
    *,
    plan_store,
    execution_service,
    reconciliation_fn: Callable[[str], Dict[str, Any]],
    risk_approval_fn: Callable[..., Any],
    protection_fn: Callable[..., Any],
    revalidate_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
    uid: str = "default",
    intent_store: Any = None,
    broker_client_fn: Optional[Callable[[str, str], Any]] = None,
) -> "SnapbackLiveExecutor":
    """Build the live executor with durable intent recording already attached.

    The constructor takes ``intent_store=None`` because the bridge was added to
    an existing class and every test that predates it still constructs one
    without. That default is safe in a test and wrong in production: without a
    store there is no record of "sent, outcome never read", so a crash between
    submission and acknowledgement leaves an order that looks un-sent.

    Any real caller goes through here, where the store is supplied by default
    and can be replaced but not omitted.
    """
    return SnapbackLiveExecutor(
        plan_store=plan_store,
        execution_service=execution_service,
        reconciliation_fn=reconciliation_fn,
        risk_approval_fn=risk_approval_fn,
        protection_fn=protection_fn,
        revalidate_fn=revalidate_fn,
        uid=uid,
        intent_store=intent_store if intent_store is not None else default_intent_store(),
        broker_client_fn=broker_client_fn or default_broker_client,
    )


async def _resolved(value: Any) -> Any:
    """Await ``value`` when it is awaitable. Production callbacks are async
    (fresh broker evidence); the pure ones in tests are not."""
    if hasattr(value, "__await__"):
        return await value
    return value


def _hedge_side(plan: Dict[str, Any]) -> str:
    """Return the futures side that offsets the option delta, or "" if ambiguous.

    Snapback fade_up buys a PE (negative delta), so its futures hedge is LONG/BUY.
    fade_down buys a CE (positive delta), so its futures hedge is SHORT/SELL.
    Explicit side/type metadata must agree; the stored option symbol is only a
    backward-compatible identity check for older plans.
    """
    signal_side = str(
        plan.get("signal_side") or plan.get("side") or ""
    ).strip().lower()
    option_type = str(plan.get("option_type") or "").strip().upper()
    symbol = str(plan.get("option_symbol") or "").strip().upper()

    if not option_type:
        if symbol.endswith("PE"):
            option_type = "PE"
        elif symbol.endswith("CE"):
            option_type = "CE"

    by_signal = {
        "fade_up": "BUY",
        "fade_down": "SELL",
    }.get(signal_side)
    by_option = {
        "PE": "BUY",
        "CE": "SELL",
    }.get(option_type)

    if by_signal and by_option and by_signal != by_option:
        return ""
    return by_signal or by_option or ""


class SnapbackLiveExecutor:
    def __init__(
        self,
        *,
        plan_store,
        execution_service,
        reconciliation_fn: Callable[[str], Dict[str, Any]],
        risk_approval_fn: Callable[..., Any],
        protection_fn: Callable[..., Any],
        revalidate_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
        uid: str = "default",
        intent_store: Any = None,
        broker_client_fn: Optional[Callable[[str, str], Any]] = None,
    ) -> None:
        self.plan_store = plan_store
        self.execution_service = execution_service
        self.reconciliation_fn = reconciliation_fn
        self.risk_approval_fn = risk_approval_fn
        self.protection_fn = protection_fn
        self.revalidate_fn = revalidate_fn
        self.uid = uid
        self.intent_store = intent_store
        self.broker_client_fn = broker_client_fn
        #: Snapback's own live-position registry. Optional so the pure ordering
        #: tests need not build one; production always supplies it.
        self.position_store = None
        #: ``async (plan, broker_client, side) -> limit price | None``: re-prices the
        #: hedge LIMIT from the futures book at the moment of the send. Optional so the
        #: pure ordering tests keep the plan's own price; production supplies it.
        self.hedge_limit_fn = None

    # ── durable intent record ────────────────────────────────────────────
    #
    # The plan store already gives exactly-once via its CONSUMED flag, but it
    # has no state for "sent, outcome never read". A restart between submission
    # and acknowledgement therefore could not tell a working order from an
    # un-sent one. The intent store adds exactly that, and is optional so
    # existing callers keep working.

    def _reserve_intent(
        self, plan: Dict[str, Any], *, symbol: str, exchange: str,
        quantity: int, action: str,
    ):
        store = getattr(self, "intent_store", None)
        if store is None:
            return None
        from app.core.horizon import canonical_mode
        from app.core.trade_intent import TradeIntent, make_intent_id

        try:
            # plan["mode"] is the execution mode (LIVE), not the strategy horizon.
            # TradeIntent requires HorizonMode, so use the strategy field instead.
            mode = canonical_mode(str(
                plan.get("horizon_mode") or plan.get("trading_mode") or "swing"
            ))
            kwargs = dict(
                strategy_id="snapback",
                strategy_version=str(plan.get("strategy_version") or "snapback_core_v1"),
                mode=mode,
                mode_version=str(plan.get("mode_version") or f"snapback_{mode.value}_v1"),
                opportunity_id=str(plan.get("opportunity_id") or plan.get("plan_id") or ""),
                instrument_token=int(plan.get("option_instrument_token") or 0),
                exchange=exchange,
                tradingsymbol=symbol,
                side="BUY",
                quantity=int(quantity),
                order_type=str(plan.get("order_type") or "MARKET"),
                limit_price=plan.get("limit_price"),
                protection_required=True,
                max_loss_budget=float(plan.get("max_loss_budget") or plan.get("risk_amount") or 0.0),
                horizon_plan_id=str(plan.get("horizon_plan_id") or plan.get("plan_id") or ""),
                config_hash=str(plan.get("config_hash") or ""),
                runtime_sha=str(plan.get("runtime_sha") or plan.get("runtime_build_sha") or ""),
                action=action,
            )
            intent = TradeIntent(
                intent_id=make_intent_id(
                    strategy_id=kwargs["strategy_id"], mode=mode,
                    opportunity_id=kwargs["opportunity_id"],
                    instrument_token=kwargs["instrument_token"], action=action,
                ),
                **kwargs,
            )
            stored = store.reserve(intent)
            # Written BEFORE the first broker call, so a crash mid-submit is
            # recoverable as SUBMITTED_UNKNOWN rather than looking un-sent.
            #
            # On a resume after the option leg already filled, reserve() returns
            # that existing terminal intent. Never regress FILLED/PARTIAL/REJECTED
            # back to SUBMITTED_UNKNOWN: the canonical order journal will return
            # the already-observed broker outcome idempotently, allowing this
            # executor to continue with the missing hedge/protection steps without
            # re-sending the option order.
            state = getattr(stored, "state", None)
            state_value = str(getattr(state, "value", state) or "").upper()
            if state_value in {"", "NOT_SUBMITTED", "SUBMITTED_UNKNOWN"}:
                store.record_submission(intent.intent_id)
            return stored.intent
        except Exception as exc:
            # Intent durability is part of execution authority, not optional
            # bookkeeping. If reservation/submission evidence cannot be persisted,
            # no broker call may follow: a crash after send would otherwise leave
            # an order that cannot be distinguished from "never sent".
            log.error("Snapback live: durable intent reservation failed: %s", exc)
            raise RuntimeError("durable_intent_reservation_failed") from exc

    def _record_broker_outcome(self, intent, result) -> None:
        store = getattr(self, "intent_store", None)
        if store is None or intent is None:
            return
        from app.core.trade_intent import IntentState

        status = str(getattr(result, "status", "") or "").upper()
        filled = int(getattr(result, "filled_quantity", 0) or 0)
        if status in {"UNKNOWN", "SUBMITTING"}:
            # UNKNOWN: broker call outcome is unknown.
            # SUBMITTING: another worker owns the canonical submission claim.
            # Neither is evidence of rejection; the private bridge must remain
            # SUBMITTED_UNKNOWN until canonical broker evidence arrives.
            return
        if not getattr(result, "success", False):
            state = IntentState.REJECTED
        elif filled >= intent.quantity:
            state = IntentState.FILLED
        elif filled > 0:
            state = IntentState.PARTIAL
        else:
            state = IntentState.OPEN
        try:
            store.record_broker_state(
                intent.intent_id, state,
                broker_order_id=str(getattr(result, "order_id", "") or ""),
                filled_quantity=filled,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Snapback live: broker state not recorded: %s", exc)

    def _record_protection(self, intent, state) -> None:
        store = getattr(self, "intent_store", None)
        if store is None or intent is None:
            return
        from app.core.trade_intent import ProtectionState as DurableProtection

        try:
            store.record_protection(
                intent.intent_id,
                DurableProtection.CONFIRMED
                if str(getattr(state, "value", state)) == "ACTIVE"
                else DurableProtection.FAILED,
                note=f"broker protection state {getattr(state, 'value', state)}",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Snapback live: protection state not recorded: %s", exc)

    def _canonical_order_evidence(
        self,
        plan: Dict[str, Any],
        *,
        exchange: str,
        symbol: str,
        signal_id: str,
        side: str,
    ):
        """Read broker-observed state from the canonical execution journal."""
        try:
            from app.services.kite_engine import order_journal

            key = order_journal.make_key(
                uid=self.uid,
                account_id=str(plan.get("account_id") or ""),
                strategy_id="snapback",
                generation_id=str(plan.get("policy_snapshot_hash") or ""),
                signal_id=str(signal_id),
                exchange=str(exchange),
                symbol=str(symbol),
                side=str(side).upper(),
            )
            return order_journal.get(key)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "Snapback live: canonical order evidence unavailable for %s %s: %s",
                symbol, signal_id, exc,
            )
            return None

    def _canonical_option_evidence(
        self,
        plan: Dict[str, Any],
        *,
        exchange: str,
        symbol: str,
        signal_id: str,
    ):
        return self._canonical_order_evidence(
            plan,
            exchange=exchange,
            symbol=symbol,
            signal_id=signal_id,
            side="BUY",
        )

    @staticmethod
    def _proves_no_fill(state_value: str, filled: int) -> bool:
        """Terminal rejected/cancelled with nothing filled: no exposure exists."""
        return int(filled or 0) == 0 and str(state_value or "").lower() in {
            "rejected", "cancelled"}

    def _retire_unfilled_entry(self, plan_id: str, *, reason: str = "entry_not_filled") -> None:
        """The option leg ended terminal with zero fill: the plan is finished.

        Leaving it ARMED made the next cycle mistake the spent journal key for an
        order in flight, flag the plan RECONCILING and engage the global kill switch
        every cycle. One signal is one plan forever, so a cancelled plan is never
        retried either; nothing exists at the broker, so nothing needs recovering.
        """
        try:
            self.plan_store.cancel(plan_id, reason=reason, actor="executor")
        except Exception as exc:  # noqa: BLE001 - the dispatcher's recovery retires it
            log.error("Snapback live: could not cancel unfilled plan %s: %s", plan_id, exc)

    def _position_refusal(self, plan_id: str) -> str:
        """Why this plan may not act on its position ("" when it may).

        No position yet, or one still being established (ENTERING) or fully open (OPEN, the
        consume of the plan was what failed), is fine. CLOSED, EXITING and RECONCILING are
        not, and an unreadable registry is not "no position".
        """
        store = self.position_store
        if store is None:
            return ""
        try:
            pos = store.get(plan_id)
        except Exception as exc:  # noqa: BLE001
            log.error("Snapback live: position %s unreadable: %s", plan_id, exc)
            return "position_unreadable"
        if pos is None:
            return ""
        status = str(getattr(pos, "status", "") or "")
        return "" if status in {"ENTERING", OPEN_STATUS} else f"position_{status.lower()}"

    def _latest_reconciliation_context(self, plan_id: str) -> Dict[str, Any]:
        """Latest durable recovery evidence for this plan.

        The reason alone is insufficient: protection can fail after the hedge
        already filled, and a retry must not re-admit/re-submit that hedge.
        """
        try:
            getter = getattr(self.plan_store, "events", None)
            if not callable(getter):
                return {}
            for event in reversed(list(getter(plan_id) or [])):
                if str(event.get("event") or "") != "RECONCILING":
                    continue
                try:
                    detail = json.loads(str(event.get("detail_json") or "{}"))
                except (TypeError, ValueError):
                    detail = {}
                return detail if isinstance(detail, dict) else {}
        except Exception as exc:  # noqa: BLE001
            log.warning("Snapback live: recovery context unavailable for %s: %s", plan_id, exc)
        return {}

    def _latest_reconciliation_reason(self, plan_id: str) -> str:
        return str(self._latest_reconciliation_context(plan_id).get("reason") or "")

    def _latest_recovery_attempt_revision(self, plan_id: str) -> int:
        """Revision of the last allocated broker recovery attempt, or zero."""
        try:
            getter = getattr(self.plan_store, "events", None)
            if not callable(getter):
                return 0
            for event in reversed(list(getter(plan_id) or [])):
                if str(event.get("event") or "") == "RECOVERY_ATTEMPT":
                    return int(event.get("revision") or 0)
        except Exception as exc:  # noqa: BLE001
            log.warning("Snapback live: recovery attempt unavailable for %s: %s", plan_id, exc)
        return 0

    def _recovery_attempts(self, plan_id: str) -> int:
        """How many hedge recovery sends this plan has been allocated."""
        try:
            getter = getattr(self.plan_store, "events", None)
            if not callable(getter):
                return 0
            return sum(1 for e in (getter(plan_id) or [])
                       if str(e.get("event") or "") == "RECOVERY_ATTEMPT")
        except Exception as exc:  # noqa: BLE001 - unknown is not "none": callers treat it as spent
            log.warning("Snapback live: recovery attempts unreadable for %s: %s", plan_id, exc)
            return HEDGE_ZERO_FILL_RETRIES

    async def _fresh_hedge_limit(self, plan, broker_client, side: str) -> Optional[float]:
        fn = self.hedge_limit_fn
        if fn is None:
            return None
        try:
            found = fn(plan, broker_client, side)
            if hasattr(found, "__await__"):
                found = await found
            return float(found) if found else None
        except Exception as exc:  # noqa: BLE001 - the plan's own limit still stands
            log.warning("Snapback live: fresh hedge limit unavailable for %s: %s",
                        plan.get("plan_id"), exc)
            return None

    def _begin_recovery_attempt(
        self, plan_id: str, expected_revision: int, reason: str
    ) -> Optional[Dict[str, Any]]:
        """CAS-allocate a unique durable identity before a replacement hedge send."""
        try:
            begin = getattr(self.plan_store, "begin_recovery_attempt", None)
            if not callable(begin):
                log.critical("Snapback live: plan store cannot allocate recovery attempts")
                return None
            return begin(
                plan_id,
                expected_revision=int(expected_revision),
                reason=reason,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("Snapback live: recovery attempt allocation failed for %s: %s", plan_id, exc)
            return None

    async def _protect_now(self, *, plan_id: str, symbol: str, filled: int,
                           account_id: str, escalate: bool = True) -> "tuple[ProtectionState, int]":
        """Place the broker stop over ``filled`` option units, the moment they exist.

        Every second between an option fill and its stop is a second the position is
        naked, so this runs straight after the fill is confirmed, before the hedge
        leg's risk approval, send and fill wait. A refusal or an exception is returned
        as ``REQUIRED``, never raised: the caller carries on and tries again once
        the hedge leg has been dealt with. ``escalate=False`` marks this as that first,
        best-effort attempt: the protection layer records a failure without halting
        entries, so the retry (and the hedge leg before it) is not refused by a halt
        this attempt would have raised.
        """
        required = protection_required_quantity(confirmed_filled=filled)
        try:
            extra = {} if escalate else {"escalate": False}
            protection = await self.protection_fn(
                plan_id=plan_id, symbol=symbol, quantity=required, account_id=account_id,
                **extra,
            ) or {}
            return ProtectionState(str(protection.get("state") or "REQUIRED")), required
        except Exception as exc:  # noqa: BLE001 - the later attempt reports it
            log.error("Snapback live: early protection for %s failed: %s", plan_id, exc)
            return ProtectionState.REQUIRED, required

    async def execute(self, plan_id: str) -> LiveExecutionResult:
        plan = self.plan_store.get(plan_id)
        if plan is None:
            return LiveExecutionResult(status="REFUSED", plan_id=plan_id,
                                       reasons=["plan_not_found"])

        # Exactly once. A retry that re-enters here must not open a second
        # position, and a plan already consumed is not an error to shout about.
        if plan.get("status") == "CONSUMED":
            return LiveExecutionResult(status="ALREADY_CONSUMED", plan_id=plan_id)

        recovering = plan.get("status") == "RECONCILING"
        recovery_context = (
            self._latest_reconciliation_context(plan_id) if recovering else {}
        )
        recovery_reason = str(recovery_context.get("reason") or "")
        recovery_attempt_revision = (
            self._latest_recovery_attempt_revision(plan_id) if recovering else 0
        )
        if plan.get("status") not in {"ARMED", "RECONCILING"}:
            return LiveExecutionResult(
                status="REFUSED", plan_id=plan_id,
                reasons=[f"plan_is_{plan.get('status')}"],
            )

        # A plan acts only on the position it created. One whose position has closed, is
        # closing or is waiting for a person must not protect or hedge anything: the hedge
        # would be a futures leg for an option the account no longer holds.
        position_refusal = self._position_refusal(plan_id)
        if position_refusal:
            log.warning("Snapback live: %s not executed: %s", plan_id, position_refusal)
            return LiveExecutionResult(
                status="REFUSED", plan_id=plan_id, reasons=[position_refusal])

        owned_plan_revision = int(plan.get("revision") or 0)
        account_id = str(plan.get("account_id") or "")

        # 1. The plan was authorised at some earlier moment. Ask whether it is
        # still the right trade before acting on it.
        if not recovering:
            revalidation = await _resolved(self.revalidate_fn(plan)) or {}
            if not revalidation.get("ok"):
                return LiveExecutionResult(
                    status="REFUSED", plan_id=plan_id,
                    reasons=list(revalidation.get("reasons") or ["revalidation_failed"]),
                )

        # 2. A book we have not agreed on is a book we must not add to.
        snapshot = await _resolved(self.reconciliation_fn(account_id)) or {}
        if not snapshot.get("clean") or not snapshot.get("fresh"):
            return LiveExecutionResult(
                status="RECONCILING" if recovering else "REFUSED",
                plan_id=plan_id,
                reconcile_first=recovering,
                stop_new_entries=recovering,
                reasons=["reconciliation_not_clean_and_fresh"],
            )

        broker_client = None
        resolver = self.broker_client_fn
        if resolver is not None:
            try:
                candidate = resolver(self.uid, account_id)
                if hasattr(candidate, "__await__"):
                    candidate = await candidate
                broker_client = candidate
            except Exception as exc:
                log.error(
                    "Snapback live: broker client resolution failed for %s: %s",
                    account_id, exc,
                )
                return LiveExecutionResult(
                    status="REFUSED", plan_id=plan_id,
                    reasons=["broker_client_unavailable"],
                )
            if broker_client is None:
                return LiveExecutionResult(
                    status="REFUSED", plan_id=plan_id,
                    reasons=["broker_client_unavailable"],
                )

        option_qty = int(plan.get("option_quantity") or 0)
        option_symbol = str(plan.get("option_symbol") or "")

        option_signal_id = str(plan.get("opportunity_id") or plan_id)

        # 3. The option venue comes from the plan, never a default: "or NFO"
        # would route a SENSEX order (BFO) to the wrong derivatives venue.
        option_exchange = str(plan.get("option_exchange") or "").strip().upper()
        if not option_exchange:
            return LiveExecutionResult(
                status="REFUSED", plan_id=plan_id,
                reasons=["plan_missing_option_exchange"],
            )

        intent = None
        option_filled = 0

        if recovering:
            # Recovery is NOT a fresh option entry. The option already exists at
            # the broker; running fresh-entry risk/admission again can correctly
            # reject because that exposure/capital is now present, which would
            # strand the missing futures hedge forever. Consume only the durable
            # option outcome written by the original attempt and proceed to the
            # missing hedge/protection work. Never resubmit from this branch.
            try:
                intent = self._reserve_intent(
                    plan, symbol=option_symbol, exchange=option_exchange,
                    quantity=option_qty, action="entry",
                )
            except Exception:
                return LiveExecutionResult(
                    status="RECONCILING",
                    plan_id=plan_id,
                    reasons=["durable_option_recovery_evidence_unavailable"],
                    reconcile_first=True,
                    stop_new_entries=True,
                )
            canonical = self._canonical_option_evidence(
                plan,
                exchange=option_exchange,
                symbol=option_symbol,
                signal_id=option_signal_id,
            )
            state_value = ""
            option_filled = 0

            if canonical is not None:
                if bool(getattr(canonical, "reconciliation_required", False)):
                    reason = "canonical_option_evidence_conflicted"
                    self._mark_reconciling(
                        plan_id,
                        reason,
                        details={
                            k: v for k, v in recovery_context.items()
                            if k != "reason"
                        },
                    )
                    return LiveExecutionResult(
                        status="RECONCILING",
                        plan_id=plan_id,
                        reasons=[reason],
                        reconcile_first=True,
                        stop_new_entries=True,
                    )
                state_value = str(getattr(canonical, "state", "") or "").lower()
                option_filled = int(
                    getattr(canonical, "filled_quantity", 0) or 0
                )
            else:
                # Unit/backward fallback. Production broker fills are expected to
                # exist in the canonical journal after the shared postback bridge.
                store = getattr(self, "intent_store", None)
                stored = None
                try:
                    stored = (
                        store.get(intent.intent_id)
                        if store is not None and intent is not None
                        else None
                    )
                except Exception as exc:  # noqa: BLE001
                    log.error(
                        "Snapback live: option recovery evidence read failed: %s",
                        exc,
                    )
                state_value = str(
                    getattr(
                        getattr(stored, "state", None),
                        "value",
                        getattr(stored, "state", ""),
                    )
                    or ""
                ).lower()
                option_filled = int(
                    getattr(stored, "filled_quantity", 0) or 0
                )

            if self._proves_no_fill(state_value, option_filled):
                # The entry ended terminal with nothing filled: nothing exists at
                # the broker, so there is nothing to recover and nothing to halt.
                self._retire_unfilled_entry(plan_id)
                return LiveExecutionResult(
                    status="REJECTED", plan_id=plan_id, reasons=["entry_not_filled"],
                )
            terminal_with_fill = (
                state_value in {"filled", "cancelled", "rejected"}
                and option_filled > 0
            )
            if not terminal_with_fill:
                if option_filled > 0:
                    # Part of the option exists even though its order is still live.
                    await self._protect_now(plan_id=plan_id, symbol=option_symbol,
                                            filled=option_filled, account_id=account_id)
                reason = (
                    "option_partial_fill_still_live"
                    if state_value == "partial"
                    else "option_submission_unknown"
                    if state_value in {
                        "submitted_unknown", "unknown", "submitted",
                        "submitting", "acknowledged", "open",
                    }
                    else "durable_option_recovery_evidence_unavailable"
                )
                # Do not replace previously persisted hedge/protection evidence
                # with a reason-only event during a temporary option read gap.
                self._mark_reconciling(
                    plan_id,
                    reason,
                    details={
                        k: v for k, v in recovery_context.items()
                        if k != "reason"
                    },
                )
                return LiveExecutionResult(
                    status="RECONCILING",
                    plan_id=plan_id,
                    reasons=[reason],
                    reconcile_first=True,
                    stop_new_entries=True,
                )
        else:
            # Fresh exposure needs a request-bound risk approval. This happens
            # before marking the durable intent as submitted, so a denied risk
            # check cannot leave a phantom SUBMITTED_UNKNOWN record.
            approval = await _resolved(self.risk_approval_fn(
                uid=self.uid, account_id=account_id, symbol=option_symbol,
                side="BUY", quantity=option_qty,
                generation_id=str(plan.get("policy_snapshot_hash") or ""),
                strategy_id="snapback",
                signal_id=option_signal_id,
                plan=plan, leg="option",
            ))
            if not getattr(approval, "approved", False):
                return LiveExecutionResult(
                    status="REFUSED", plan_id=plan_id,
                    reasons=[f"risk_denied:{getattr(approval, 'reason', '')}"],
                )

            try:
                intent = self._reserve_intent(
                    plan, symbol=option_symbol, exchange=option_exchange,
                    quantity=option_qty, action="entry",
                )
            except Exception:
                return LiveExecutionResult(
                    status="REFUSED",
                    plan_id=plan_id,
                    reasons=["durable_intent_reservation_failed"],
                    stop_new_entries=True,
                )

            option_result = await self._submit(
                plan, symbol=option_symbol, exchange=option_exchange,
                side="BUY", quantity=option_qty, approval=approval,
                broker_client=broker_client, signal_id=option_signal_id,
            )
            self._record_broker_outcome(intent, option_result)

            option_status = str(option_result.status or "").upper()
            if option_status in {
                "UNKNOWN", "SUBMITTING", "SUBMITTED",
                "ACKNOWLEDGED", "OPEN", "PARTIAL",
            }:
                # A crash/restart can re-enter an ARMED plan after the canonical
                # journal already advanced. PARTIAL still has a live remainder;
                # ACK/SUBMITTED/OPEN are likewise nonterminal. Never size a hedge
                # or consume the plan until the option order is terminal.
                reason = (
                    "option_submission_concurrent"
                    if option_status == "SUBMITTING"
                    else "option_partial_fill_still_live"
                    if option_status == "PARTIAL"
                    else "option_submission_unknown"
                    if option_status == "UNKNOWN"
                    else f"option_submission_pending:{option_status.lower()}"
                )
                log.error(
                    "Snapback live: option submission %s for %s",
                    option_status, plan_id,
                )
                if int(option_result.filled_quantity or 0) > 0:
                    # The units that did fill were booked; they need their stop now.
                    await self._protect_now(
                        plan_id=plan_id, symbol=option_symbol,
                        filled=int(option_result.filled_quantity or 0), account_id=account_id)
                self._mark_reconciling(
                    plan_id,
                    reason,
                    details={
                        "option_filled": int(option_result.filled_quantity or 0),
                    },
                )
                return LiveExecutionResult(
                    status="RECONCILING", plan_id=plan_id, reconcile_first=True,
                    stop_new_entries=True, reasons=[reason],
                )

            if not option_result.success:
                # A journal key that ended rejected/cancelled with no fill is spent:
                # the plan can never place its option, and nothing exists. (A refusal
                # before the journal was touched leaves no key and stays ARMED to be
                # retried until it expires.)
                evidence = self._canonical_option_evidence(
                    plan, exchange=option_exchange, symbol=option_symbol,
                    signal_id=option_signal_id)
                if (evidence is not None
                        and not bool(getattr(evidence, "reconciliation_required", False))
                        and self._proves_no_fill(getattr(evidence, "state", ""),
                                                 getattr(evidence, "filled_quantity", 0))):
                    self._retire_unfilled_entry(plan_id)
                return LiveExecutionResult(
                    status="REJECTED", plan_id=plan_id,
                    reasons=[option_result.error or "option_rejected"],
                )

            option_filled = int(option_result.filled_quantity or 0)
            if option_filled <= 0:
                # A successful broker acknowledgement with zero confirmed fill
                # can still represent a working order. Preserve recovery state.
                self._mark_reconciling(plan_id, "option_fill_zero_unresolved")
                return LiveExecutionResult(
                    status="RECONCILING", plan_id=plan_id, reconcile_first=True,
                    stop_new_entries=True, reasons=["option_fill_zero"],
                )

        # 4b. The option is held and has no broker stop. Place it NOW, before the hedge
        # leg spends its risk approval, send and fill wait with the option naked.
        early_state, early_quantity = await self._protect_now(
            plan_id=plan_id, symbol=option_symbol, filled=option_filled,
            account_id=account_id, escalate=False)

        # 5. Hedge the position that exists, not the one intended. Sizing to the
        # requested quantity after a partial fill leaves a naked futures leg.
        hedge_filled = (
            int(recovery_context.get("hedge_filled") or 0) if recovering else 0
        )
        target_hedge = int(plan.get("target_futures_quantity") or 0)
        hedge_qty = 0
        futures_lot = int(plan.get("futures_lot_size") or 0)
        if target_hedge > 0 and option_qty > 0:
            if futures_lot > 0:
                # The broker only accepts whole futures lots. Size to the confirmed
                # option fill, rounded to the nearest lot (half up).
                lots = int(math.floor(target_hedge * option_filled / option_qty / futures_lot + 0.5))
                hedge_qty = max(0, lots) * futures_lot
            else:
                hedge_qty = int(math.ceil(target_hedge * option_filled / option_qty))

        persisted_hedge_target = (
            int(recovery_context.get("hedge_target") or 0) if recovering else 0
        )
        recovery_has_leg_evidence = (
            recovering
            and any(
                key in recovery_context
                for key in ("option_filled", "hedge_filled", "hedge_target", "hedge_problem")
            )
        )
        hedge_problem = (
            str(
                recovery_context.get("hedge_problem")
                or (
                    recovery_reason
                    if recovery_reason.startswith("hedge_")
                    else ""
                )
            )
            if recovering
            else ""
        )
        hedge_reconcile_first = False
        if target_hedge > 0 and option_filled > 0 and hedge_qty == 0 and not hedge_problem:
            # A hedge was part of the plan but the confirmed fill is too small for
            # one futures lot. Never carry that silently as if it were hedged.
            hedge_problem = "hedge_quantity_below_one_lot"
        if (
            recovering
            and recovery_reason == "protection_not_active"
            and not recovery_has_leg_evidence
        ):
            # Pre-context plans cannot prove whether the futures hedge already
            # exists. Never guess by sending one; broker reconciliation/manual
            # migration must establish the leg state first.
            hedge_problem = "recovery_leg_evidence_missing"
            hedge_reconcile_first = True
        if (
            recovering
            and persisted_hedge_target > 0
            and persisted_hedge_target != hedge_qty
        ):
            hedge_problem = (
                f"hedge_recovery_target_conflict:"
                f"{persisted_hedge_target}/{hedge_qty}"
            )
            hedge_reconcile_first = True
        if hedge_qty > 0:
            futures_symbol = str(plan.get("futures_symbol") or "").strip()
            futures_exchange = str(plan.get("futures_exchange") or "").strip().upper()
            hedge_side = _hedge_side(plan)
            if not futures_symbol:
                hedge_problem = "plan_missing_futures_symbol_after_option_fill"
                hedge_reconcile_first = True
            elif not futures_exchange:
                hedge_problem = "plan_missing_futures_exchange_after_option_fill"
                hedge_reconcile_first = True
            elif not hedge_side:
                hedge_problem = "plan_hedge_direction_ambiguous"
                hedge_reconcile_first = True
            else:
                base_hedge_signal_id = str(
                    plan.get("opportunity_id") or plan_id
                )
                observed_hedge_signal_id = (
                    f"{base_hedge_signal_id}:hedge-recovery:{recovery_attempt_revision}"
                    if recovering and recovery_attempt_revision > 0
                    else base_hedge_signal_id
                )
                if recovering:
                    canonical_hedge = self._canonical_order_evidence(
                        plan,
                        exchange=futures_exchange,
                        symbol=futures_symbol,
                        signal_id=observed_hedge_signal_id,
                        side=hedge_side,
                    )
                    if canonical_hedge is not None:
                        if bool(getattr(canonical_hedge, "reconciliation_required", False)):
                            hedge_problem = "canonical_hedge_evidence_conflicted"
                            hedge_reconcile_first = True
                        else:
                            hedge_state = str(
                                getattr(canonical_hedge, "state", "") or ""
                            ).lower()
                            canonical_hedge_filled = int(
                                getattr(canonical_hedge, "filled_quantity", 0) or 0
                            )
                            hedge_filled = canonical_hedge_filled
                            if (
                                hedge_state == "filled"
                                and canonical_hedge_filled == hedge_qty
                            ):
                                hedge_problem = ""
                                hedge_reconcile_first = False
                            elif (
                                hedge_state in {"cancelled", "rejected"}
                                and canonical_hedge_filled == 0
                            ):
                                hedge_problem = (
                                    "hedge_rejected_zero_fill:"
                                    f"canonical_{hedge_state}"
                                )
                                hedge_reconcile_first = False
                            elif hedge_state in {
                                "submitted", "submitting", "acknowledged",
                                "open", "partial",
                            } or 0 < canonical_hedge_filled < hedge_qty:
                                hedge_problem = (
                                    f"hedge_fill_mismatch:"
                                    f"{canonical_hedge_filled}/{hedge_qty}"
                                )
                                hedge_reconcile_first = True

                hedge_signal_id: Optional[str] = None
                hedge_already_complete = (
                    recovering
                    and hedge_qty > 0
                    and hedge_filled == hedge_qty
                    and not hedge_problem
                )

                if recovering and not hedge_already_complete:
                    # Durable partial/unknown evidence is NOT permission to submit
                    # again. The broker must be reconciled first. A terminal
                    # zero-fill rejection is different: it positively proves that
                    # none of that hedge exists, so a new revision-scoped attempt is
                    # safe. A risk denial never reached the broker and may simply be
                    # re-evaluated on the original identity.
                    if hedge_problem in {"hedge_submission_unknown"} or hedge_problem.startswith(
                        "hedge_fill_mismatch:"
                    ):
                        hedge_reconcile_first = True
                    elif (hedge_problem.startswith(_ZERO_FILL_PREFIX)
                          and self._recovery_attempts(plan_id) >= HEDGE_ZERO_FILL_RETRIES):
                        # Every retry was priced fresh and none filled: stop sending.
                        hedge_problem = (
                            f"hedge_retries_exhausted:{self._recovery_attempts(plan_id)}")
                        hedge_reconcile_first = True
                    elif hedge_problem.startswith(_ZERO_FILL_PREFIX):
                        attempt = self._begin_recovery_attempt(
                            plan_id,
                            int(plan.get("revision") or 0),
                            "retry confirmed zero-fill hedge rejection",
                        )
                        if attempt is None:
                            hedge_problem = "hedge_recovery_attempt_allocation_failed"
                            hedge_reconcile_first = True
                        else:
                            recovery_attempt_revision = int(attempt.get("revision") or 0)
                            owned_plan_revision = recovery_attempt_revision
                            hedge_problem = ""
                    elif hedge_problem.startswith("hedge_risk_denied:"):
                        # No broker send occurred; retrying admission is safe.
                        hedge_problem = ""

                    if recovery_attempt_revision > 0:
                        hedge_signal_id = (
                            f"{plan.get('opportunity_id') or plan_id}:"
                            f"hedge-recovery:{recovery_attempt_revision}"
                        )

                if not hedge_already_complete and not hedge_problem:
                    effective_hedge_signal_id = str(
                        hedge_signal_id or plan.get("opportunity_id") or plan_id
                    )
                    hedge_approval = await _resolved(self.risk_approval_fn(
                        uid=self.uid, account_id=account_id,
                        symbol=futures_symbol, side=hedge_side,
                        quantity=hedge_qty,
                        generation_id=str(plan.get("policy_snapshot_hash") or ""),
                        strategy_id="snapback",
                        signal_id=effective_hedge_signal_id,
                        plan=plan, leg="hedge",
                    ))
                    if not getattr(hedge_approval, "approved", False):
                        hedge_problem = (
                            "hedge_risk_denied:"
                            + str(getattr(hedge_approval, "reason", "") or "")
                        )
                    else:
                        # The plan's hedge LIMIT is as old as the plan. Price the send
                        # from the futures book as it is now.
                        fresh_limit = await self._fresh_hedge_limit(
                            plan, broker_client, hedge_side)
                        hedge_result = await self._submit(
                            plan, symbol=futures_symbol,
                            exchange=futures_exchange,
                            side=hedge_side, quantity=hedge_qty,
                            approval=hedge_approval,
                            broker_client=broker_client,
                            signal_id=hedge_signal_id,
                            price_override=fresh_limit,
                        )
                        hedge_filled = int(
                            getattr(hedge_result, "filled_quantity", 0) or 0
                        )
                        hedge_status = str(
                            getattr(hedge_result, "status", "") or ""
                        ).upper()
                        if hedge_status in {
                            "UNKNOWN", "SUBMITTING", "SUBMITTED",
                            "ACKNOWLEDGED", "OPEN", "PARTIAL",
                        }:
                            hedge_problem = (
                                "hedge_submission_concurrent"
                                if hedge_status == "SUBMITTING"
                                else "hedge_submission_unknown"
                                if hedge_status == "UNKNOWN"
                                else f"hedge_submission_pending:{hedge_status.lower()}"
                            )
                            hedge_reconcile_first = True
                        elif not getattr(hedge_result, "success", False):
                            if hedge_filled > 0:
                                hedge_problem = (
                                    f"hedge_fill_mismatch:{hedge_filled}/{hedge_qty}"
                                )
                                hedge_reconcile_first = True
                            else:
                                hedge_problem = (
                                    "hedge_rejected_zero_fill:"
                                    + str(getattr(hedge_result, "error", "") or "")
                                )
                        elif hedge_filled != hedge_qty:
                            hedge_problem = (
                                f"hedge_fill_mismatch:{hedge_filled}/{hedge_qty}"
                            )
                            hedge_reconcile_first = True
                        else:
                            hedge_problem = ""

        # 6. Protection covers the confirmed option inventory. Even when the
        # hedge failed or is uncertain, protect the option inventory that
        # definitely exists before returning a degraded/reconciliation state.
        required = protection_required_quantity(confirmed_filled=option_filled)
        if early_state is ProtectionState.ACTIVE and early_quantity == required:
            state = early_state          # already working over exactly this quantity
        else:
            protection = await self.protection_fn(
                plan_id=plan_id, symbol=option_symbol, quantity=required,
                account_id=account_id,
            ) or {}
            state = ProtectionState(str(protection.get("state") or "REQUIRED"))
        self._record_protection(intent, state)

        if state is not ProtectionState.ACTIVE:
            action = protection_deadline_action(
                state=state, deadline_exceeded=True,
                confirmed_filled=option_filled,
                entry_remainder=max(0, option_qty - option_filled),
            )
            log.error(
                "Snapback live: protection not established for %s (state=%s)",
                plan_id, state.value,
            )
            self._flag_position(plan_id, "unprotected")
            self._mark_reconciling(
                plan_id,
                "protection_not_active",
                details={
                    "option_filled": option_filled,
                    "hedge_filled": hedge_filled,
                    "hedge_target": hedge_qty,
                    "hedge_problem": hedge_problem,
                },
            )
            return LiveExecutionResult(
                status="PROTECTION_FAILED", plan_id=plan_id,
                option_filled=option_filled, hedge_filled=hedge_filled,
                protection_state=state.value,
                emergency_exit_quantity=action.emergency_exit_quantity,
                reconcile_first=action.reconcile_first,
                stop_new_entries=action.stop_new_entries,
                reasons=["protection_not_active"],
            )

        if hedge_problem:
            log.error(
                "Snapback live: hedge incomplete for %s (%s)",
                plan_id, hedge_problem,
            )
            self._flag_position(plan_id, "hedge_incomplete", hedge_problem)
            self._mark_reconciling(
                plan_id,
                hedge_problem,
                details={
                    "option_filled": option_filled,
                    "hedge_filled": hedge_filled,
                    "hedge_target": hedge_qty,
                    "hedge_problem": hedge_problem,
                },
            )
            # A hedge that ended with nothing filled is retried (re-priced) by the
            # dispatcher's recovery pass. That is an alert, not a halt: the halt it
            # used to raise refused the very retry that completes the hedge. Once
            # the retries are spent the halt applies.
            retryable = (
                hedge_problem.startswith(_ZERO_FILL_PREFIX) and not hedge_reconcile_first
                and self._recovery_attempts(plan_id) < HEDGE_ZERO_FILL_RETRIES
            )
            return LiveExecutionResult(
                status="RECONCILING" if hedge_reconcile_first else "HEDGE_FAILED",
                plan_id=plan_id,
                option_filled=option_filled,
                hedge_filled=hedge_filled,
                protection_state=state.value,
                reconcile_first=hedge_reconcile_first,
                stop_new_entries=not retryable,
                reasons=[hedge_problem],
            )

        self._mark_position(plan_id, OPEN_STATUS)
        if not self._consume(plan_id, expected_revision=owned_plan_revision):
            self._mark_reconciling(
                plan_id,
                "plan_consumption_failed",
                details={
                    "option_filled": option_filled,
                    "hedge_filled": hedge_filled,
                    "hedge_target": hedge_qty,
                    "hedge_problem": "",
                },
            )
            return LiveExecutionResult(
                status="RECONCILING",
                plan_id=plan_id,
                option_filled=option_filled,
                hedge_filled=hedge_filled,
                protection_state=state.value,
                reconcile_first=True,
                stop_new_entries=True,
                reasons=["plan_consumption_failed"],
            )
        return LiveExecutionResult(
            status="OPEN", plan_id=plan_id, option_filled=option_filled,
            hedge_filled=hedge_filled, protection_state=state.value,
        )

    async def _submit(
        self, plan, *, symbol, exchange, side, quantity, approval,
        broker_client=None, signal_id: Optional[str] = None,
        price_override: Optional[float] = None,
    ):
        from app.services.execution_service import ExecutionRequest, ExecutionResult, ExposureEffect
        import math

        real_transport = getattr(broker_client, "_is_paper", None) is False
        live_request = str(plan.get("mode") or "").upper() == "LIVE" or real_transport
        is_hedge = bool(plan.get("futures_symbol")) and symbol == plan.get("futures_symbol")
        if live_request:
            option_matches = (
                not is_hedge
                and symbol == str(plan.get("option_symbol") or "")
                and exchange == str(plan.get("option_exchange") or "")
                and side == "BUY"
            )
            hedge_matches = (
                is_hedge
                and exchange == str(plan.get("futures_exchange") or "")
                and side == str(plan.get("hedge_side") or "").upper()
                and side in {"BUY", "SELL"}
            )
            if not (option_matches or hedge_matches):
                return ExecutionResult(
                    success=False, status="REJECTED_PLAN",
                    error="Snapback entry does not match frozen contract/exchange/direction",
                )
        if is_hedge and self.hedge_limit_fn is not None and price_override is None:
            # Production has a fresh futures-book repricer. If its read failed,
            # a plan-era quote cannot authorize an exposure-changing hedge.
            # The executor still protects the confirmed option and marks the
            # hedge recovery needed without submitting at an obsolete limit.
            return ExecutionResult(
                success=False, status="REJECTED_PLAN",
                error="Snapback fresh futures hedge quote unavailable; no stale-price fallback",
            )
        if is_hedge:
            order_type = str(plan.get("hedge_order_type") or "")
            raw_price = price_override if price_override is not None else plan.get("hedge_price_limit")
            raw_lot_size = plan.get("futures_lot_size")
        else:
            order_type = str(plan.get("order_type") or "")
            raw_price = plan.get("limit_price")
            raw_lot_size = plan.get("lot_size")
        try:
            if isinstance(raw_price, bool):
                raise ValueError("bool cannot be a limit price")
            price = float(raw_price or 0.0)
            lot_float = float(raw_lot_size or 0)
            qty_float = float(quantity)
            if (isinstance(raw_lot_size, bool) or isinstance(quantity, bool)
                    or not math.isfinite(lot_float) or not math.isfinite(qty_float)
                    or lot_float <= 0 or lot_float != int(lot_float)
                    or qty_float <= 0 or qty_float != int(qty_float)):
                raise ValueError("invalid integral lot or quantity")
            lot_size = int(lot_float)
            approved_quantity = int(qty_float)
            if approved_quantity % lot_size != 0:
                raise ValueError("quantity not an exchange whole-lot multiple")
        except (TypeError, ValueError, OverflowError):
            return ExecutionResult(
                success=False, status="REJECTED_PLAN",
                error="Snapback entry price, quantity or lot metadata is invalid",
            )
        priced = order_type.upper() == "LIMIT" and math.isfinite(price) and price > 0
        # Production plans from build_plan carry mode=LIVE and frozen LIMIT
        # prices for both option and futures hedge. A corrupt/legacy payload
        # must never silently become an unbounded market BUY/hedge.
        if live_request and not priced:
            return ExecutionResult(
                success=False, status="REJECTED_PLAN",
                error="Snapback live exposure-increasing legs require a valid LIMIT price",
            )
        payload = {"product": str(plan.get("product") or "NRML")}
        if (plan.get('rules') or {}).get('mode_policy'):
            payload['mode_policy'] = plan['rules']['mode_policy']
        if lot_size > 0:
            payload["lot_size"] = lot_size
        if priced:
            payload["limit_price"] = price

        request = ExecutionRequest(
            uid=self.uid,
            account_id=str(plan.get("account_id") or ""),
            strategy_id="snapback",
            generation_id=str(plan.get("policy_snapshot_hash") or ""),
            signal_id=str(signal_id or plan.get("opportunity_id") or ""),
            exchange=exchange,
            symbol=symbol,
            side=side,
            quantity=approved_quantity,
            exposure_effect=ExposureEffect.INCREASE_EXPOSURE,
            tag=f"snapback:{plan.get('plan_id')}",
            order_type=order_type.upper() if priced else "MARKET",
            price=price if priced else 0.0,
            capital_required=float(getattr(approval, "capital_required", 0.0) or 0.0),
            available_capital=getattr(approval, "available_capital", None),
            payload=payload,
        )
        return await self.execution_service.submit_order(
            request,
            broker_client=broker_client,
            risk_approval=approval,
            risk_approved=True,
        )

    def _mark_reconciling(
        self,
        plan_id: str,
        reason: str,
        *,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Persist live recovery ownership; never unwind broker exposure on DB error."""
        try:
            marker = getattr(self.plan_store, "mark_reconciling", None)
            if callable(marker):
                marker(plan_id, reason=reason, details=details)
            else:
                log.critical(
                    "Snapback live: plan store cannot persist reconciliation for %s",
                    plan_id,
                )
        except Exception as exc:  # noqa: BLE001
            log.critical(
                "Snapback live: could not persist reconciliation for %s: %s",
                plan_id, exc,
            )

    def _mark_position(self, plan_id: str, status: str) -> None:
        """The position is fully established (option, hedge, protection)."""
        store = self.position_store
        if store is None:
            return
        try:
            def apply(pos) -> None:
                if pos.status == "ENTERING":
                    pos.status = status
                pos.flags = [f for f in pos.flags if f not in ("hedge_incomplete", "unprotected")]

            store.mutate(plan_id, apply)
        except Exception as exc:  # noqa: BLE001 - never unwind a live position
            log.error("Snapback live: position %s not marked open: %s", plan_id, exc)

    def _flag_position(self, plan_id: str, flag: str, note: str = "") -> None:
        store = self.position_store
        if store is None:
            return
        try:
            store.add_flag(plan_id, flag, note)
        except Exception as exc:  # noqa: BLE001
            log.error("Snapback live: position %s not flagged %s: %s", plan_id, flag, exc)

    def _consume(self, plan_id: str, *, expected_revision: int) -> bool:
        """Consume only the plan revision owned by this executor instance."""
        try:
            self.plan_store.consume(
                plan_id,
                expected_revision=int(expected_revision),
            )
            return True
        except Exception as exc:  # noqa: BLE001 - never unwind a live position
            log.error(
                "Snapback live: could not mark plan %s consumed: %s",
                plan_id, exc,
            )
            return False
