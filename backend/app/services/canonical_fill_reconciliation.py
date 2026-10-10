"""Shared broker-fill reconciliation for canonically submitted strategy entries.

Submission acknowledgement is not fill evidence. This module owns the small
piece common to every strategy that uses the canonical order journal but keeps
its own position schema:

* observe cumulative broker quantity/value into the shared durable journal;
* never retry an ambiguous submit;
* cancel only an acknowledged, attributable working order when a bounded wait
  leaves an unfilled remainder;
* expose the exact confirmed quantity a strategy may protect; and
* let the strategy acknowledge journal projection only after its own durable
  registry has been updated.

It deliberately does NOT project into any strategy position registry.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from app.services.kite_engine import order_journal
from app.services.simulation.seam import is_instant_paper, is_live_like

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FillResolution:
    order_id: str
    intent_key: str
    status: str
    filled_quantity: int
    average_price: float
    terminal: bool
    settled: bool
    reconciliation_required: bool
    projection_version: int
    reason: str = ""
    expected_quantity: int = 0
    exchange_ts_ms: int = 0

    @property
    def has_fill(self) -> bool:
        return self.filled_quantity > 0 and self.average_price > 0


_TERMINAL = {"COMPLETE", "CANCELLED", "REJECTED"}
_PENDING = {
    "OPEN",
    "UPDATE",
    "TRIGGER PENDING",
    "PUT ORDER REQ RECEIVED",
    "VALIDATION PENDING",
    "OPEN PENDING",
    "MODIFY VALIDATION PENDING",
    "MODIFY PENDING",
    "CANCEL PENDING",
    "AMO REQ RECEIVED",
}


def _normalize_status(value: Any) -> str:
    status = str(value or "").strip().upper()
    if status in {"PARTIAL", "PARTIALLY FILLED"}:
        return "OPEN"
    if status == "EXPIRED":
        # The journal vocabulary has CANCELLED/REJECTED as zero-remainder
        # terminal states. Expiry likewise proves no further fill can occur.
        return "CANCELLED"
    return status


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        out = float(value or 0.0)
        return out if out >= 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


async def _broker_snapshot(client: Any, order_id: str) -> tuple[str, int, float, dict]:
    latest: dict[str, Any] = {}
    history = getattr(client, "get_order_history", None)
    if callable(history):
        try:
            rows = await history(order_id)
            latest = (
                rows[-1]
                if isinstance(rows, list) and rows and isinstance(rows[-1], dict)
                else rows if isinstance(rows, dict) else {}
            )
        except Exception:
            latest = {}

    status = _normalize_status(latest.get("status"))
    filled = _safe_int(
        latest.get("filled_quantity", latest.get("filled_qty"))
    )
    average = _safe_float(
        latest.get("average_price", latest.get("average_price_filled"))
    )

    trades_fn = getattr(client, "get_order_trades", None)
    if callable(trades_fn):
        try:
            trades = await trades_fn(order_id)
        except Exception:
            trades = []
        if isinstance(trades, list) and trades:
            qty = 0
            value = 0.0
            for trade in trades:
                if not isinstance(trade, dict):
                    continue
                q = _safe_int(trade.get("quantity"))
                px = _safe_float(trade.get("average_price", trade.get("price")))
                if q > 0 and px > 0:
                    qty += q
                    value += q * px
            if qty > filled:
                filled = qty
                average = value / qty if value > 0 else average

    return status, filled, average, latest


def _find_intent(uid: str, client: Any, order_id: str):
    account_id = str(getattr(client, "_account_id", "") or "").strip()
    if not account_id:
        raise ValueError("execution_account_identity_missing")
    intent = order_journal.find(
        uid=uid,
        account_id=account_id,
        order_id=str(order_id or ""),
    )
    if intent is None:
        raise ValueError("canonical_intent_not_found")
    return intent


def _observe(
    *,
    uid: str,
    client: Any,
    order_id: str,
    status: str,
    filled_quantity: int,
    average_price: float,
    raw: dict | None = None,
):
    intent = _find_intent(uid, client, order_id)
    normalized = _normalize_status(status)
    if normalized not in _TERMINAL | _PENDING:
        return intent, None
    # Zero-fill pending/cancel/reject observations legitimately have no average.
    observed = order_journal.observe_order(
        intent.intent_key,
        status=normalized,
        order_id=order_id,
        filled_quantity=max(0, int(filled_quantity)),
        average_price=float(average_price if filled_quantity > 0 else 0.0),
        raw=dict(raw or {}),
    )
    return observed.intent, observed


async def _cancel_remaining(client: Any, order_id: str) -> bool:
    cancel = getattr(client, "cancel_order", None)
    if not callable(cancel):
        return False
    from app.services.snapback_family_mode import (
        INTENT_CANCEL,
        canonical_broker_capability,
    )

    account_id = str(getattr(client, "_account_id", "") or "paper")
    try:
        with canonical_broker_capability(
            f"canonical-entry-cancel:{account_id}:{order_id}",
            intent=INTENT_CANCEL,
        ):
            await cancel(order_id)
        return True
    except Exception:
        return False


def _halt_uncertain_live(client: Any, reason: str) -> None:
    """An ambiguous real-money order is a global stop condition."""
    if getattr(client, "_is_paper", None) is not False:
        return
    try:
        from app.services import live_safety

        live_safety.set_kill_switch(True, str(reason or "broker order reconciliation required"))
    except Exception as exc:
        log.critical("Uncertain broker order halt persistence failed: %s", exc)


def _resolution(intent, order_id: str, *, status: str = "", reason: str = "") -> FillResolution:
    import json
    from app.services import db
    from app.services.kite_engine.execution_lifecycle import exchange_ms

    with db._conn() as conn:
        row = conn.execute(
            "SELECT raw_json,received_ts_ms FROM kite_order_observations "
            "WHERE intent_key=? AND disposition='accepted' ORDER BY id DESC LIMIT 1",
            (intent.intent_key,),
        ).fetchone()
    execution_ms = (exchange_ms(json.loads(row["raw_json"])) or int(row["received_ts_ms"])) if row else 0
    state = str(intent.state or "")
    terminal = state in order_journal.TERMINAL
    settled = terminal or int(intent.filled_quantity or 0) >= int(intent.quantity or 0)
    return FillResolution(
        order_id=order_id,
        intent_key=intent.intent_key,
        status=status or state,
        filled_quantity=int(intent.filled_quantity or 0),
        average_price=(
            float(intent.filled_value or 0.0) / int(intent.filled_quantity)
            if int(intent.filled_quantity or 0) > 0
            else 0.0
        ),
        terminal=terminal,
        settled=settled,
        reconciliation_required=bool(intent.reconciliation_required),
        projection_version=int(intent.projection_version or 0),
        reason=reason or str(intent.error or ""),
        expected_quantity=int(intent.quantity or 0),
        exchange_ts_ms=execution_ms,
    )


async def resolve_entry_fill(
    *,
    uid: str,
    client: Any,
    order_id: str,
    expected_quantity: int,
    reference_price: float,
    timeout_s: float = 5.0,
    cancel_unfilled: bool = True,
) -> FillResolution:
    """Resolve an acknowledged canonical entry without ever resubmitting it.

    Paper execution is synchronous, so the adapter's ACK is settled immediately
    at the caller's already-authorized reference price. Live execution polls
    bounded broker evidence. If a live order still has an unfilled remainder at
    the deadline, the attributable order is cancelled once and reconciled; a
    failed/ambiguous cancellation stays unresolved and blocks further exposure.
    """
    expected = int(expected_quantity)
    if expected <= 0:
        raise ValueError("expected_quantity_must_be_positive")
    oid = str(order_id or "").strip()
    if not oid:
        raise ValueError("order_id_required")

    intent = _find_intent(uid, client, oid)
    if int(intent.quantity) != expected:
        raise ValueError("canonical_intent_quantity_mismatch")

    if is_instant_paper(client):          # the legacy paper client fills synchronously; a simulated one reports by postback
        intent, observed = _observe(
            uid=uid,
            client=client,
            order_id=oid,
            status="COMPLETE",
            filled_quantity=expected,
            average_price=float(reference_price),
            raw={"paper": True, "status": "COMPLETE", "filled_quantity": expected},
        )
        reason = observed.reason if observed is not None else ""
        return _resolution(intent, oid, status="COMPLETE", reason=reason)

    if not is_live_like(client):
        return _resolution(
            intent,
            oid,
            status="UNKNOWN",
            reason="broker_mode_unknown",
        )

    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, float(timeout_s))
    last_status = ""
    last_raw: dict = {}

    while True:
        status, filled, average, raw = await _broker_snapshot(client, oid)
        if status == "COMPLETE" and filled == 0 and average > 0:
            # Kite COMPLETE is terminal full execution even when a reduced
            # response/fixture omits cumulative filled_quantity.
            filled = expected
        last_status, last_raw = status, raw
        if status in _TERMINAL | _PENDING:
            try:
                intent, observed = _observe(
                    uid=uid,
                    client=client,
                    order_id=oid,
                    status=status,
                    filled_quantity=filled,
                    average_price=average,
                    raw=raw,
                )
            except Exception as exc:
                latest = _find_intent(uid, client, oid)
                _halt_uncertain_live(client, f"entry journal observation failed for {oid}")
                return _resolution(
                    latest,
                    oid,
                    status=status or "UNKNOWN",
                    reason=f"journal_observation_failed:{exc}",
                )
            if intent.reconciliation_required:
                _halt_uncertain_live(client, f"entry reconciliation required for {oid}")
                return _resolution(
                    intent,
                    oid,
                    status=status,
                    reason=observed.reason if observed is not None else "reconciliation_required",
                )
            if status in _TERMINAL or int(intent.filled_quantity) >= expected:
                return _resolution(intent, oid, status=status)

        if loop.time() >= deadline:
            break
        await asyncio.sleep(0.25)

    latest = _find_intent(uid, client, oid)
    if int(latest.filled_quantity or 0) >= expected:
        return _resolution(latest, oid, status=last_status or latest.state)

    if cancel_unfilled and await _cancel_remaining(client, oid):
        cancel_deadline = loop.time() + 3.0
        while True:
            status, filled, average, raw = await _broker_snapshot(client, oid)
            if status == "COMPLETE" and filled == 0 and average > 0:
                filled = expected
            if status in _TERMINAL | _PENDING:
                try:
                    latest, observed = _observe(
                        uid=uid,
                        client=client,
                        order_id=oid,
                        status=status,
                        filled_quantity=filled,
                        average_price=average,
                        raw=raw,
                    )
                except Exception as exc:
                    latest = _find_intent(uid, client, oid)
                    _halt_uncertain_live(client, f"post-cancel entry observation failed for {oid}")
                    return _resolution(
                        latest,
                        oid,
                        status=status or "UNKNOWN",
                        reason=f"post_cancel_observation_failed:{exc}",
                    )
                if latest.reconciliation_required:
                    _halt_uncertain_live(client, f"entry reconciliation required for {oid}")
                    return _resolution(
                        latest,
                        oid,
                        status=status,
                        reason=observed.reason if observed is not None else "reconciliation_required",
                    )
                if status in _TERMINAL:
                    return _resolution(latest, oid, status=status)
            if loop.time() >= cancel_deadline:
                break
            await asyncio.sleep(0.25)

    latest = _find_intent(uid, client, oid)
    _halt_uncertain_live(client, f"entry fill unresolved for {oid}")
    return _resolution(
        latest,
        oid,
        status=last_status or "UNKNOWN",
        reason="entry_fill_unresolved_after_bounded_reconciliation",
    )



async def resolve_known_entry(
    *,
    uid: str,
    client: Any,
    order_id: str,
    reference_price: float,
    timeout_s: float = 0.0,
    cancel_unfilled: bool = True,
) -> FillResolution:
    """Resume an existing canonical entry using its durable requested quantity."""
    intent = _find_intent(uid, client, str(order_id or ""))
    return await resolve_entry_fill(
        uid=uid,
        client=client,
        order_id=str(order_id or ""),
        expected_quantity=int(intent.quantity),
        reference_price=float(reference_price),
        timeout_s=timeout_s,
        cancel_unfilled=cancel_unfilled,
    )


@dataclass(frozen=True)
class BrokerOrderResolution:
    order_id: str
    status: str
    filled_quantity: int
    average_price: float
    terminal: bool
    settled: bool
    reason: str = ""

    @property
    def has_fill(self) -> bool:
        return self.filled_quantity > 0 and self.average_price > 0


async def resolve_acknowledged_order(
    *,
    client: Any,
    order_id: str,
    expected_quantity: int,
    reference_price: float,
    timeout_s: float = 5.0,
    cancel_unfilled: bool = True,
) -> BrokerOrderResolution:
    """Resolve a known broker order that is not represented as an entry intent.

    Used for risk-reducing exits/scale-outs. It never resubmits. If a bounded
    wait leaves a live remainder, it attempts one cancellation of that exact
    acknowledged order and then reconciles cumulative fills.
    """
    expected = int(expected_quantity)
    oid = str(order_id or "").strip()
    if expected <= 0:
        raise ValueError("expected_quantity_must_be_positive")
    if not oid:
        raise ValueError("order_id_required")

    if is_instant_paper(client):
        return BrokerOrderResolution(
            order_id=oid,
            status="COMPLETE",
            filled_quantity=expected,
            average_price=float(reference_price),
            terminal=True,
            settled=True,
        )
    if not is_live_like(client):
        return BrokerOrderResolution(
            order_id=oid,
            status="UNKNOWN",
            filled_quantity=0,
            average_price=0.0,
            terminal=False,
            settled=False,
            reason="broker_mode_unknown",
        )

    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, float(timeout_s))
    status = ""
    filled = 0
    average = 0.0
    while True:
        status, filled, average, _raw = await _broker_snapshot(client, oid)
        if status == "COMPLETE" and filled == 0 and average > 0:
            filled = expected
        if status in _TERMINAL:
            if status == "COMPLETE" and 0 < filled < expected:
                return BrokerOrderResolution(
                    oid,
                    status,
                    filled,
                    average,
                    True,
                    False,
                    reason="complete_quantity_mismatch",
                )
            return BrokerOrderResolution(
                oid,
                status,
                filled,
                average,
                True,
                True,
            )
        if filled >= expected:
            return BrokerOrderResolution(
                oid,
                status or "OPEN",
                filled,
                average,
                False,
                True,
            )
        if loop.time() >= deadline:
            break
        await asyncio.sleep(0.25)

    if cancel_unfilled and await _cancel_remaining(client, oid):
        cancel_deadline = loop.time() + 3.0
        while True:
            status, filled, average, _raw = await _broker_snapshot(client, oid)
            if status == "COMPLETE" and filled == 0 and average > 0:
                filled = expected
            if status in _TERMINAL:
                if status == "COMPLETE" and 0 < filled < expected:
                    return BrokerOrderResolution(
                        oid,
                        status,
                        filled,
                        average,
                        True,
                        False,
                        reason="complete_quantity_mismatch",
                    )
                return BrokerOrderResolution(
                    oid,
                    status,
                    filled,
                    average,
                    True,
                    True,
                )
            if filled >= expected:
                return BrokerOrderResolution(
                    oid,
                    status or "OPEN",
                    filled,
                    average,
                    False,
                    True,
                )
            if loop.time() >= cancel_deadline:
                break
            await asyncio.sleep(0.25)

    return BrokerOrderResolution(
        order_id=oid,
        status=status or "UNKNOWN",
        filled_quantity=filled,
        average_price=average,
        terminal=False,
        settled=False,
        reason="broker_order_unresolved_after_bounded_reconciliation",
    )

def mark_projected(resolution: FillResolution) -> bool:
    """Acknowledge the exact journal version after strategy persistence."""
    if resolution.projection_version <= 0:
        return False
    return order_journal.mark_projected(
        resolution.intent_key,
        resolution.projection_version,
    )
