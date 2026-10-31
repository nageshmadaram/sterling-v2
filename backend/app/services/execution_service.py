"""Canonical Execution Service for Sterling.

Serves as the single execution authority for all broker order submissions across
all strategy engines (Kite Engine, Opening Leaders, Intraday, Gamma Move, Adaptive Edge,
Navigator, manual API).

Enforces:
1. Strict order state machine: RESERVED -> SUBMITTING -> ACKNOWLEDGED / UNKNOWN -> PARTIAL / FILLED.
2. Durable intent journal reservation (kite_engine/order_journal.py).
3. Broker order recovery on transport uncertainty (never blind re-submission).
4. Durable 2D halt & recovery state checking via execution_control (operator_state & recovery_state).
5. Exposure effect classification (INCREASE_EXPOSURE, REDUCE_EXPOSURE, CLOSE_POSITION, PROTECT_POSITION, CANCEL_ORDER, MODIFY_ORDER).
6. Fail-closed broker client availability, production static egress, exact account identity, and deployment binding.
7. Real broker protocol arguments (symbol/tradingsymbol, side/transaction_type, size/quantity, exchange, product=NRML, allow_amo=False, trigger_price).
8. Inventory verification of declared exposure effects.
9. Protection lease & pending persistence for protection commands.
10. Complete startup recovery (journal intents + inventory reconciliation + protection pending state).
"""
import asyncio
from dataclasses import dataclass, field
from enum import Enum
import inspect
import math
import time
from typing import Any, Dict, Optional, Tuple

from app.core.logging import get_logger
from app.services import db
from app.services.kite_engine import order_journal
from app.services.kite_engine.execution_lifecycle import recover

log = get_logger(__name__)


class BrokerRejected(Exception):
    """Order explicitly rejected by broker API."""
    pass


class SubmissionOutcomeUnknown(Exception):
    """Broker order submission transport or post-send status unknown."""
    pass


def _never_reached_the_broker(exc: BaseException) -> bool:
    """True when ``exc`` proves the order request was never written to the wire.

    A refusal by the broker-write guard happens inside this process; a connect,
    DNS or pool failure happens before the request body is sent. Neither can
    have created an order, so the intent is definitively not delivered. A read
    timeout, a 5xx, a Kite network error or anything unclassified proves nothing
    and stays UNKNOWN: the order may exist and must be found, never re-sent.
    """
    if isinstance(exc, PermissionError):
        return True
    try:
        import socket

        import httpx

        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout,
                            socket.gaierror)):
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


class ExposureEffect(str, Enum):
    INCREASE_EXPOSURE = "INCREASE_EXPOSURE"
    REDUCE_EXPOSURE = "REDUCE_EXPOSURE"
    CLOSE_POSITION = "CLOSE_POSITION"
    PROTECT_POSITION = "PROTECT_POSITION"
    CANCEL_ORDER = "CANCEL_ORDER"
    MODIFY_ORDER = "MODIFY_ORDER"


@dataclass(frozen=True)
class RiskApproval:
    approval_id: str
    uid: str
    account_id: str
    symbol: str
    side: str
    quantity: int
    generation_id: str
    approved: bool = True
    reason: str = "Risk decision approved"
    timestamp_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    available_capital: Optional[float] = None
    capital_required: Optional[float] = None
    strategy_id: str = ""
    signal_id: str = ""


@dataclass(frozen=True)
class ExecutionRequest:
    uid: str
    account_id: str
    strategy_id: str
    generation_id: str
    signal_id: str
    exchange: str
    symbol: str
    side: str                            # "BUY" | "SELL"
    quantity: int
    tag: str = ""
    exposure_effect: ExposureEffect = ExposureEffect.INCREASE_EXPOSURE
    order_type: str = "MARKET"
    price: float = 0.0
    trigger_price: float = 0.0
    capital_required: float = 0.0
    available_capital: Optional[float] = None
    payload: Optional[Dict[str, Any]] = None
    gtt_params: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class ExecutionResult:
    success: bool
    status: str          # "FILLED" | "ACKNOWLEDGED" | "SUBMITTED" | "UNKNOWN" | "REJECTED" | "HALTED"
    intent_key: str = ""
    order_id: str = ""
    error: str = ""
    filled_quantity: int = 0
    filled_price: float = 0.0
    protection_id: str = ""
    revision: int = 0


def snapback_live_gate_decision(**kwargs):
    """Server-derived family gate decision for Snapback.

    Every input is derived here, never taken from the caller's payload: evidence
    verdict from the authoritative gate, readiness from the server, health from the
    health probe, reconciliation from the broker state. A caller that claims to be
    LIVE_ELIGIBLE is simply not believed.
    """
    from app.services.snapback_family_gate import evaluate_live_gate

    return evaluate_live_gate(**kwargs)


def _snapback_server_state(uid: str) -> dict:
    """Derive the gate inputs. Anything unknown fails closed."""
    verdict = "INCONCLUSIVE"
    live_eligible = False
    system_status = "HALTED"
    broker_reconciled = False

    try:
        from app.services.snapback_family_ops import get_family_evidence_verdict

        verdict = str(get_family_evidence_verdict().get("verdict") or "INCONCLUSIVE")
    except Exception as exc:
        log.warning("Snapback gate: evidence verdict unavailable: %s", exc)

    try:
        from app.services.snapback_readiness import readiness_state

        state = readiness_state(uid)
        live_eligible = state.state == "LIVE_ELIGIBLE"
        system_status = state.system_status
        broker_reconciled = state.broker_reconciled
    except Exception as exc:
        log.warning("Snapback gate: readiness unavailable: %s", exc)

    return {
        "evidence_verdict": verdict,
        "live_eligible": live_eligible,
        "system_status": system_status,
        "broker_reconciled": broker_reconciled,
    }


