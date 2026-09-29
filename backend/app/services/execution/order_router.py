"""
Sterling v4 — OrderRouter

Single integration point for emitting orders. Wraps every safety primitive,
selects between paper / shadow / live modes, and produces a structured
response that the FastAPI endpoint converts to HTTP.

Design goals
------------
1. **Pure orchestration**.  No FastAPI dependency — fully unit-testable.
2. **Deterministic dispatch**.  Same pipeline for backtest, paper, shadow,
   and live so behavior matches.
3. **Fail-closed**.  Any uncaught exception in a safety guard rejects the
   order; the guard never silently fails-open.
4. **Idempotent**.  Identical (underlying, direction, instrument_type, size,
   minute-bucket) submissions return the prior order_id within the safety
   TTL window. Callers may also supply an explicit `client_order_id`.

Non-goals
---------
* No blind order replay. A broker/transport exception is an UNKNOWN submission:
  new exposure is halted and broker reconciliation is required before another
  entry can be attempted. The legacy RetryItem queue is not an execution worker.
* No backtest hooks — the backtest engine instantiates this class with a
  mock adapter when it wants to test live-router code paths.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from app.core.logging import get_logger
from app.services import live_safety

log = get_logger(__name__)


# ─── Modes ────────────────────────────────────────────────────────────────


class RouterMode(str, Enum):
    PAPER = "paper"      # never call exchange; record paper position only
    SHADOW = "shadow"    # simulate and record only; never call exchange
    LIVE = "live"        # call exchange; no paper record


# ─── Request / Response shapes ────────────────────────────────────────────


@dataclass
class OrderRouterRequest:
    underlying: str
    direction: str               # "long" | "short"
    instrument_type: str         # "futures" | "options"
    size: float = 1.0
    order_type: str = "market"   # "market" | "limit"
    limit_price: Optional[float] = None
    time_in_force: str = "gtc"
    reduce_only: bool = False
    # Bracket
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    trail_amount: Optional[float] = None
    # Options-only
    option_symbol: Optional[str] = None
    # Idempotency / observability
    client_order_id: Optional[str] = None
    notes: str = ""
    # Carryover for sizing decisions made upstream
    score: float = 0.0
    signal_strength: str = "SIGNAL"
    mode_name: str = "swing"     # for cooldown keying
    uid: Optional[str] = None    # user / account identity for daily loss and circuit breaker gates


@dataclass
class OrderRouterResponse:
    accepted: bool
    mode: str                    # "paper" | "shadow" | "live"
    order_id: Optional[str] = None
    paper_position_id: Optional[str] = None
    symbol: str = ""
    side: str = ""
    size: float = 0.0
    entry_price: Optional[float] = None
    status: str = "unknown"      # "filled" | "pending" | "duplicate" | "rejected"
    code: str = ""               # machine-readable when rejected
    reason: str = ""             # human-readable
    timestamp_ms: int = 0
    retry_id: Optional[str] = None    # legacy field; live submissions are never auto-retried


# ─── Hooks (dependency injection points) ──────────────────────────────────


def _noop_greeks_gate(*a, **k) -> Optional[str]:
    # LIVE must never authorize exposure because the caller forgot to wire the
    # portfolio Greeks authority. Paper/shadow never call this dependency.
    raise RuntimeError("Portfolio Greeks budget gate is not configured")


@dataclass
class RouterDeps:
    """All external dependencies pinned at construction time. Caller passes
    these explicitly so unit tests can swap each independently."""
    list_open_positions: Callable[[], List[Any]]
    create_paper_position: Callable[[OrderRouterRequest, str, float, Optional[str]], str]
    cooldown_blocked: Callable[[str, str, str, int], bool] = lambda *a, **k: False
    correlation_penalty: Callable[[str, List[Any]], float] = lambda *a, **k: 1.0
    portfolio_cap_breach: Callable[[OrderRouterRequest, List[Any]], Optional[str]] = lambda *a, **k: None
    microstructure_veto: Callable[[OrderRouterRequest], Optional[str]] = lambda *a, **k: None
    # Portfolio Greeks budget hard gate. Runs only in `_submit_live` (paper /
    # shadow are explicitly NOT gated so the user can paper-trade past caps
    # to learn from the breach). Callable may be sync or async; both are
    # awaited correctly. Default is a no-op so callers that don't supply a
    # gate continue to behave as before. Returns None on pass, or a machine-
    # readable breach string ("delta_breach:35%>30%") on reject; OrderRouter
    # echoes that string back as `code=greeks_budget_breach`. The default raises:
    # omission of the hard gate is UNKNOWN, never an implicit pass.
    greeks_budget_gate: Union[
        Callable[[OrderRouterRequest, List[Any]], Optional[str]],
        Callable[[OrderRouterRequest, List[Any]], Awaitable[Optional[str]]],
    ] = _noop_greeks_gate


# ─── Adapter shim ─────────────────────────────────────────────────────────


class _AsyncAdapterShim:
    """Minimal protocol the OrderRouter needs from an exchange adapter.
    Lets tests pass a `MagicMock(spec=...)` without importing a concrete
    broker client."""
    async def get_index_price(self, instrument: Any) -> float: ...
    async def place_order(self, **kwargs) -> Dict[str, Any]: ...
    async def place_order_option(self, **kwargs) -> Dict[str, Any]: ...
    async def get_product_id(self, symbol: str) -> int: ...
    async def cancel_replace_stop(
        self, product_id: int, side: str, size: float,
        old_stop: float, new_stop: float, take_profit: float | None = None,
    ) -> Dict[str, Any]: ...
    async def market_reduce_close(
        self, product_id: int, side: str, size: float,
    ) -> Dict[str, Any]: ...


# ─── Main router ──────────────────────────────────────────────────────────


class OrderRouter:
    """
    Stateless orchestrator. Every call to `submit()` runs the full safety
    pipeline and dispatches based on `self.mode`. Mutating `self.mode`
    between calls is supported (for hot-swapping paper↔live).
    """

    def __init__(
        self,
        mode: RouterMode | str,
        adapter: Optional[Any],
        deps: RouterDeps,
        instrument_resolver: Callable[[str], Any],
    ) -> None:
        self.mode = RouterMode(mode) if isinstance(mode, str) else mode
        self.adapter = adapter
        self.deps = deps
        self._resolve = instrument_resolver

    # ── public ──────────────────────────────────────────────────────────

    async def submit(self, req: OrderRouterRequest) -> OrderRouterResponse:
        now_ms = int(time.time() * 1000)
        sym = req.underlying.upper()

        inst = self._resolve(sym)
        if inst is None:
            return self._reject(req, "unknown_underlying", f"Unknown underlying: {sym}", now_ms)

        # 1. Composite safety gate (kill switch / daily loss / idempotency)
        idem_key = req.client_order_id or live_safety.make_idempotency_key(
            sym, req.direction, req.instrument_type, req.size,
            int(now_ms // 60_000),       # minute bucket
        )
        decision = live_safety.assert_safe_to_trade(
            positions=self.deps.list_open_positions(),
            idempotency_key=idem_key,
            uid=req.uid,
        )
        if not decision.allowed:
            if decision.code == "duplicate_order":
                prior = live_safety.check_idempotency(idem_key)
                # PENDING/UNKNOWN means the broker may already have accepted the
                # first submission. Treating that as an accepted duplicate would
                # lie about the outcome; resubmitting would risk a double position.
                if prior in ("PENDING", "UNKNOWN"):
                    return self._reject(
                        req,
                        "submission_unknown",
                        "Prior broker submission outcome is unknown; reconcile before retrying",
                        now_ms,
                    )
                return OrderRouterResponse(
                    accepted=True,
                    mode=self.mode.value,
                    order_id=prior,
                    symbol=self._symbol_for(req, inst),
                    side=self._side(req),
                    size=req.size,
                    status="duplicate",
                    code=decision.code,
                    reason=decision.reason,
                    timestamp_ms=now_ms,
                )
            return self._reject(req, decision.code, decision.reason, now_ms)

        # 2. Cooldown
        if self.deps.cooldown_blocked(sym, req.mode_name, req.direction, now_ms):
            return self._reject(req, "cooldown_active",
                                f"Cooldown active for {sym}/{req.mode_name}/{req.direction}", now_ms)

        # 3. Portfolio bucket caps
        cap_breach = self.deps.portfolio_cap_breach(req, self.deps.list_open_positions())
        if cap_breach:
            return self._reject(req, "portfolio_cap_breach", cap_breach, now_ms)

        # 4. Microstructure veto (cheap, runs late)
        micro = self.deps.microstructure_veto(req)
        if micro:
            return self._reject(req, "microstructure_veto", micro, now_ms)

        # 5. Correlation penalty applies as a *size* multiplier (not a veto).
    # Preserve the strategy's requested size here. Indian lot-size and freeze
    # quantity enforcement belongs to the broker-aware dispatcher.
        # Hard floor: penalty-scaled size below 0.01 contracts → reject.
        penalty = self.deps.correlation_penalty(sym, self.deps.list_open_positions())
        scaled_size = req.size * penalty
        if penalty < 1.0 and scaled_size < 0.01:
            return self._reject(req, "correlation_size_zero",
                                f"Correlation penalty {penalty:.2f} sized below 0.01 contracts", now_ms)
        adjusted = OrderRouterRequest(**{**req.__dict__, "size": round(scaled_size, 4)})

        # ── dispatch ───────────────────────────────────────────────────
        if self.mode == RouterMode.PAPER:
            return await self._submit_paper(adjusted, inst, idem_key, now_ms)
        if self.mode == RouterMode.SHADOW:
            return await self._submit_shadow(adjusted, inst, idem_key, now_ms)
        return await self._submit_live(adjusted, inst, idem_key, now_ms)

    # ── dispatchers ─────────────────────────────────────────────────────

    async def _submit_paper(
        self, req: OrderRouterRequest, inst: Any, idem_key: str, now_ms: int,
    ) -> OrderRouterResponse:
        entry = await self._fetch_entry_price(inst)
        pid = self.deps.create_paper_position(req, self._symbol_for(req, inst), entry, None)
        live_safety.record_idempotency(idem_key, f"paper:{pid}")
        return OrderRouterResponse(
            accepted=True, mode="paper",
            paper_position_id=pid,
            symbol=self._symbol_for(req, inst),
            side=self._side(req),
            size=req.size,
            entry_price=entry,
            status="filled",
            timestamp_ms=now_ms,
        )

    async def _submit_shadow(
        self, req: OrderRouterRequest, inst: Any, idem_key: str, now_ms: int,
    ) -> OrderRouterResponse:
        entry = await self._fetch_entry_price(inst)
        try:
            pid = self.deps.create_paper_position(
                req, self._symbol_for(req, inst), entry, None,
            )
        except Exception as exc:  # noqa: BLE001
            # SHADOW is observational only: a persistence failure must never fall
            # through to a broker send, but it also must not masquerade as a
            # strategy rejection. Surface the evidence failure explicitly so the
            # operator can repair the writer.
            log.error("shadow evidence record failed for %s: %s", self._symbol_for(req, inst), exc)
            return OrderRouterResponse(
                accepted=True,
                mode="shadow",
                symbol=self._symbol_for(req, inst),
                side=self._side(req),
                size=req.size,
                entry_price=entry,
                status="unknown",
                code="shadow_record_failed",
                reason=f"shadow paper-record failed: {exc}",
                timestamp_ms=now_ms,
            )
        live_safety.record_idempotency(idem_key, f"shadow:{pid}")
        return OrderRouterResponse(
            accepted=True, mode="shadow", paper_position_id=pid,
            symbol=self._symbol_for(req, inst), side=self._side(req),
            size=req.size, entry_price=entry, status="filled",
            timestamp_ms=now_ms,
        )

    async def _submit_live(
        self, req: OrderRouterRequest, inst: Any, idem_key: str, now_ms: int,
    ) -> OrderRouterResponse:
        if self.adapter is None:
            return self._reject(req, "no_adapter", "Live mode requires an adapter", now_ms)

        # ── Portfolio Greeks budget hard gate ───────────────────────────────
        # Runs ONLY in live (not paper/shadow). The gate refreshes open
        # positions' Greeks at current market via portfolio_greeks_aggregator
        # before checking the new order's contribution against the budget caps.
        # An unavailable risk check cannot authorize an exposure increase.
        try:
            gate_result = self.deps.greeks_budget_gate(
                req, self.deps.list_open_positions(),
            )
            if hasattr(gate_result, "__await__"):
                gate_result = await gate_result   # type: ignore[assignment]
        except Exception as _exc:
            log.warning("order router: greeks_budget_gate failed: %s", _exc)
            return self._reject(req, "greeks_budget_unknown",
                                "Portfolio Greeks budget unavailable", now_ms)
        if gate_result:
            return self._reject(
                req, "greeks_budget_breach",
                f"Portfolio Greeks budget breach: {gate_result}", now_ms,
            )

        side = self._side(req)
        symbol = self._symbol_for(req, inst)

        # Kite has no atomic entry+SL/TP bracket. This legacy router cannot claim
        # a protected live entry simply because stop/target fields were supplied:
        # the generic adapter ignores those fields for an ordinary MARKET/LIMIT
        # entry. The canonical Kite lifecycle confirms the fill and then arms a
        # durable broker-side GTT; protected entries must use that path.
        if not req.reduce_only and any(
            value is not None
            for value in (req.stop_loss, req.take_profit, req.trail_amount)
        ):
            return self._reject(
                req,
                "atomic_protection_unsupported",
                "Legacy OrderRouter cannot guarantee exchange-side entry protection; "
                "use canonical protected execution",
                now_ms,
            )

        # Production Shadow floor at the final transport boundary. The risk
        # pipeline above may still run so shadow/live diagnostics remain useful,
        # but no real or mode-unknown adapter can receive an entry while the
        # immutable global switch is off.
        from app.core.lane_registry import entry_transport_allowed
        if not entry_transport_allowed(self.adapter):
            return self._reject(
                req,
                "global_live_execution_disabled",
                "Global live execution is disabled or broker mode is unknown",
                now_ms,
            )

        # Mark pending idempotency immediately before dispatching to adapter to prevent race conditions (TOCTOU)
        live_safety.record_idempotency(idem_key, "PENDING")

        extra_kwargs: Dict[str, Any] = {}
        if req.stop_loss is not None:
            extra_kwargs["trigger_price"] = req.stop_loss
            if req.order_type in ("sl", "sl-m", "slm", "stop", "stop_loss", "stop_market") or req.reduce_only:
                extra_kwargs["kite_order_type"] = "SL" if req.limit_price else "SL-M"

        try:
            if req.instrument_type == "options":
                if not req.option_symbol:
                    if getattr(live_safety, "_IDEMPOTENCY_CACHE", None) is not None:
                        live_safety._IDEMPOTENCY_CACHE.pop(idem_key, None)
                    return self._reject(req, "missing_option_symbol",
                                        "Options orders require option_symbol", now_ms)
                order = await self.adapter.place_order_option(
                    option_symbol=req.option_symbol,
                    side="buy" if not req.reduce_only else "sell",
                    size=req.size,
                    order_type=self._api_order_type(req.order_type),
                    limit_price=req.limit_price,
                    stop_loss=req.stop_loss,
                    take_profit=req.take_profit,
                    **extra_kwargs,
                )
                symbol = req.option_symbol
            else:
                product_id = await self.adapter.get_product_id(symbol)
                order = await self.adapter.place_order(
                    symbol=symbol,
                    side=side,
                    size=req.size,
                    order_type=self._api_order_type(req.order_type),
                    limit_price=req.limit_price,
                    time_in_force=req.time_in_force,
                    reduce_only=req.reduce_only,
                    stop_loss=req.stop_loss,
                    take_profit=req.take_profit,
                    trail_amount=req.trail_amount,
                    **extra_kwargs,
                )
        except Exception as exc:
            return self._submission_unknown(
                req=req,
                idem_key=idem_key,
                now_ms=now_ms,
                symbol=symbol,
                side=side,
                detail=str(exc),
            )

        order_id = str(order.get("id") or order.get("order_id") or "")
        if not order_id:
            return self._submission_unknown(
                req=req,
                idem_key=idem_key,
                now_ms=now_ms,
                symbol=symbol,
                side=side,
                detail="Broker returned no order id",
            )

        entry_price = float(
            order.get("average_fill_price") or order.get("limit_price") or 0.0
        ) or None
        live_safety.record_idempotency(idem_key, order_id)

        return OrderRouterResponse(
            accepted=True, mode=self.mode.value,
            order_id=order_id,
            symbol=symbol, side=side, size=req.size,
            entry_price=entry_price,
            status="filled" if not req.limit_price else "pending",
            timestamp_ms=now_ms,
        )

    # ── stop amendment ──────────────────────────────────────────────────

    async def execute_partial_market_close(
        self, underlying: str, size_to_close: float, side: str,
    ) -> dict:
        """Immediately liquidate a fractional clip of an active position.

        Paper/shadow mode is a no-op (simulated fills). Live mode fires a
        market reduce-only order that cannot flip the position direction.
        """
        if self.mode != RouterMode.LIVE:
            return {"status": "ok", "mode": self.mode.value, "note": "simulated partial fill"}

        if self.adapter is None:
            return {"status": "error", "reason": "no_adapter"}

        inst = self._resolve(underlying.upper())
        if inst is None:
            return {"status": "error", "reason": f"unknown underlying: {underlying}"}

        symbol = self._symbol_for(OrderRouterRequest(underlying=underlying, direction="long", instrument_type="futures"), inst)
        try:
            product_id = await self.adapter.get_product_id(symbol)
        except Exception as exc:
            return {"status": "error", "reason": f"product_id lookup failed: {exc}"}

        # side is position direction ("long"/"short") — invert for the close
        exec_side = "sell" if side == "long" else "buy"

        try:
            result = await self.adapter.market_reduce_close(
                product_id=product_id, side=exec_side, size=size_to_close,
            )
            return {"status": "ok", "order": result}
        except Exception as exc:
            return {"status": "error", "reason": str(exc)}

    async def amend_resting_stop(
        self, underlying: str, new_stop: float, side: str, size: float,
        take_profit: float | None = None,
    ) -> dict:
        """Push a trailing-stop update to the live exchange.

        Paper mode is a no-op. Shadow mode is a no-op (shadows don't have
        real orders to amend). Live mode performs a cancel+replace
        operation on the exchange — cancelling all open bracket orders
        for this product, then placing a reduce-only carrier order with
        the new bracket_stop_loss.

        *side* is the order side of the stop bracket: "sell" to close a
        long position, "buy" to cover a short.
        """
        if self.mode != RouterMode.LIVE:
            return {"status": "ok", "mode": self.mode.value, "note": "no live orders to amend"}

        if self.adapter is None:
            return {"status": "error", "reason": "no_adapter"}

        inst = self._resolve(underlying.upper())
        if inst is None:
            return {"status": "error", "reason": f"unknown underlying: {underlying}"}

        symbol = self._symbol_for(OrderRouterRequest(underlying=underlying, direction="long", instrument_type="futures"), inst)
        try:
            product_id = await self.adapter.get_product_id(symbol)
        except Exception as exc:
            return {"status": "error", "reason": f"product_id lookup failed: {exc}"}

        try:
            result = await self.adapter.cancel_replace_stop(
                product_id=product_id,
                side=side,
                size=size,
                old_stop=0.0,
                new_stop=new_stop,
                take_profit=take_profit,
            )
            return {"status": "ok", "order": result}
        except Exception as exc:
            return {"status": "error", "reason": str(exc)}

    # ── helpers ─────────────────────────────────────────────────────────

    def _submission_unknown(
        self,
        *,
        req: OrderRouterRequest,
        idem_key: str,
        now_ms: int,
        symbol: str,
        side: str,
        detail: str,
    ) -> OrderRouterResponse:
        """Fail closed when a broker send may have reached the exchange.

        Never delete the idempotency marker and never enqueue a blind replay.
        The durable control plane is moved to RECOVERY_REQUIRED so the 60-second
        in-memory idempotency TTL cannot later reopen new-risk admission.
        """
        live_safety.record_idempotency(idem_key, "UNKNOWN")
        reason = f"Broker submission outcome unknown: {detail}"
        try:
            from app.services import db

            db.set_recovery_state(
                recovery_state="RECOVERY_REQUIRED",
                reason_code="BROKER_TRANSPORT_UNCERTAINTY",
                reason=reason,
                uid=req.uid or "default",
                account_id="default",
            )
        except Exception as recovery_exc:  # noqa: BLE001
            # Preserve an in-process hard stop even if durable recovery state
            # cannot be written. set_kill_switch updates memory before persisting.
            try:
                live_safety.set_kill_switch(
                    True,
                    reason=reason,
                    uid=req.uid or "default",
                    reason_code="BROKER_TRANSPORT_UNCERTAINTY",
                )
            except Exception:
                log.critical(
                    "Could not persist recovery state after uncertain broker submit: %s",
                    recovery_exc,
                )

        return OrderRouterResponse(
            accepted=False,
            mode=self.mode.value,
            symbol=symbol,
            side=side,
            size=req.size,
            status="unknown",
            code="submission_unknown",
            reason=reason + "; reconcile with the broker before another entry",
            timestamp_ms=now_ms,
        )

    @staticmethod
    def _side(req: OrderRouterRequest) -> str:
        if req.instrument_type == "options":
            return "buy"
        return "buy" if req.direction == "long" else "sell"

    @staticmethod
    def _api_order_type(order_type: str) -> str:
        return "limit_order" if order_type == "limit" else "market_order"

    @staticmethod
    def _symbol_for(req: OrderRouterRequest, inst: Any) -> str:
        if req.instrument_type == "options" and req.option_symbol:
            return req.option_symbol
        return req.underlying.upper()

    async def _fetch_entry_price(self, inst: Any) -> float:
        if self.adapter is None:
            return 0.0
        try:
            return float(await self.adapter.get_index_price(inst))
        except Exception:
            return 0.0

    @staticmethod
    def _reject(req: OrderRouterRequest, code: str, reason: str, now_ms: int) -> OrderRouterResponse:
        return OrderRouterResponse(
            accepted=False, mode="rejected",
            symbol=req.underlying, side="", size=req.size,
            status="rejected", code=code, reason=reason,
            timestamp_ms=now_ms,
        )