class CanonicalExecutionService:
    """Single execution authority for all broker order placements."""

    def __init__(self):
        pass

    def verify_exposure_effect(
        self,
        uid: str,
        account_id: str,
        symbol: str,
        side: str,
        quantity: int,
        declared_effect: ExposureEffect,
    ) -> ExposureEffect:
        """Verify caller-declared exposure effect against database inventory including quantity overshoot checks.
        
        If caller declares CLOSE_POSITION or REDUCE_EXPOSURE without holding open inventory,
        or on the same side as existing position, or for quantity > net_quantity,
        reclassify as INCREASE_EXPOSURE.
        """
        if declared_effect not in (ExposureEffect.CLOSE_POSITION, ExposureEffect.REDUCE_EXPOSURE):
            return declared_effect

        try:
            inv = db.get_inventory_strict(account_id, uid, symbol) if hasattr(db, "get_inventory_strict") else None
            net_qty = inv.get("net_quantity", 0) if inv else 0
            if net_qty == 0:
                log.warning("Declared %s for %s on zero inventory — reclassifying to INCREASE_EXPOSURE", declared_effect.value, symbol)
                return ExposureEffect.INCREASE_EXPOSURE

            is_reducing = (side.upper() == "SELL" and net_qty > 0) or (side.upper() == "BUY" and net_qty < 0)
            if not is_reducing:
                log.warning("Declared %s for %s on same side (%s, net=%d) — reclassifying to INCREASE_EXPOSURE", declared_effect.value, symbol, side, net_qty)
                return ExposureEffect.INCREASE_EXPOSURE

            if quantity > abs(net_qty):
                log.warning("Declared %s for %s quantity %d > net %d — reclassifying to INCREASE_EXPOSURE due to overshoot", declared_effect.value, symbol, quantity, net_qty)
                return ExposureEffect.INCREASE_EXPOSURE
        except Exception as exc:
            log.warning("Failed to verify inventory for %s: %s — treating as INCREASE_EXPOSURE", symbol, exc)
            return ExposureEffect.INCREASE_EXPOSURE

        return declared_effect

    async def verify_broker_reduction(
        self,
        broker_client: Any,
        request: ExecutionRequest,
    ) -> Tuple[bool, str]:
        """Prove a live REDUCE/CLOSE is still reducing at the broker boundary.

        Durable inventory is necessary for recovery, but it is not sufficient to
        authorize a real-money reduction: it can lag a manual exit or another
        process.  A stale positive row followed by a SELL would otherwise open a
        short while carrying a REDUCE capability.  Paper clients have no broker
        exposure and may rely on the local inventory model; live/unknown clients
        must positively prove fresh broker inventory.
        """
        mode = getattr(broker_client, "_is_paper", None)
        if mode is True:
            return True, "paper"
        if mode is not False:
            return False, "Cannot verify broker mode for live reduction"

        reader = getattr(broker_client, "get_positions_raw", None)
        if not callable(reader):
            return False, "Live reduction requires fresh broker positions"

        try:
            raw = await reader() if inspect.iscoroutinefunction(reader) else reader()
        except Exception as exc:
            return False, f"Fresh broker positions unavailable: {exc}"

        if not isinstance(raw, dict):
            return False, "Fresh broker positions returned an invalid payload"

        rows = raw.get("net")
        if not isinstance(rows, list):
            return False, "Fresh broker positions are missing the net book"

        symbol = str(request.symbol or "").strip().upper()
        exchange = str(request.exchange or "").strip().upper()
        from app.services.kite_engine.positions import broker_product
        product = broker_product((request.payload or {}).get("product", "NRML"))
        if not product:
            return False, "Cannot verify broker product for live reduction"

        net_qty = 0
        matched = False
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_symbol = str(row.get("tradingsymbol") or row.get("symbol") or "").strip().upper()
            row_exchange = str(row.get("exchange") or "").strip().upper()
            row_product = str(row.get("product") or "").strip().upper()
            if row_symbol != symbol or row_exchange != exchange or row_product != product:
                continue
            try:
                net_qty += int(row.get("quantity") or row.get("net_quantity") or 0)
            except (TypeError, ValueError):
                return False, "Fresh broker position quantity is invalid"
            matched = True

        if not matched or net_qty == 0:
            return False, f"Broker has no {exchange}:{symbol} {product} inventory to reduce"

        side = str(request.side or "").upper()
        qty = int(request.quantity or 0)
        if qty <= 0:
            return False, "Reduction quantity must be positive"
        if side == "SELL" and net_qty > 0 and qty <= net_qty:
            return True, "verified long reduction"
        if side == "BUY" and net_qty < 0 and qty <= abs(net_qty):
            return True, "verified short reduction"
        return False, (
            f"Broker inventory does not prove {side} {qty} is reducing "
            f"{exchange}:{symbol} {product} (net={net_qty})"
        )

    async def verify_production_account_binding(
        self,
        broker_client: Any,
        request: ExecutionRequest,
    ) -> Tuple[bool, str]:
        """Prove the production deployment is bound to this exact live account.

        The durable binding is not decorative release metadata.  In production it
        is the authority for which legal broker account this deployment may act
        through.  A stale verification is refreshed read-only from Kite before an
        entry; missing, ambiguous or contradictory identity refuses new exposure.

        This gate is intentionally entry-only.  Exits, protection and
        reconciliation must remain available even when binding metadata is broken.
        """
        from app.services.account_binding_service import (
            active_binding,
            verification_is_current,
        )

        try:
            binding = active_binding()
        except Exception as exc:  # noqa: BLE001
            return False, f"Broker account binding is unreadable: {exc}"
        if binding is None:
            return False, "No ACTIVE broker account binding is declared"
        if not bool(getattr(binding, "live_ready", False)):
            return False, "ACTIVE broker account binding is not live-ready"

        import os

        running_deployment = str(os.environ.get("STERLING_DEPLOYMENT_ID") or "").strip()
        binding_deployment = str(getattr(binding, "deployment_id", "") or "").strip()
        if not running_deployment:
            return False, "STERLING_DEPLOYMENT_ID is required for production broker binding"
        if running_deployment != binding_deployment:
            return False, (
                f"Running deployment {running_deployment!r} does not match "
                f"ACTIVE binding deployment {binding_deployment!r}"
            )

        account_id = str(request.account_id or "").strip()
        client_account = str(
            getattr(broker_client, "_account_id", getattr(broker_client, "account_id", ""))
            or ""
        ).strip()
        if not account_id or client_account != account_id:
            return False, "Broker client does not match the requested application account"

        client_kite_user = str(getattr(broker_client, "_kite_user_id", "") or "").strip()
        binding_client = str(getattr(binding, "client_id", "") or "").strip()
        if client_kite_user and binding_client and client_kite_user != binding_client:
            return False, (
                f"Broker session user {client_kite_user!r} does not match "
                f"ACTIVE binding client {binding_client!r}"
            )

        # Verification evidence intentionally expires.  Refresh it here rather
        # than making a human re-run a command during the trading session.
        if not verification_is_current(binding):
            try:
                from app.services.broker_binding_verification import (
                    verify_current_kite_binding,
                )

                binding = await verify_current_kite_binding(
                    binding.binding_id,
                    user_id=request.uid,
                    application_account_id=account_id,
                )
            except Exception as exc:  # noqa: BLE001
                return False, f"Fresh broker account binding verification failed: {exc}"

        if not verification_is_current(binding):
            return False, "Broker account binding verification is not current"
        if str(getattr(binding, "verification_account_id", "") or "").strip() != account_id:
            return False, (
                "ACTIVE broker binding was verified for a different application account"
            )
        if (
            str(getattr(binding, "verification_profile_client_id", "") or "").strip()
            != str(getattr(binding, "client_id", "") or "").strip()
        ):
            return False, "ACTIVE broker binding profile identity is inconsistent"

        return True, "verified production broker binding"

    def is_trading_allowed(
        self,
        scope: str = "global",
        uid: str = "default",
        account_id: str = "default",
        exposure_effect: ExposureEffect = ExposureEffect.INCREASE_EXPOSURE,
        strategy_id: str = "",
        ignore_intent_keys: Tuple[str, ...] = (),
        entry_request: Optional[ExecutionRequest] = None,
    ) -> Tuple[bool, str]:
        """Ask the one safety authority. Never decide admission locally."""
        from app.services.safety_supervisor import SafetySupervisor

        verdict = SafetySupervisor().authorize(
            exposure_effect.value,
            uid=uid,
            account_id=account_id,
            strategy_id=strategy_id,
            ignore_intent_keys=ignore_intent_keys,
            entry_request=entry_request,
        )
        if not verdict.allowed:
            return False, verdict.reason
        return True, "OK"

    def require_execution_authority(
        self,
        *,
        uid: str,
        account_id: str,
        exposure_effect: ExposureEffect,
        scope: str = "global",
        strategy_id: str = "",
        ignore_intent_keys: Tuple[str, ...] = (),
        entry_request: Optional[ExecutionRequest] = None,
    ) -> Tuple[bool, str]:
        """Fail-closed lifecycle/safe-mode admission for canonical operations.

        This SafetySupervisor verdict is NOT sufficient to send to a broker.
        submit_order also checks policy, risk, account, session, release scope,
        transport and the final just-before-send safety revision.
        """
        try:
            effect = ExposureEffect(exposure_effect)
        except (TypeError, ValueError):
            return False, "Unknown execution exposure effect"
        if (
            not isinstance(uid, str) or not uid.strip() or uid != uid.strip()
            or not isinstance(account_id, str) or not account_id.strip()
            or account_id != account_id.strip()
        ):
            return False, "Invalid execution account identity"
        try:
            return self.is_trading_allowed(
                scope=scope, uid=uid, account_id=account_id,
                exposure_effect=effect, strategy_id=strategy_id,
                ignore_intent_keys=ignore_intent_keys,
                entry_request=entry_request,
            )
        except Exception:
            return False, "Canonical execution safety authority unavailable"

    @staticmethod
    def _mark_submission_uncertain(request: ExecutionRequest, intent: Any, reason: str) -> None:
        """Record an uncertain send and fence the account until recovery reads the broker."""
        try:
            order_journal.submission_uncertain(intent.intent_key, reason)
        except Exception as journal_err:  # noqa: BLE001
            log.error("Could not mark intent %s uncertain: %s", intent.intent_key, journal_err)
        try:
            db.set_recovery_state(
                recovery_state="RECOVERY_REQUIRED",
                reason_code="BROKER_TRANSPORT_UNCERTAINTY",
                reason=f"Broker submit uncertain for intent {intent.intent_key}: {reason}",
                uid=request.uid,
                account_id=request.account_id,
            )
        except Exception as db_err:  # noqa: BLE001
            log.warning("Failed to set RECOVERY_REQUIRED in DB: %s", db_err)

    async def submit_order(
        self,
        request: ExecutionRequest,
        broker_client: Any = None,
        risk_approval: Optional[RiskApproval] = None,
        risk_approved: bool = False,
    ) -> ExecutionResult:
        """Submit an order through the canonical execution pipeline, then alert.

        Every strategy's order passes here, so this is the one place that can
        tell the operator an entry/exit was submitted, rejected, blocked or left
        in an unknown state — without any strategy having to remember to.
        """
        from app.services.execution_identity_lock import account_lock
        async with account_lock(request.uid):
            result = await self._submit_order(
                request,
                broker_client=broker_client,
                risk_approval=risk_approval,
                risk_approved=risk_approved,
            )
        try:  # alerting must never change an order outcome
            from app.services.notifications import system_alerts

            system_alerts.order_submission(request, result)
        except Exception:  # noqa: BLE001
            pass
        return result

    async def _submit_order(
        self,
        request: ExecutionRequest,
        broker_client: Any = None,
        risk_approval: Optional[RiskApproval] = None,
        risk_approved: bool = False,
    ) -> ExecutionResult:
        """Submit an order through the canonical execution pipeline."""
        from app.services.kite_engine.positions import broker_product

        if (type(request.quantity) is not int or request.quantity <= 0
                or str(request.side).upper() not in {"BUY", "SELL"}):
            return ExecutionResult(success=False, status="REJECTED", error="Invalid order quantity or side")
        for value in (request.price, request.trigger_price, request.capital_required):
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                return ExecutionResult(success=False, status="REJECTED", error="Order prices and capital must be finite nonnegative numbers")
        request_product = broker_product((request.payload or {}).get("product", "NRML"))
        if not request_product:
            return ExecutionResult(success=False, status="REJECTED",
                                   error="Broker product identity is unknown")
        # Cancellation, amendment and protection have dedicated primitives with
        # their own proofs. A command label on an ordinary order must never skip
        # both the entry gates and broker-reduction proof before place_order.
        try:
            declared_effect = ExposureEffect(request.exposure_effect)
        except (TypeError, ValueError):
            declared_effect = None
        if declared_effect not in {
            ExposureEffect.INCREASE_EXPOSURE,
            ExposureEffect.REDUCE_EXPOSURE,
            ExposureEffect.CLOSE_POSITION,
        }:
            return ExecutionResult(
                success=False,
                status="REJECTED",
                error="Order submission requires an explicit increase/reduce/close exposure effect",
            )

        # Verify declared exposure effect against inventory & quantity overshoot
        effective_effect = self.verify_exposure_effect(
            uid=request.uid,
            account_id=request.account_id,
            symbol=request.symbol,
            side=request.side,
            quantity=request.quantity,
            declared_effect=declared_effect,
        )

        is_exposure_increasing = effective_effect == ExposureEffect.INCREASE_EXPOSURE
        if is_exposure_increasing:
            from app.services.auth.account_policy import entry_refusal as feature_refusal
            refusal = feature_refusal(request.uid)
            if refusal:
                return ExecutionResult(success=False, status='REJECTED_FEATURE_ACCESS', error=refusal)

        # Production release scope is an execution invariant, not a UI choice.
        # Legacy engines may remain readable and may reduce/reconcile positions,
        # but they cannot originate new exposure in the production release even
        # if an old endpoint, persisted toggle or direct API call still exists.
        #
        # The product is live-only, so the gate does not depend on an environment
        # variable: any broker client that is not an explicit in-process test
        # double is held to it. ``ENVIRONMENT=production`` still applies it to a
        # call with no client, and is read through ``runtime_env`` so a value that
        # only lives in ``backend/.env`` (pydantic-settings) is honoured too.
        if is_exposure_increasing:
            from app.core import runtime_env

            live_transport = getattr(broker_client, "_is_paper", None) is False
            if live_transport or runtime_env.is_production():
                from app.core.release_scope import may_originate
                if not may_originate(request.strategy_id, request.uid):
                    return ExecutionResult(
                        success=False,
                        status="REJECTED_OUTSIDE_RELEASE_SCOPE",
                        error=(
                            f"Strategy {request.strategy_id!r} cannot originate in this release; "
                            "archived engines are observation/management only; supported originators are Gamma-Unwind, Snapback, SuperTrend, and Price Rules"
                        ),
                    )

        # Require a request-bound RiskApproval for all exposure-increasing submissions.
        # Boolean risk_approved=True without a RiskApproval object is strictly forbidden for INCREASE_EXPOSURE.
        if is_exposure_increasing:
            if risk_approval is None:
                return ExecutionResult(success=False, status="REJECTED", error="Risk check rejected or missing risk approval proof")

            for value in (risk_approval.available_capital, risk_approval.capital_required):
                if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                    return ExecutionResult(success=False, status="REJECTED", error="Risk approval capital must be finite and nonnegative")

            # A missing/future clock cannot authorize a live send.
            now_ms = int(time.time() * 1000)
            if not 0 < risk_approval.timestamp_ms <= now_ms or now_ms - risk_approval.timestamp_ms > 60000:
                return ExecutionResult(success=False, status="REJECTED", error="Risk approval proof expired or invalid timestamp")

            valid_approval = (
                risk_approval.approved is True
                and risk_approval.uid == request.uid
                and risk_approval.account_id == request.account_id
                and risk_approval.symbol.upper() == request.symbol.upper()
                and risk_approval.side.upper() == request.side.upper()
                and risk_approval.quantity == request.quantity
                and risk_approval.generation_id == request.generation_id
                and risk_approval.strategy_id == request.strategy_id
                and risk_approval.signal_id == request.signal_id
            )
            if not valid_approval:
                return ExecutionResult(success=False, status="REJECTED", error="Risk approval proof mismatch or invalid (strategy_id/signal_id mismatch)")
        elif risk_approval is not None and not risk_approval.approved:
            return ExecutionResult(success=False, status="REJECTED", error="Risk check rejected")

        if is_exposure_increasing:
            from app.services.fee_safety import refresh as refresh_fees
            await refresh_fees(broker_client, request.uid, request.account_id)

        # Master live floor. New entries need the operator kill switch
        # (STERLING_LIVE_EXECUTION) to be off its "disabled" position and a
        # broker client that states its mode explicitly. This is deliberately
        # below every strategy: no engine, manual endpoint or future caller can
        # bypass it by forgetting its own check. Exits, protection and
        # reconciliation never come through this branch.
        if is_exposure_increasing and broker_client is not None:
            from app.core.lane_registry import (
                LIVE_EXECUTION_ENV,
                entry_transport_allowed,
                entry_transport_refusal,
            )

            if not entry_transport_allowed(broker_client):
                if entry_transport_refusal(broker_client) == "BROKER_MODE_UNKNOWN":
                    why = "the broker client does not state its mode, so it is refused as unknown"
                else:
                    why = (
                        f"new entries are halted by the operator kill switch "
                        f"({LIVE_EXECUTION_ENV}); exits and protection are unaffected"
                    )
                return ExecutionResult(
                    success=False,
                    status="HALTED",
                    error=f"Live entry orders are not permitted: {why}",
                )

        # Broker-account continuity is a real safety fact, not a
        # certification/advisory gate. Enforce it for every real broker transport,
        # regardless of ENVIRONMENT: a development-labelled process can still move
        # real money if its client is _is_paper=False. Explicit in-process test
        # doubles remain exempt. Exits/protection/reconciliation never enter here.
        if (
            is_exposure_increasing
            and getattr(broker_client, "_is_paper", None) is False
        ):
            from app.services.continuity_doctor import egress_status

            try:
                egress = egress_status()
            except Exception as exc:  # noqa: BLE001 - unreadable identity must refuse
                return ExecutionResult(
                    success=False,
                    status="REJECTED_STATIC_EGRESS",
                    error=(
                        "Live static egress is unreadable: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                )
            if egress.verified is not True:
                return ExecutionResult(
                    success=False,
                    status="REJECTED_STATIC_EGRESS",
                    error=(
                        "Live static egress is not verified: "
                        + (str(egress.reason or "").strip() or "unknown egress state")
                    ),
                )

            binding_ok, binding_reason = await self.verify_production_account_binding(
                broker_client, request
            )
            if not binding_ok:
                return ExecutionResult(
                    success=False,
                    status="REJECTED_ACCOUNT_BINDING",
                    error=binding_reason,
                )

        # Snapback family gate: an exposure-increasing live Snapback order requires
        # PASSED authoritative evidence, LIVE_ELIGIBLE state, RiskEngine approval, a
        # HEALTHY system and a reconciled broker. Protective actions stay permitted.
        if str(request.strategy_id or "").lower() == "snapback":
            from app.services.snapback_family_gate import LiveIntent

            if is_exposure_increasing:
                intent = LiveIntent.ENTER
            elif effective_effect in (ExposureEffect.CLOSE_POSITION, ExposureEffect.REDUCE_EXPOSURE):
                intent = LiveIntent.EXIT
            else:
                intent = LiveIntent.RECONCILE

            try:
                state = _snapback_server_state(request.uid)
                decision = snapback_live_gate_decision(
                    intent=intent,
                    evidence_verdict=state["evidence_verdict"],
                    live_eligible=state["live_eligible"],
                    risk_approved=bool(risk_approval and risk_approval.approved),
                    system_status=state["system_status"],
                    broker_reconciled=state["broker_reconciled"],
                    operator_override=False,
                )
            except Exception as gate_exc:
                log.error("Snapback family gate failed closed: %s", gate_exc)
                return ExecutionResult(
                    success=False,
                    status="REJECTED_BY_FAMILY_GATE",
                    error=f"Snapback family gate unavailable: {gate_exc}",
                )

            if not decision.allowed:
                log.warning(
                    "Snapback family gate blocked %s %s: %s",
                    request.side, request.symbol, decision.reasons,
                )
                return ExecutionResult(
                    success=False,
                    status="REJECTED_BY_FAMILY_GATE",
                    error="Snapback live gate: " + ", ".join(decision.reasons),
                )

        allowed, reason = self.require_execution_authority(
            uid=request.uid,
            account_id=request.account_id,
            exposure_effect=effective_effect,
            strategy_id=request.strategy_id,
            entry_request=request,
        )
        if not allowed:
            log.warning("Execution rejected by control plane: %s", reason)
            return ExecutionResult(success=False, status="HALTED", error=reason)

        if is_exposure_increasing:
            from app.core.async_work import run_blocking
            from app.services.release_entry_authority import entry_refusal

            refusal = await run_blocking(entry_refusal, request, broker_client, risk_approval.capital_required if risk_approval else None)
            if refusal:
                return ExecutionResult(success=False, status="REJECTED_RELEASE_AUTHORITY", error=refusal)

        approved_release_scope = None
        if is_exposure_increasing and getattr(broker_client, "_is_paper", None) is False:
            from app.services.release_entry_authority import entry_scope
            try:
                approved_release_scope = await run_blocking(entry_scope, request)
            except Exception:
                return ExecutionResult(success=False, status="REJECTED_RELEASE_AUTHORITY", error="Server strategy identity unavailable")

        if is_exposure_increasing and str(request.strategy_id).strip().lower() == "price_rules" and getattr(broker_client, "_is_paper", None) is False:
            from app.services.price_rules import canonical_entry_refusal
            try:
                refusal = await run_blocking(canonical_entry_refusal, request)
            except Exception:
                refusal = "Price Rules owned plan is unreadable"
            if refusal:
                return ExecutionResult(success=False, status="REJECTED_PRICE_RULES", error=refusal)

        # 1. Reserve intent in durable order journal
        payload = dict(request.payload or {})
        if approved_release_scope is not None:
            # Only server-derived identity is journaled as release evidence.
            # The caller cannot relabel a real broker intent as another mode,
            # account, source revision or experiment through payload fields.
            payload.update(approved_release_scope)
            payload.update(release_sha=approved_release_scope["runtime_release_sha"],
                           signal_id=request.signal_id, plan_version=request.generation_id,
                           evidence_type="REAL_BROKER_INTENT")
        payload["product"] = request_product
        payload["exposure_effect"] = effective_effect.value
        payload["intent_type"] = "EXIT" if effective_effect in (ExposureEffect.CLOSE_POSITION, ExposureEffect.REDUCE_EXPOSURE) else "ENTRY"
        if request.tag:
            payload["user_tag"] = request.tag

        # Capital evidence: for exposure increases, NEVER fall back to caller-supplied request capital!
        avail_cap = None
        if risk_approval and risk_approval.available_capital is not None:
            avail_cap = risk_approval.available_capital
        elif broker_client and hasattr(broker_client, "get_margins"):
            try:
                m_fn = getattr(broker_client, "get_margins")
                if callable(m_fn):
                    if inspect.iscoroutinefunction(m_fn):
                        m = await m_fn()
                    else:
                        m = m_fn()
                    if isinstance(m, dict):
                        avail_cap = float(m.get("equity", {}).get("available", {}).get("live_balance", 0.0) or m.get("available_capital", 0.0))
            except Exception:
                pass

        if is_exposure_increasing:
            if avail_cap is None or avail_cap <= 0.0:
                return ExecutionResult(
                    success=False,
                    status="REJECTED",
                    error="Missing verified capital evidence for exposure increase (caller-supplied capital fallback forbidden)",
                )
            cap_req = risk_approval.capital_required if (risk_approval and risk_approval.capital_required is not None) else 0.0
        else:
            avail_cap = avail_cap if avail_cap is not None else request.available_capital
            cap_req = request.capital_required if (risk_approval is None or risk_approval.capital_required is None) else risk_approval.capital_required

        if is_exposure_increasing:
            from app.services.shared_risk import entry_refusal, committed_value
            from app.core.async_work import run_blocking
            try:
                from math import isfinite
                committed = committed_value(request, cap_req)
                payload.pop("_shared_committed_value", None)
                if isfinite(committed):
                    payload["_shared_committed_value"] = committed
                shared_refusal = await run_blocking(entry_refusal, request, cap_req)
            except Exception:
                shared_refusal = "Shared risk settings could not be verified"
            if shared_refusal:
                return ExecutionResult(success=False, status="REJECTED_SHARED_RISK", error=shared_refusal)

        # Day-one exposure envelope: daily-loss budget, gross exposure, no
        # averaging down, and an optional lot cap. It runs after the risk
        # approval and the verified-capital evidence it measures against, and
        # before anything is journaled. Only live broker transports are held
        # to it; an in-process test double moves no capital.
        if is_exposure_increasing and getattr(broker_client, "_is_paper", None) is False:
            from app.services.live_minimum_gate import evaluate_entry

            envelope_verdict = await evaluate_entry(
                request=request,
                order_value=cap_req,
                available_capital=avail_cap,
                broker_client=broker_client,
            )
            if not envelope_verdict.allowed:
                return ExecutionResult(
                    success=False,
                    status="REJECTED_BY_LIVE_MINIMUM",
                    error="Live-minimum envelope refused this entry: "
                    + envelope_verdict.message(),
                )

        try:
            intent = order_journal.reserve(
                uid=request.uid,
                account_id=request.account_id,
                strategy_id=request.strategy_id,
                generation_id=request.generation_id,
                signal_id=request.signal_id,
                exchange=request.exchange,
                symbol=request.symbol,
                side=request.side,
                quantity=request.quantity,
                capital_required=cap_req,
                available_capital=avail_cap,
                payload=payload,
            )
        except Exception as reserve_err:
            return ExecutionResult(success=False, status="REJECTED", error=f"Journal reservation failed: {reserve_err}")

        # If already submitted/acknowledged/filled, return existing status
        if intent.state in ("SUBMITTED", "FILLED", "PARTIAL", "ACKNOWLEDGED"):
            return ExecutionResult(
                success=True,
                status=intent.state,
                intent_key=intent.intent_key,
                order_id=intent.order_id,
                filled_quantity=intent.filled_quantity,
            )

        # A reduction is exempt from entry halts only because it lowers existing
        # exposure. Prove that from fresh broker inventory BEFORE the claim: the
        # durable inventory may have gone stale after a manual trade, another
        # worker, or a reconciliation lag. A refusal here leaves an unclaimed
        # RESERVED row (never a SUBMITTING one, which would fence the account
        # and could only be settled by an operator); recovery cancels it.
        if (
            effective_effect in (ExposureEffect.CLOSE_POSITION, ExposureEffect.REDUCE_EXPOSURE)
            and intent.state == "RESERVED"
            and broker_client is not None
            and str(getattr(broker_client, "_account_id", getattr(broker_client, "account_id", "")) or "").strip()
            == str(request.account_id).strip()
        ):
            reduction_ok, reduction_reason = await self.verify_broker_reduction(
                broker_client, request
            )
            if not reduction_ok:
                return ExecutionResult(
                    success=False,
                    status="REJECTED",
                    intent_key=intent.intent_key,
                    error=reduction_reason,
                )

        # 2. Claim submission state
        claimed = order_journal.claim_submission(intent.intent_key)
        if not claimed:
            return ExecutionResult(
                success=False,
                status=intent.state,
                intent_key=intent.intent_key,
                error="Could not claim submission state (concurrent execution or intent already claimed)",
            )

        # 3. Fail closed if no authenticated broker client is provided
        if broker_client is None:
            order_journal.transition(intent.intent_key, "REJECTED", error="No authenticated broker client available")
            return ExecutionResult(
                success=False,
                status="REJECTED",
                intent_key=intent.intent_key,
                error="No authenticated broker client available for live order submission",
            )

        # 4. Strict authenticated account identity check
        client_acct = getattr(broker_client, "_account_id", getattr(broker_client, "account_id", None))
        if not client_acct or str(client_acct).strip() != str(request.account_id).strip():
            order_journal.transition(intent.intent_key, "REJECTED", error=f"Broker account identity missing or mismatch: {client_acct} != {request.account_id}")
            return ExecutionResult(
                success=False,
                status="REJECTED",
                intent_key=intent.intent_key,
                error=f"Broker client account identity ({client_acct}) missing or does not match request account identity ({request.account_id})",
            )

        # 5. Last check before the account is touched.
        #
        # Everything above — the risk approval, the family gate, the journal
        # reservation, the account identity check — takes time, and an operator
        # who engages SAFE_MODE during that window expects no order to follow.
        # Checking admission once at the top of submit_order left exactly that
        # race open, so the same authority is asked again here, with nothing
        # between it and the send.
        if is_exposure_increasing:
            still_allowed, still_reason = self.require_execution_authority(
                uid=request.uid,
                account_id=request.account_id,
                exposure_effect=effective_effect,
                strategy_id=request.strategy_id,
                ignore_intent_keys=(intent.intent_key,),
                entry_request=request,
            )
            if not still_allowed:
                order_journal.transition(
                    intent.intent_key, "REJECTED", error=f"Safety engaged before send: {still_reason}"
                )
                log.warning("Execution rejected at the broker boundary: %s", still_reason)
                return ExecutionResult(
                    success=False,
                    status="HALTED",
                    intent_key=intent.intent_key,
                    error=still_reason,
                )

        # 6. Broker Send using real KiteClient signature & arguments
        order_id = ""
        try:
            place_fn = getattr(broker_client, "place_order", None)
            if not callable(place_fn):
                raise BrokerRejected(f"Broker client {broker_client} does not expose place_order interface")

            sig = inspect.signature(place_fn)
            params = sig.parameters

            kwargs = {}
            kwargs["symbol"] = request.symbol
            kwargs["tradingsymbol"] = request.symbol
            kwargs["side"] = request.side
            kwargs["transaction_type"] = request.side
            kwargs["size"] = request.quantity
            kwargs["quantity"] = request.quantity
            kwargs["exchange"] = request.exchange or "NSE"

            # The broker and durable journal must use the same verified bucket.
            kwargs["product"] = request_product

            # Support AMO when requested by manual entry or payload; otherwise prevent silent conversion
            is_amo = (request.payload or {}).get("variety") == "amo" or bool((request.payload or {}).get("allow_amo"))
            kwargs["variety"] = "amo" if is_amo else "regular"
            kwargs["allow_amo"] = is_amo

            # Always send intent.tag as canonical broker tracking tag
            kwargs["tag"] = intent.tag
            kwargs["order_type"] = request.order_type

            if request.price > 0:
                kwargs["limit_price"] = request.price
                kwargs["price"] = request.price

            if request.trigger_price > 0:
                kwargs["trigger_price"] = request.trigger_price
                kwargs["stop_loss"] = request.trigger_price

            if request.payload:
                for k, v in request.payload.items():
                    if k not in kwargs and (k in params or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())):
                        kwargs[k] = v

            # Filter kwargs to parameters accepted by place_fn signature (or keep all if **kwargs present)
            has_var_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
            if not has_var_kwargs:
                kwargs = {k: v for k, v in kwargs.items() if k in params}

            # The broker transport refuses writes that do not carry this capability,
            # so an engine calling place_order directly cannot reach the account.
            from app.services.snapback_family_mode import (
                INTENT_INCREASE,
                INTENT_REDUCE,
                ReduceBound,
                canonical_broker_capability,
            )

            broker_intent = (
                INTENT_REDUCE
                if effective_effect in (ExposureEffect.CLOSE_POSITION, ExposureEffect.REDUCE_EXPOSURE)
                else INTENT_INCREASE
            )
            # A reduction is held at the transport to the contract, the closing side
            # and the quantity the broker inventory proof just covered.
            reduce_bound = (
                ReduceBound.closing(
                    exchange=request.exchange or "NSE", symbol=request.symbol,
                    side=request.side, quantity=request.quantity)
                if broker_intent == INTENT_REDUCE else None
            )

            def before_send() -> None:
                if not is_exposure_increasing:
                    return
                from app.services.auth.account_policy import entry_refusal as feature_refusal
                refusal = feature_refusal(request.uid)
                if refusal:
                    raise PermissionError(refusal)
                if approved_release_scope is not None:
                    from app.services.release_entry_authority import entry_scope
                    if entry_scope(request) != approved_release_scope:
                        raise PermissionError("Strategy configuration or source changed before broker send")
                if str(request.strategy_id).strip().lower() == "price_rules" and getattr(broker_client, "_is_paper", None) is False:
                    from app.services.price_rules import canonical_entry_refusal
                    refusal = canonical_entry_refusal(request)
                    if refusal:
                        raise PermissionError(refusal)
                from app.services.release_entry_authority import entry_refusal as release_refusal
                refusal = release_refusal(request, broker_client, risk_approval.capital_required if risk_approval else None)
                if refusal:
                    raise PermissionError(refusal)
                from app.services.shared_risk import entry_refusal
                try:
                    shared_refusal = entry_refusal(request, cap_req, ignore_intent_key=intent.intent_key)
                except Exception:
                    raise PermissionError("Shared risk settings could not be verified before send")
                if shared_refusal:
                    raise PermissionError(shared_refusal)
                from app.core.release_scope import may_originate
                from app.core import runtime_env
                if (getattr(broker_client, "_is_paper", None) is False or runtime_env.is_production()) and not may_originate(request.strategy_id, request.uid):
                    raise PermissionError("Strategy disabled before broker send")
                now_ms = int(time.time() * 1000)
                if not risk_approval or not 0 < risk_approval.timestamp_ms <= now_ms or now_ms - risk_approval.timestamp_ms > 60000:
                    raise PermissionError("Risk approval expired or invalid before broker send")
                if getattr(broker_client, "_account_generation", ""):
                    from app.services.exchanges.kite import accounts
                    account = accounts.get(request.uid, request.account_id)
                    if not account or not account.is_active or not accounts.client_is_current(broker_client, user_id=request.uid):
                        raise PermissionError("Broker account/session changed before send")
                allowed, reason = self.require_execution_authority(
                    uid=request.uid, account_id=request.account_id,
                    exposure_effect=effective_effect, strategy_id=request.strategy_id,
                    ignore_intent_keys=(intent.intent_key,),
                    entry_request=request,
                )
                if not allowed:
                    raise PermissionError(f"Safety engaged before send: {reason}")

            # Broker inventory may change after the first pre-reservation
            # proof (a manual trade or an independent worker can close it).
            # Recheck the exact SELL/BUY bucket at the last asynchronous
            # boundary before invoking the transport; never transform a
            # stale reduction into an exposure-increasing order.
            if not is_exposure_increasing:
                still_reducing, reduce_reason = await self.verify_broker_reduction(
                    broker_client, request,
                )
                if not still_reducing:
                    raise BrokerRejected(
                        "Fresh reduction proof changed before broker send: " + reduce_reason
                    )

            with canonical_broker_capability(
                request.signal_id or request.strategy_id or "canonical",
                intent=broker_intent,
                bound=reduce_bound,
                before_send=before_send,
            ):
                if is_exposure_increasing:
                    await run_blocking(before_send)
                if inspect.iscoroutinefunction(place_fn):
                    res = await place_fn(**kwargs)
                else:
                    res = place_fn(**kwargs)

            if isinstance(res, dict):
                order_id = str(res.get("order_id", ""))
            else:
                order_id = str(res)

            if not order_id:
                raise SubmissionOutcomeUnknown("Broker place_order returned empty order_id")

        except asyncio.CancelledError:
            # The task was cancelled somewhere inside the send: the request may
            # or may not have left. Leave no SUBMITTING row behind unflagged.
            self._mark_submission_uncertain(request, intent, "send cancelled mid-flight")
            raise
        except Exception as exc:
            is_kite_rejection = False
            try:
                from app.services.exchanges.kite.errors import (
                    KiteOrderError, KiteInputError, KiteMarginError, KitePermissionError, KiteTokenError
                )
                if isinstance(exc, (KiteOrderError, KiteInputError, KiteMarginError, KitePermissionError, KiteTokenError)):
                    is_kite_rejection = True
            except ImportError:
                pass

            if isinstance(exc, BrokerRejected) or is_kite_rejection:
                order_journal.transition(intent.intent_key, "REJECTED", error=str(exc))
                return ExecutionResult(
                    success=False,
                    status="REJECTED",
                    intent_key=intent.intent_key,
                    error=f"Broker placement rejected: {exc}",
                )
            elif _never_reached_the_broker(exc):
                order_journal.transition(
                    intent.intent_key, "REJECTED", error=f"not delivered: {type(exc).__name__}: {exc}"
                )
                return ExecutionResult(
                    success=False,
                    status="REJECTED",
                    intent_key=intent.intent_key,
                    error=f"Order not sent (broker unreachable): {exc}",
                )
            else:
                self._mark_submission_uncertain(request, intent, str(exc))
                return ExecutionResult(
                    success=False,
                    status="UNKNOWN",
                    intent_key=intent.intent_key,
                    error=f"Broker submission uncertain: {exc}",
                )

        # 6. Acknowledge order placement in journal (Broker HTTP submit ACK)
        try:
            ack_intent = order_journal.acknowledge(intent.intent_key, order_id)
        except Exception as ack_err:  # noqa: BLE001
            # The order is live at the broker; only the journal write failed. Leaving
            # the intent SUBMITTING with a CLEAN control row would hide it from
            # recovery. Fence the account and let recovery bind it by tag.
            log.error("Order %s sent but journal acknowledgement failed: %s", order_id, ack_err)
            self._mark_submission_uncertain(
                request, intent, f"order {order_id} sent; journal acknowledgement failed: {ack_err}"
            )
            return ExecutionResult(
                success=False,
                status="UNKNOWN",
                intent_key=intent.intent_key,
                order_id=order_id,
                error=f"Order {order_id} was sent but could not be journalled: {ack_err}",
            )

        return ExecutionResult(
            success=True,
            status="ACKNOWLEDGED",
            intent_key=intent.intent_key,
            order_id=order_id,
            filled_quantity=ack_intent.filled_quantity if ack_intent else 0,
        )

    submit = submit_order  # Alias for backward compatibility

    async def modify_order(
        self,
        uid: str,
        account_id: str,
        order_id: str,
        changes: Dict[str, Any],
        broker_client: Any = None,
    ) -> ExecutionResult:
        """Simulate generic amendments until a bound live amendment contract exists."""
        # Unlike submit_order, this legacy API has no durable order ownership,
        # request-bound risk approval, or broker-inventory proof. A price/type
        # change can increase risk even without a quantity change. Naming this
        # service canonical cannot grant those missing proofs, and promotion of
        # canonical entries must not silently promote generic amendments.
        from app.core.lane_registry import noncanonical_broker_write_allowed

        if broker_client is not None and not noncanonical_broker_write_allowed(broker_client):
            return ExecutionResult(
                success=False,
                status="HALTED",
                error=(
                    "Generic order modification is paper-only until canonical "
                    "amendment ownership and risk approval are implemented"
                ),
            )

        req_qty = int(changes.get("quantity") or changes.get("size") or 0)
        new_price = float(changes.get("price") or changes.get("limit_price") or changes.get("trigger_price") or 0.0)
        is_exposure_increasing = req_qty > 0 or new_price > 0
        effect = ExposureEffect.INCREASE_EXPOSURE if is_exposure_increasing else ExposureEffect.MODIFY_ORDER

        # During HALTED or RECOVERY_REQUIRED, generic modifications are strictly blocked.
        # Caller-supplied risk_reducing claims are NOT trusted; only CANCEL and dedicated protection commands are allowed.
        try:
            ctrl = db.get_execution_control(uid=uid, account_id=account_id)
            op_state = ctrl.get("operator_state", "RUNNING")
            rec_state = ctrl.get("recovery_state", "CLEAN")
            if op_state == "HALTED" or rec_state == "RECOVERY_REQUIRED":
                return ExecutionResult(
                    success=False,
                    status="HALTED",
                    error=f"Order modification blocked during control state {op_state}/{rec_state} (generic modification forbidden)",
                )
        except Exception as exc:
            log.warning("Control plane state check failed in modify_order: %s", exc)
            return ExecutionResult(
                success=False,
                status="HALTED",
                error="Order modification blocked: execution control state unavailable",
            )

        allowed, reason = self.require_execution_authority(
            uid=uid, account_id=account_id, exposure_effect=effect
        )
        if not allowed:
            return ExecutionResult(success=False, status="HALTED", error=reason)

        if broker_client is None:
            return ExecutionResult(success=False, status="REJECTED", error="No authenticated broker client available")

        client_acct = getattr(broker_client, "_account_id", getattr(broker_client, "account_id", None))
        if not client_acct or str(client_acct).strip() != str(account_id).strip():
            return ExecutionResult(success=False, status="REJECTED", error=f"Broker account identity missing or mismatch: {client_acct} != {account_id}")

        try:
            modify_fn = getattr(broker_client, "modify_order", None)
            if callable(modify_fn):
                from app.services.snapback_family_mode import (
                    INTENT_INCREASE,
                    canonical_broker_capability,
                )

                kwargs = {"order_id": order_id, **changes}
                with canonical_broker_capability(
                    f"modify:{account_id}:{order_id}",
                    intent=INTENT_INCREASE,
                ):
                    if inspect.iscoroutinefunction(modify_fn):
                        await modify_fn(**kwargs)
                    else:
                        modify_fn(**kwargs)
                return ExecutionResult(success=True, status="ACKNOWLEDGED", order_id=order_id)
            else:
                return ExecutionResult(success=False, status="REJECTED", error="Broker client does not support modify_order")
        except Exception as exc:
            return ExecutionResult(success=False, status="REJECTED", order_id=order_id, error=str(exc))

    async def cancel_order(
        self,
        uid: str,
        account_id: str,
        order_id: str,
        broker_client: Any = None,
        variety: str = "regular",
    ) -> ExecutionResult:
        """Cancel an open order (permitted even during HALTED state)."""
        allowed, reason = self.require_execution_authority(
            uid=uid, account_id=account_id, exposure_effect=ExposureEffect.CANCEL_ORDER
        )
        if not allowed:
            return ExecutionResult(success=False, status="HALTED", error=reason)

        if broker_client is None:
            return ExecutionResult(success=False, status="REJECTED", error="No authenticated broker client available")

        client_acct = getattr(broker_client, "_account_id", getattr(broker_client, "account_id", None))
        if not client_acct or str(client_acct).strip() != str(account_id).strip():
            return ExecutionResult(success=False, status="REJECTED", error=f"Broker account identity missing or mismatch: {client_acct} != {account_id}")

        # Cancellation is not intrinsically risk-reducing: cancelling an EXIT or
        # protective order can *increase* exposure. For a real broker, authorize
        # this generic primitive only when durable journal identity proves that
        # order_id belongs to an unresolved canonical ENTRY. Paper cancellation
        # remains unrestricted because it cannot mutate real broker state.
        mode = getattr(broker_client, "_is_paper", None)
        if mode is not True:
            if mode is not False:
                return ExecutionResult(
                    success=False,
                    status="REJECTED",
                    error="Cannot verify broker mode for live cancellation",
                )
            try:
                from app.services.kite_engine import order_journal

                intent = order_journal.find(
                    uid=uid,
                    account_id=account_id,
                    order_id=str(order_id),
                )
            except Exception as exc:
                return ExecutionResult(
                    success=False,
                    status="REJECTED",
                    error=f"Live cancellation identity is ambiguous/unavailable: {exc}",
                )
            if (
                intent is None
                or intent.state not in order_journal.UNRESOLVED
                or str((intent.payload or {}).get("intent_type") or "").upper() != "ENTRY"
            ):
                return ExecutionResult(
                    success=False,
                    status="REJECTED",
                    error=(
                        "Live generic cancellation is limited to unresolved "
                        "canonical ENTRY orders"
                    ),
                )

        try:
            cancel_fn = getattr(broker_client, "cancel_order", None)
            if callable(cancel_fn):
                from app.services.snapback_family_mode import (
                    INTENT_CANCEL,
                    canonical_broker_capability,
                )

                with canonical_broker_capability(
                    f"cancel:{account_id}:{order_id}",
                    intent=INTENT_CANCEL,
                ):
                    if inspect.iscoroutinefunction(cancel_fn):
                        await cancel_fn(variety=variety, order_id=order_id)
                    else:
                        cancel_fn(variety=variety, order_id=order_id)
                return ExecutionResult(success=True, status="ACKNOWLEDGED", order_id=order_id)
            else:
                return ExecutionResult(success=False, status="REJECTED", error="Broker client does not support cancel_order")
        except Exception as exc:
            return ExecutionResult(success=False, status="UNKNOWN", order_id=order_id, error=f"Cancellation outcome uncertain: {exc}")

    async def place_protection(
        self,
        uid: str,
        account_id: str,
        position_id: str,
        protection_params: Dict[str, Any],
        broker_client: Any = None,
    ) -> ExecutionResult:
        """Place GTT / stop protection order with durable protection_pending fence and cross-process lease."""
        allowed, reason = self.require_execution_authority(
            uid=uid, account_id=account_id, exposure_effect=ExposureEffect.PROTECT_POSITION
        )
        if not allowed:
            return ExecutionResult(success=False, status="HALTED", error=reason)

        if broker_client is None:
            return ExecutionResult(success=False, status="REJECTED", error="No authenticated broker client available")

        client_acct = getattr(broker_client, "_account_id", getattr(broker_client, "account_id", None))
        if not client_acct or str(client_acct).strip() != str(account_id).strip():
            return ExecutionResult(success=False, status="REJECTED", error=f"Broker account identity missing or mismatch: {client_acct} != {account_id}")

        symbol = protection_params.get("symbol", protection_params.get("tradingsymbol", ""))

        from app.services.kite_engine import execution_lease, positions
        p = positions.get(uid, symbol) if symbol else None
        if not p and position_id:
            for open_p in positions.open_positions(uid):
                if open_p.order_id == position_id or open_p.symbol == position_id:
                    p = open_p
                    symbol = p.symbol
                    break

        # Require confirmed OPEN position + exact account ownership before any GTT
        if p is None or p.status != positions.OPEN or p.qty <= 0:
            return ExecutionResult(
                success=False,
                status="REJECTED",
                error=f"No confirmed OPEN position found for symbol='{symbol}' / position_id='{position_id}'",
            )

        # Blank or unpopulated position account_id is treated as unknown/untrusted and MUST fail validation
        if not p.account_id or str(p.account_id).strip() != str(account_id).strip():
            return ExecutionResult(
                success=False,
                status="REJECTED",
                error=f"Position account identity missing or mismatch: position account '{p.account_id}' != request account '{account_id}'",
            )

        if position_id and position_id not in (p.order_id, p.symbol, f"POS_{p.order_id}", f"POS_{p.symbol}") and str(p.order_id) != str(position_id) and p.symbol != str(position_id):
            return ExecutionResult(
                success=False,
                status="REJECTED",
                error=f"position_id '{position_id}' does not match position order_id '{p.order_id}' or symbol '{p.symbol}'",
            )

        if p.protection_pending:
            return ExecutionResult(success=False, status="REJECTED", error=f"Protection placement pending/uncertain for {symbol}; reconcile before retry")

        prot_id = ""

        with execution_lease.guard(execution_lease.PROTECTION, account_id=account_id, uid=uid, symbol=symbol) as token:
            if token is None:
                return ExecutionResult(success=False, status="REJECTED", error=f"Protection lease busy for {symbol}")

            # Derive GTT parameters strictly from persisted confirmed OpenPosition
            stop_prem = float(protection_params.get("trigger_price") or protection_params.get("trigger_premium") or protection_params.get("stop_premium") or p.stop_premium or 0.0)
            target_prem = float(protection_params.get("target_premium") or p.target_premium or 0.0)
            if stop_prem <= 0:
                # A GTT exit leg is a priced LIMIT order derived from its trigger;
                # without a stop level there is nothing valid to rest at the broker.
                return ExecutionResult(
                    success=False,
                    status="REJECTED",
                    error=f"Protection stop price is missing for {symbol}; cannot place a broker stop",
                )

            from app.services.kite_engine import protective_stop

            verified, inventory_reason, resolved_product = (
                await protective_stop.verify_protection_inventory(
                    broker_client,
                    tradingsymbol=p.symbol,
                    exchange=p.exchange or "NFO",
                    qty=p.qty,
                    direction=p.direction,
                    product=getattr(p, "product", "") or "",
                )
            )
            if not verified:
                return ExecutionResult(
                    success=False,
                    status="REJECTED",
                    error=f"Protection inventory proof failed: {inventory_reason}",
                )

            ref_last = await protective_stop._reference_price(
                broker_client, p.symbol, p.exchange or "NFO", p.fill_price,
            )
            if not ref_last:
                return ExecutionResult(success=False, status="REJECTED",
                                       error="Fresh broker quote unavailable for protection")
            if (getattr(broker_client, "_is_paper", None) is False
                    and protective_stop.stop_is_breached(stop_prem, ref_last, p.direction)):
                return ExecutionResult(success=False, status="REJECTED",
                                       error="Protective stop already breached; reconcile exit before arming")

            gtt_request = protective_stop.build_gtt_request(
                tradingsymbol=p.symbol,
                exchange=p.exchange or "NFO",
                qty=p.qty,
                trigger_premium=stop_prem,
                last_price=ref_last,
                direction=p.direction,
                target_premium=target_prem,
                product=resolved_product,
            )

            if (getattr(broker_client, "_is_paper", None) is False
                    and protective_stop.protection_is_breached(gtt_request, ref_last, p.direction)):
                return ExecutionResult(success=False, status="REJECTED",
                                       error="Rounded stop or target already breached; reconcile exit before arming")

            p.protection_pending = True
            p.protection_pending_ms = int(time.time() * 1000)
            positions.persist_strict(uid)

            try:
                place_gtt_fn = getattr(broker_client, "place_gtt", None)
                if callable(place_gtt_fn):
                    from app.services.snapback_family_mode import (
                        INTENT_PROTECT,
                        canonical_broker_capability,
                    )

                    with canonical_broker_capability(
                        f"protect:{account_id}:{symbol}",
                        intent=INTENT_PROTECT,
                    ):
                        if inspect.iscoroutinefunction(place_gtt_fn):
                            res = await place_gtt_fn(**gtt_request)
                        else:
                            res = place_gtt_fn(**gtt_request)

                    prot_id = str(res.get("trigger_id", "") if isinstance(res, dict) else res or "")
                    if not prot_id.isdigit() or int(prot_id) <= 0:
                        raise RuntimeError("Broker protection acknowledgement missing a valid trigger id")
                    p.gtt_id = int(prot_id)
                    if stop_prem > 0:
                        p.stop_premium = stop_prem
                    p.product = resolved_product
                    p.protection_pending = False
                    positions.persist_strict(uid)

                    return ExecutionResult(
                        success=True,
                        status="ACKNOWLEDGED",
                        order_id=prot_id,
                    )
                p.protection_pending = False
                positions.persist_strict(uid)
                return ExecutionResult(
                    success=False,
                    status="REJECTED",
                    error="Broker client does not support place_gtt",
                )
            except Exception as exc:
                p.protection_pending = True
                positions.persist_strict(uid)
                try:
                    db.set_recovery_state(
                        recovery_state="RECOVERY_REQUIRED",
                        reason_code="PROTECTION_UNCERTAINTY",
                        reason=f"Protection placement uncertain for {symbol}: {exc}",
                        uid=uid,
                        account_id=account_id,
                    )
                except Exception as db_err:
                    log.warning("Failed to set RECOVERY_REQUIRED in DB during protection uncertainty: %s", db_err)
                return ExecutionResult(success=False, status="UNKNOWN", error=f"Protection outcome uncertain: {exc}")


    async def reconcile_order(
        self,
        *,
        uid: str,
        account_id: str,
        order_id: str,
        broker_client: Any = None,
    ) -> ExecutionResult:
        """Read one canonical broker order and durably observe it, never send.

        Only existing journaled, account-owned orders are eligible. A missing,
        ambiguous, cross-account, or malformed broker response cannot be used to
        clear UNKNOWN/recovery state. This operation intentionally does NOT
        project strategy inventory or resolve account-wide recovery readiness.
        """
        from app.services.execution_identity_lock import account_lock

        if any(
            not isinstance(value, str) or not value.strip() or value != value.strip()
            for value in (uid, account_id, order_id)
        ):
            return ExecutionResult(False, "REJECTED", error="Invalid reconciliation identity")
        if broker_client is None:
            return ExecutionResult(False, "REJECTED", error="Authenticated broker client required")
        mode = getattr(broker_client, "_is_paper", None)
        if mode is not True and mode is not False:
            return ExecutionResult(False, "REJECTED", error="Unknown broker transport mode")
        client_account = str(getattr(broker_client, "_account_id", "") or "").strip()
        if client_account != account_id:
            return ExecutionResult(False, "REJECTED", error="Broker account identity mismatch")

        async with account_lock(uid):
            try:
                intent = order_journal.find(uid=uid, account_id=account_id, order_id=order_id)
            except Exception:
                return ExecutionResult(False, "REJECTED", error="Ambiguous or unavailable canonical order identity")
            if intent is None:
                return ExecutionResult(False, "REJECTED", error="Canonical order does not exist for this account")
            if intent.reconciliation_required:
                return ExecutionResult(
                    False, "UNKNOWN", intent_key=intent.intent_key, order_id=order_id,
                    error="Existing order conflict requires independent reconciliation",
                )

            def fence(code: str) -> ExecutionResult:
                # A broker read can be uncertain, but never a reason to retry
                # a submit. Persist the halt, with a conservative in-process
                # fallback if the control database is unavailable.
                try:
                    db.set_recovery_state(
                        recovery_state="RECOVERY_REQUIRED",
                        reason_code="CANONICAL_ORDER_RECONCILIATION_FAILED",
                        reason="Canonical order " + order_id + ": " + code,
                        uid=uid,
                        account_id=account_id,
                    )
                except Exception:
                    try:
                        from app.services import live_safety

                        live_safety.set_kill_switch(
                            True, reason="Canonical order reconciliation unavailable",
                            uid=uid, reason_code="CANONICAL_ORDER_RECONCILIATION_FAILED",
                        )
                    except Exception:
                        log.critical("Cannot persist canonical reconciliation halt")
                return ExecutionResult(
                    False, "UNKNOWN", intent_key=intent.intent_key, order_id=order_id,
                    error=code,
                )

            if intent.state == "RESERVED":
                return fence("Unclaimed reservation has unexpected broker-order identity")

            # Existing shared broker parser validates history/trade identity and
            # exact nonnegative fill quantities. This path is observation-only:
            # unlike a bounded entry-fill resolver, it never cancels an order.
            try:
                from app.services.canonical_fill_reconciliation import (
                    _TERMINAL, _PENDING, _broker_snapshot,
                )

                status, filled, price, _ = await _broker_snapshot(broker_client, order_id)
            except Exception:
                return fence("Broker order read unavailable")

            if status == "IDENTITY MISMATCH":
                return fence("Broker order identity mismatch")
            if status not in _TERMINAL | _PENDING:
                return fence("Broker order status unknown")
            if status == "COMPLETE" and filled <= 0:
                return fence("Complete order lacks verified fill quantity")

            # Do not place arbitrary broker payloads (including potential
            # credentials) into the audit journal. Persist only an allowlisted
            # source attribution and fields already validated by the parser.
            raw = {
                "source": "canonical_broker_order_read",
                "order_id": order_id,
                "broker_status": status,
                "filled_quantity": filled,
                "average_price": price,
            }
            try:
                observed = order_journal.observe_order(
                    intent.intent_key,
                    status=status,
                    order_id=order_id,
                    filled_quantity=filled,
                    average_price=price,
                    raw=raw,
                )
            except Exception:
                return fence("Durable journal observation failed")
            if observed.reconciliation_required:
                return fence("Conflicting broker order evidence")
            current = observed.intent
            return ExecutionResult(
                True,
                current.state,
                intent_key=current.intent_key,
                order_id=order_id,
                filled_quantity=current.filled_quantity,
                filled_price=(
                    current.filled_value / current.filled_quantity
                    if current.filled_quantity else 0.0
                ),
                revision=current.projection_version,
            )

    async def record_execution_outcome(
        self,
        *,
        uid: str,
        account_id: str,
        order_id: str,
        broker_client: Any = None,
    ) -> ExecutionResult:
        """Record a fresh broker-read outcome through the same canonical journal.

        Caller-provided status/price/quantity are deliberately not accepted.
        The method cannot create an order, bypass authority, or settle UNKNOWN
        without account-bound broker evidence.
        """
        return await self.reconcile_order(
            uid=uid, account_id=account_id, order_id=order_id,
            broker_client=broker_client,
        )

    async def startup_recovery(
        self,
        uid: str = "default",
        account_id: str = "default",
        broker_client: Any = None,
    ) -> Dict[str, Any]:
        """Perform startup recovery workflow: inspect unresolved journal + pending projections + inventory reconciliation + protection pending state, observe broker, update recovery_state via CAS."""
        db.init()

        try:
            ctrl = db.get_execution_control(uid=uid, account_id=account_id)
        except Exception as exc:
            return {"status": "error", "reason": f"Control plane uninitialized: {exc}"}

        curr_rev = ctrl.get("revision", 0)

        # 1. Clean orphaned RESERVED intents (no submission claim won, no broker request made)
        unresolved_intents = order_journal.unresolved(uid, account_id=account_id)
        for i in unresolved_intents:
            if i.state == "RESERVED":
                order_journal.transition(i.intent_key, "CANCELLED", error="Startup recovery expired orphaned reservation")

        # 2. Inspect remaining unresolved journal items, pending projections, protection pending & strict inventory reconciliation state
        from app.services.kite_engine import positions
        unresolved_intents = order_journal.unresolved(uid, account_id=account_id)
        pending_projections = order_journal.pending_projection(uid, account_id) if hasattr(order_journal, "pending_projection") else []
        inventory_reconciliation_required = False
        try:
            invs = db.get_inventory_all_strict(account_id, uid)
            inventory_reconciliation_required = any(inv.get("reconciliation_required") for inv in invs)
        except Exception as inv_err:
            log.warning("Strict inventory read failed in startup_recovery: %s", inv_err)
            inventory_reconciliation_required = True

        protection_pending_present = False
        positions_read_error = False
        try:
            all_positions = positions.open_positions(uid) if hasattr(positions, "open_positions") else []
            protection_pending_present = any(getattr(p, "protection_pending", False) for p in all_positions)
        except Exception as pos_err:
            log.warning("Positions read failed in startup_recovery: %s — treating as recovery required", pos_err)
            positions_read_error = True

        uncertain = (
            len(unresolved_intents) > 0
            or len(pending_projections) > 0
            or inventory_reconciliation_required
            or protection_pending_present
            or positions_read_error
        )

        if uncertain:
            try:
                ctrl = db.set_recovery_state(
                    recovery_state="RECOVERY_REQUIRED",
                    reason_code="UNRESOLVED_JOURNAL_INTENTS",
                    reason=f"Startup found unresolved state: {len(unresolved_intents)} journal intent(s), {len(pending_projections)} pending projection(s), protection_pending={protection_pending_present}, pos_error={positions_read_error}",
                    uid=uid,
                    account_id=account_id,
                )
                curr_rev = ctrl.get("revision", curr_rev)
            except Exception as exc:
                log.warning("Failed to set RECOVERY_REQUIRED: %s", exc)

        # 3. Broker Reconciliation: OBSERVE ONLY, NEVER RESUBMIT
        if broker_client is not None:
            await recover(broker_client, uid=uid)

        # 4. Re-read strict state after broker recovery (Item 10)
        unresolved_intents = order_journal.unresolved(uid, account_id=account_id)
        pending_projections = order_journal.pending_projection(uid, account_id) if hasattr(order_journal, "pending_projection") else []
        inventory_reconciliation_required = False
        try:
            invs = db.get_inventory_all_strict(account_id, uid)
            inventory_reconciliation_required = any(inv.get("reconciliation_required") for inv in invs)
        except Exception as inv_err:
            log.warning("Strict inventory read after recover failed in startup_recovery: %s", inv_err)
            inventory_reconciliation_required = True

        protection_pending_present = False
        positions_read_error = False
        try:
            all_positions = positions.open_positions(uid) if hasattr(positions, "open_positions") else []
            protection_pending_present = any(getattr(p, "protection_pending", False) for p in all_positions)
        except Exception as pos_err:
            log.warning("Positions read after recover failed in startup_recovery: %s — treating as recovery required", pos_err)
            positions_read_error = True

        remaining_uncertain = (
            len(unresolved_intents) > 0
            or len(pending_projections) > 0
            or inventory_reconciliation_required
            or protection_pending_present
            or positions_read_error
        )
        from app.services.protection_health import admission_blocker
        protection_fault, _ = admission_blocker(uid, account_id)
        remaining_uncertain = remaining_uncertain or bool(protection_fault)
        recovery_write_error = False
        if not remaining_uncertain:
            try:
                ctrl = db.set_recovery_state(
                    recovery_state="CLEAN",
                    reason_code="RECONCILIATION_CLEAN",
                    reason="Startup recovery clean — broker orders reconciled",
                    uid=uid,
                    account_id=account_id,
                    expected_revision=curr_rev,
                    last_reconciled_ms=int(time.time() * 1000),
                )
            except Exception as exc:
                log.warning("CAS update to CLEAN failed in startup_recovery: %s", exc)
                recovery_write_error = True
                remaining_uncertain = True

        return {
            "status": "success",
            "operator_state": ctrl.get("operator_state", "RUNNING"),
            "recovery_state": "CLEAN" if not remaining_uncertain else "RECOVERY_REQUIRED",
            "unresolved_count": len(unresolved_intents),
            "pending_projections_count": len(pending_projections),
            "protection_pending": protection_pending_present,
            "positions_read_error": positions_read_error,
            "recovery_write_error": recovery_write_error,
            "protection_fault": protection_fault,
        }

    async def recover_unresolved(self, uid: str, account_id: str, client: Any = None) -> Dict[str, Any]:
        """Recover and project unresolved orders for account."""
        return await self.startup_recovery(uid=uid, account_id=account_id, broker_client=client)


# Global singleton instance
execution_service = CanonicalExecutionService()
