"""Tick-driven exit + fill tracking for auto-exec option positions (C / D / E).

Two event handlers, both fed by the existing per-user KiteTicker WS — no polling:

  * ``on_tick`` (C/D): for every price tick on a held contract, exit at market the
    moment the premium breaches the trail. This is intrabar and independent of the
    5-minute scan, so a fast collapse no longer rides unprotected between scans.
    When a broker GTT also guards the position, the GTT is cancelled FIRST and the
    monitor stands down entirely if that cancel cannot be confirmed on a price
    breach — at a shared trigger the broker's stop has very likely already fired,
    and a second SELL would leave the account short. See ``_exit_position``.

  * ``on_order_update`` (E): consume Kite order postbacks to confirm fills (stamp
    the real average fill price), and to mark COMPLETE/REJECTED instead of assuming
    the entry succeeded.

The monitor holds no scheduling of its own; it reacts to WS callbacks. The order
exit itself is a market SELL of the held quantity via the warm client.
"""
from __future__ import annotations

import time
from typing import Dict, Optional, Tuple

from app.core.logging import get_logger
from app.engines.common.exit_counter import exit_needs_counter_signal, get_exit_threshold
from app.engines.common.exit_counter import (
    exit_needs_counter_signal,
    get_exit_threshold,
    should_exit_on_reds,
)
from app.services.kite_engine import positions as pos
from app.services.kite_engine import protective_stop as pstop
from app.services.kite_engine import state
from app.services.kite_engine import execution_lease
from app.services.kite_engine import fill_ledger
from app.services.kite_engine import order_journal
from app.services.kite_engine import lifecycle_alerts as _alerts

log = get_logger(__name__)


# Kite order "status" values that mean the entry will never fill.
_DEAD_STATUSES = {"REJECTED", "CANCELLED"}
_FILLED_STATUS = "COMPLETE"

# (uid, symbol) whose market exit order is mid-flight. The exit paths — the WS tick
# monitor (on_tick) and the scan loop (_square_off_expiring / _time_stop_positions) —
# are separate coroutines on one event loop and each calls _exit_position, which
# awaits the SELL placement BEFORE it marks the position closed. This set is claimed
# synchronously (no await between the check and the add, so it is race-free on the
# single-threaded loop) so a second exit path for the same position bails instead of
# placing a duplicate SELL (the double-sell / naked-short bug).
class _ExitClaims:
    """Who is currently placing an exit for one position.

    Deliberately keeps the surface of the plain ``set`` it replaces. Every bail
    path in ``_exit_position`` already calls ``discard``, so the cross-process
    claim is released on ALL of them rather than on the ones someone remembered
    to update — the same reason the realized-PnL claim lives in one helper.

    Only a LIVE position takes a durable claim. A paper position has no broker to
    race for, and making simulation depend on storage would be a worse trade.
    """

    def __init__(self) -> None:
        self._local: set = set()
        self._tokens: dict = {}

    def claim(self, key, *, account_id: str) -> bool:
        """Take the exit for this position. False means somebody already has it."""
        if key in self._local:
            return False
        uid, symbol = key
        if account_id:
            try:
                token = execution_lease.acquire(
                    execution_lease.EXIT, account_id=account_id, uid=uid, symbol=symbol)
            except Exception as exc:  # noqa: BLE001
                # A claim store we cannot reach degrades a LIVE exit to
                # single-process safety, which is how two workers both sell.
                state.log(uid, "order_failed",
                          f"{symbol}: exit ownership cannot be established ({exc}); "
                          f"no order sent")
                return False
            if token is None:
                state.log(uid, "info",
                          f"{symbol}: another engine process is already exiting this "
                          f"position; standing down")
                return False
            self._tokens[key] = (account_id, token)
        self._local.add(key)
        return True

    def __contains__(self, key) -> bool:
        return key in self._local

    def add(self, key) -> None:
        self._local.add(key)

    def discard(self, key) -> None:
        self._local.discard(key)
        held = self._tokens.pop(key, None)
        if held is None:
            return
        account_id, token = held
        try:
            execution_lease.release(execution_lease.EXIT, account_id=account_id,
                                    uid=key[0], symbol=key[1], owner=token)
        except Exception as exc:  # noqa: BLE001
            log.debug("suppressed exit lease release for %s: %s", key, exc)

    def clear(self) -> None:
        for key in list(self._tokens):
            self.discard(key)
        self._local.clear()


_exiting = _ExitClaims()


def _critical(uid: str, p: "pos.OpenPosition", key: str, event: str, reason: str) -> None:
    """A state the operator must act on, queued once per incident.

    ``key`` must name the incident, not the moment: the tick monitor re-enters these
    branches on every tick, and an alert per tick is an alert nobody reads.
    """
    _alerts.emit(
        uid,
        dedupe_key=f"supertrend:{key}:{p.symbol}:{int(p.opened_ms or 0)}",
        event_type=event, title=p.symbol, severity="CRITICAL",
        detail=_alerts.build_detail(**_alerts.stop_fields(p), qty=p.qty, reason=reason),
    )


def _record_realized(uid: str, p: "pos.OpenPosition", exit_price: float) -> None:
    """Book a closed position's realized PnL (INR) into the per-day accumulator that
    backs the INR daily-loss breaker. Long: (exit − entry)·qty; short future: negated.
    Uses the confirmed fill price when known, else the intended entry premium.

    This is the LEGACY path, and it is what paper positions and any live position
    whose entry predates the signed ledger still use. A ledger-backed exit settles
    per fill in ``fill_ledger`` instead, at the price each increment actually got;
    it claims the flag below so the two can never both book the same money.

    Exactly once per position, whichever exit path gets here first. Both callers —
    this module's ``_exit_position`` and the ``on_order_update`` reconciliation — used
    to guard on ``status`` alone, which does not cover a fill postback arriving while
    ``_exit_position`` is still inside its placement await: the position is still OPEN
    there, so both booked it and the day's total came out doubled. The claim is taken
    here rather than at the call sites so no future exit path has to remember to.
    """
    entry = float(p.fill_price or p.entry_premium or 0.0)
    if entry <= 0 or not exit_price or p.qty <= 0:
        return
    if not pos.claim_realized(uid, p.symbol):
        return
    sign = 1.0 if p.direction == "long" else -1.0
    state.record_realized_pnl(uid, (float(exit_price) - entry) * p.qty * sign)


#: What the signed ledger did with an exit fill. The distinction matters because
#: it decides who books the money, and getting it wrong books it twice:
#:   BOOKED / ALREADY — the ledger owns this exit; the legacy accumulator must not
#:                      touch it. ALREADY means the ledger already had the same
#:                      evidence, which the registry can lose track of on its own.
#:   ABSENT           — this holding has no cost basis in the ledger (a paper
#:                      position, or one opened before the ledger existed), so the
#:                      legacy accumulator is the only thing that can book it.
#:   UNKNOWN          — the ledger could not answer. Book nowhere and flag: a
#:                      guessed figure feeds the INR daily-loss breaker.
BOOKED, ALREADY, ABSENT, UNKNOWN = "booked", "already", "absent", "unknown"


def _ledger_holding(uid: str, p: "pos.OpenPosition"):
    """(outcome, holding) for this position's cost basis in the signed ledger.

    A position whose entry predates the ledger — or never went through the order
    journal at all, which is every paper position — has no cost basis in it.
    Booking an exit against that would not close anything; it would open a
    phantom short at the exit price and report a realized PnL invented from it.
    """
    if not getattr(p, "account_id", "") or p.qty <= 0:
        return ABSENT, None
    try:
        holding = fill_ledger.inventory(p.account_id, uid, p.symbol)
    except Exception as exc:  # noqa: BLE001
        state.log(uid, "order_failed",
                  f"{p.symbol}: execution ledger unreadable ({exc}); realized PnL is on hold")
        return UNKNOWN, None
    if holding is None:
        return ABSENT, None
    if holding.net_quantity == 0:
        # The ledger knows this holding and says it is already flat. Whatever this
        # event is, the legacy accumulator must not invent a second closure for it.
        return ALREADY, holding
    if (holding.net_quantity > 0) != (p.direction == "long"):
        state.log(uid, "order_failed",
                  f"{p.symbol}: ledger holds {holding.net_quantity} against a "
                  f"{p.direction} position; realized PnL is on hold")
        return UNKNOWN, holding
    return BOOKED, holding


def _book_exit(uid: str, p: "pos.OpenPosition", *, order_id: str,
               cumulative_quantity: int, average_price: float, source: str,
               exchange_ts_ms: int = 0):
    """Settle one piece of cumulative exit evidence. Returns (outcome, applied).

    The ledger is handed the broker's cumulative figures unchanged and works out
    the increment itself, so a duplicate postback, a reconnect replay or a
    restart cannot book the same exit twice.
    """
    outcome, _holding = _ledger_holding(uid, p)
    if outcome != BOOKED:
        return outcome, None
    try:
        applied = fill_ledger.record(
            account_id=p.account_id, uid=uid, symbol=p.symbol, exchange=p.exchange,
            side="SELL" if p.direction == "long" else "BUY", order_id=order_id,
            cumulative_quantity=int(cumulative_quantity),
            cumulative_value=int(cumulative_quantity) * float(average_price),
            source=source, lot_size=int(getattr(p, "lot_size", 0) or 0),
            exchange_ts_ms=int(exchange_ts_ms))
    except Exception as exc:  # noqa: BLE001
        state.log(uid, "order_failed",
                  f"{p.symbol}: exit fill could not be booked to the ledger ({exc}); "
                  f"realized PnL needs reconciliation")
        return UNKNOWN, None
    if applied.reconciliation_required:
        state.log(uid, "order_failed",
                  f"{p.symbol}: contradictory exit fill evidence quarantined "
                  f"({applied.inventory.reconciliation_reason}) — realized PnL is on hold")
        return UNKNOWN, None
    if applied.accepted:
        return BOOKED, applied
    # Benign non-acceptance — a duplicate or older cumulative. The ledger already
    # holds this exit, so the legacy accumulator must stay out of it.
    return ALREADY, applied


async def _attribute_trigger_fill(client, uid: str, p: pos.OpenPosition,
                                  order: dict) -> bool:
    """Is this unmatched exit-side fill the child order of our own GTT?

    Zerodha places a trigger's child order, not us, so it carries neither our exit
    order id nor our tag — and a bare symbol match is also what another app's SELL
    looks like. Attribution therefore comes from the broker's own state rather
    than from the event: the trigger we armed must no longer be resting, and the
    holding must actually have shrunk by exactly this fill.

    Attribution only ever CLOSES a registry row; it never places an order. The
    failure it prevents is the dangerous one — believing we still hold something
    the broker already sold, and later market-selling it a second time.
    """
    filled = int(order.get("filled_quantity") or 0)
    try:
        requested = int(float(order.get("quantity") or 0))
    except (TypeError, ValueError):
        requested = 0
    if (
        filled <= 0
        or filled > p.qty
        or requested != int(p.qty)
        or str(order.get("exchange") or "").strip().upper()
            != str(p.exchange or "").strip().upper()
        or str(order.get("product") or "").strip().upper()
            != str(getattr(p, "product", "") or "").strip().upper()
    ):
        return False
    verdict = await pstop.stop_status(
        client,
        int(p.gtt_id),
        tradingsymbol=p.symbol,
        direction=p.direction,
        exchange=p.exchange,
        product=str(getattr(p, "product", "") or "NRML").upper(),
        qty=int(p.qty),
    )
    if verdict not in (pstop.STOP_TRIGGERED, pstop.GONE):
        # Still resting, or the broker could not tell us. Either way this fill is
        # not evidence that OUR trigger fired.
        return False
    held = await _broker_holding(client, uid, p.symbol, p.direction,
                                 exchange=p.exchange,
                                 product=str(getattr(p, "product", "NRML") or "NRML"),
                                 fresh=True)
    if held is None:
        return False   # unanswered, not zero
    return held == p.qty - filled


async def _resize_broker_stop(client, uid: str, p: pos.OpenPosition, old_qty: int) -> bool:
    """Re-arm the resting GTT for the quantity we actually hold.

    Returns True only when no resize is needed or the broker confirms the resize.
    A failed shrink is a protection-integrity fault: the old trigger can sell more
    than the position still owns, so mark protection reconciliation pending and
    let the normal preflight block new exposure until it is resolved.
    """
    if client is None or not p.gtt_id or p.qty <= 0 or p.qty == old_qty or p.stop_premium <= 0:
        return True
    product = pos.broker_product(p.product)
    if not product:
        p.protection_pending = True
        pos._persist(uid)
        state.log(uid, "order_failed", f"{p.symbol}: resize product identity unknown; no trigger changed")
        return False
    from app.services.snapback_family_mode import (
        INTENT_PROTECT,
        canonical_broker_capability,
    )

    with canonical_broker_capability(
        f"resize-protection:{p.account_id or 'paper'}:{p.symbol}",
        intent=INTENT_PROTECT,
    ):
        moved = await pstop.move_stop(
            client, trigger_id=p.gtt_id, tradingsymbol=p.symbol, exchange=p.exchange,
            qty=p.qty, trigger_premium=p.stop_premium,
            last_price=float(p.fill_price or p.entry_premium or p.stop_premium),
            direction=p.direction,
            target_premium=float(getattr(p, "target_premium", 0.0) or 0.0),
            product=product)
    if moved:
        p.protection_pending = False
        pos._persist(uid)
        state.log(uid, "info",
                  f"Protective GTT #{p.gtt_id} resized {old_qty} → {p.qty} qty for {p.symbol}")
        return True

    p.protection_pending = True
    pos._persist(uid)
    if p.qty < old_qty:
        state.log(uid, "order_failed",
                  f"⚠ {p.symbol}: only {p.qty} of {old_qty} remains but GTT #{p.gtt_id} still "
                  f"sells {old_qty} — protection reconciliation required before new risk")
    else:
        state.log(uid, "order_failed",
                  f"{p.symbol}: protective GTT resize could not be confirmed")
    return False


async def on_order_update(
    uid: str, order: dict, *, client=None, strict: bool = False
) -> None:
    """Handle one Kite order postback for ``uid`` (workstream E).

    Matches the postback to a registered position by tradingsymbol, then classifies
    it by order_id + transaction_type so the ENTRY fill and a PROTECTIVE-EXIT fill
    are never confused:

      * ENTRY (order_id matches the entry, or a legacy postback with no id) → confirm
        the fill price / status, or mark rejected and release the guard.
      * EXIT  (a COMPLETE on the position's exit side — SELL for a long, BUY to cover
        a short — from a *different* order id) → a broker GTT / protective stop fired
        at Zerodha. Reconcile our registry to CLOSED and release the guard, so the tick
        monitor does not later market-SELL the same position AGAIN (the double-sell /
        naked-short bug). Best-effort token unsubscribe.

    Unknown orders (manual trades, other strategies) are ignored. By default a bad
    postback must not kill the websocket loop. Signed HTTP postbacks pass strict=True
    so reconciliation failures propagate to the endpoint instead of being reported
    as routed.
    """
    strict_projection_needed = False
    try:
        symbol = str(order.get("tradingsymbol", "")).strip()
        if not symbol:
            return
        client_mode = None
        if client is not None:
            client_mode = getattr(client, "_is_paper", None)
            if client_mode is not True and client_mode is not False:
                raise RuntimeError("order_update_broker_mode_unknown")
            if client_mode is False:
                from app.services.kite_engine.execution_lifecycle import consume_order
                if await consume_order(client, uid, order):
                    return
        p = pos.get(uid, symbol)
        if p is None:
            return  # not one of ours
        if (client is not None and client_mode is False
                and (not p.account_id or p.account_id != str(getattr(client, "_account_id", "")))):
            return  # an order ID/symbol alone cannot establish account ownership
        # Journal-owned canonical events persist strictly inside consume_order().
        # Everything below is the legacy/manual/GTT-child reconciliation surface.
        # A signed HTTP postback must not report success until this projection is
        # durably written, even though the websocket path remains best-effort.
        strict_projection_needed = bool(strict)
        status = str(order.get("status", "")).upper()
        txn = str(order.get("transaction_type", "")).upper()
        oid = str(order.get("order_id", "")).strip()
        exit_side = "SELL" if p.direction == "long" else "BUY"  # side that CLOSES us
        is_entry = bool(oid) and oid == str(p.order_id)

        # Only matched strategy exits may consume quantity. Unattributed external
        # sells require broker-position reconciliation; a symbol match is insufficient.
        tag_identity_matches = False
        if p.exit_order_id in {"submitting", "unknown"} and p.exit_tag:
            try:
                reported_qty = int(float(order.get("quantity") or 0))
            except (TypeError, ValueError):
                reported_qty = 0
            requested_qty = int(getattr(p, "exit_requested_qty", 0) or 0)
            tag_identity_matches = (
                requested_qty > 0
                and str(order.get("tag") or "") == p.exit_tag
                and str(order.get("tradingsymbol") or "").strip().upper()
                    == str(p.symbol or "").strip().upper()
                and str(order.get("exchange") or "").strip().upper()
                    == str(p.exchange or "").strip().upper()
                and str(order.get("product") or "").strip().upper()
                    == str(getattr(p, "product", "") or "").strip().upper()
                and reported_qty == requested_qty
            )
        matched_exit = bool(oid) and (
            oid == p.exit_order_id or tag_identity_matches
        )
        # A GTT that fired at Zerodha reaches us as an ordinary fill for a symbol we
        # hold and nothing else. Left unattributed the registry keeps the position
        # OPEN, and the tick monitor later market-sells a position we do not own.
        via_trigger = False
        if (not matched_exit and not is_entry and txn == exit_side
                and status == _FILLED_STATUS and p.gtt_id and client is not None
                and client_mode is False
                and p.status in (pos.PENDING, pos.OPEN)
                and int(order.get("filled_quantity") or 0) > 0):
            if await _attribute_trigger_fill(client, uid, p, order):
                p.exit_order_id = oid
                p.exit_reason = p.exit_reason or "broker GTT triggered"
                pos._persist(uid)
                matched_exit = True
                via_trigger = True
                state.log(uid, "info",
                          f"{symbol}: broker trigger fill #{oid} attributed by trigger "
                          f"state and fresh holdings")
            else:
                # Something sold a contract we hold and we cannot prove it was our
                # trigger. Do not consume quantity on a symbol match alone.
                p.pnl_reconciliation_required = True
                pos._persist(uid)
                state.log(uid, "order_failed",
                          f"⚠ {symbol}: an exit-side fill (#{oid}) could not be "
                          f"attributed to this position — reconcile at Zerodha before "
                          f"trading it again")
                return
        if matched_exit and status in _DEAD_STATUSES and not order.get("filled_quantity"):
            # The exit path cancels a broker GTT before submitting its own SELL.
            # If that SELL is then rejected/cancelled with zero fills, the position
            # remains live but the old GTT is gone. Restore protection before
            # releasing the exit claim; otherwise a failed exit silently converts a
            # protected position into an unprotected one.
            p.exit_order_id = ""
            p.exit_tag = ""
            p.exit_requested_ms = 0
            p.exit_requested_qty = 0
            # The reason named an exit that did not happen. Left in place, a later
            # broker stop-out was recorded as "red count exit 1/1".
            p.exit_reason = ""
            _critical(uid, p, f"exit-rejected:{oid}", "EXIT REJECTED",
                      f"Exit order {status.lower()} with no fill; position is still open")
            if strict_projection_needed:
                pos.persist_strict(uid)
            else:
                pos._persist(uid)

            if client is not None and p.status == pos.OPEN and p.qty > 0:
                from app.services.kite_engine import execution_lifecycle as _life
                await _life._protect(client, p)
                current = pos.get(uid, symbol) or p
                broker_required = (
                    current.stop_mode in {"broker", "both"}
                    and float(current.stop_premium or 0.0) > 0
                )
                if broker_required and (
                    not int(current.gtt_id or 0)
                    or bool(getattr(current, "protection_pending", False))
                ):
                    state.log(
                        uid,
                        "order_failed",
                        f"{symbol}: exit {status}; position remains open and broker "
                        "protection could not be confirmed — new entries remain blocked",
                    )
                    return
                state.log(
                    uid,
                    "order_failed",
                    f"{symbol}: exit {status}; position remains open; protection restored",
                )
                return

            # No usable client is available to re-arm broker protection. Mark the
            # protection outcome uncertain so readiness/preflight fail closed rather
            # than allowing the account to continue as if the rejected exit were safe.
            if (
                p.stop_mode in {"broker", "both"}
                and float(p.stop_premium or 0.0) > 0
            ):
                p.protection_pending = True
                if strict_projection_needed:
                    pos.persist_strict(uid)
                else:
                    pos._persist(uid)
                state.log(
                    uid,
                    "order_failed",
                    f"{symbol}: exit {status}; position remains open but broker "
                    "protection cannot be restored without a broker client",
                )
                return

            state.log(uid, "order_failed", f"{symbol}: exit {status}; position remains open")
            return
        if (matched_exit and txn == exit_side and not is_entry
                and p.status in (pos.PENDING, pos.OPEN)):
            from math import isfinite
            from app.services.kite_engine import execution_lifecycle as _life
            filled = int(order.get("filled_quantity") or 0)
            avg = float(order.get("average_price") or 0)
            prior = p.exit_fills.get(oid, 0)
            delta = filled - prior
            if status in _DEAD_STATUSES and delta <= 0:
                p.exit_order_id = ""
                p.exit_tag = ""
                p.exit_requested_ms = 0
                p.exit_requested_qty = 0
                p.exit_reason = ""
                pos._persist(uid)

                # Common broker sequence: OPEN reports the partial fill first,
                # then CANCELLED/REJECTED repeats the same cumulative quantity.
                # The OPEN update already reduced p.qty, so delta is zero here.
                # Because the pre-exit path cancelled the old GTT, the residual
                # needs a NEW trigger now that the exit order is terminal.
                broker_required = (
                    p.qty > 0
                    and p.stop_mode in {"broker", "both"}
                    and float(p.stop_premium or 0.0) > 0
                    and not int(p.gtt_id or 0)
                )
                if broker_required:
                    if client is None:
                        p.protection_pending = True
                        pos._persist(uid)
                    else:
                        await _life._protect(client, p)
                    current = pos.get(uid, symbol) or p
                    if (
                        not int(current.gtt_id or 0)
                        or bool(getattr(current, "protection_pending", False))
                    ):
                        state.log(
                            uid,
                            "order_failed",
                            f"{symbol}: terminal exit after prior partial fill left "
                            f"{current.qty} qty without confirmed broker protection",
                        )
                    else:
                        state.log(
                            uid,
                            "order_failed",
                            f"{symbol}: terminal exit after prior partial fill; "
                            "residual broker protection restored",
                        )
                return
            if delta <= 0 or delta > p.qty or not isfinite(avg) or avg <= 0:
                return
            # Quantity evidence survives duplicate/out-of-order events and restarts.
            # Settle the money in the signed ledger, which owns cost basis and
            # realized PnL per increment; the registry only tracks quantity.
            # Named apart from the GTT cancellation's own `outcome` below.
            ledger_outcome, booked = _book_exit(
                uid, p, order_id=oid, cumulative_quantity=filled, average_price=avg,
                source="gtt" if via_trigger else "exit",
                exchange_ts_ms=_life.exchange_ms(order))
            p.exit_fills[oid] = filled
            if delta < p.qty:
                old_qty = p.qty
                p.qty -= delta
                # A partial exit used to leave its PnL unbooked and the position
                # flagged. It is now settled at the price it actually filled at, so
                # the flag is only for exits the ledger could not account for — and
                # the legacy accumulator still cannot book a partial at all.
                if ledger_outcome != BOOKED:
                    p.pnl_reconciliation_required = True
                terminal_partial = status in _DEAD_STATUSES
                if terminal_partial or status == _FILLED_STATUS:
                    p.exit_order_id = ""
                pos._persist(uid)

                broker_required = (
                    p.stop_mode in {"broker", "both"}
                    and float(p.stop_premium or 0.0) > 0
                )
                if terminal_partial and broker_required and not int(p.gtt_id or 0):
                    # The pre-exit path cancelled the old GTT before submitting the
                    # exit. A CANCELLED/REJECTED exit may still have filled partly,
                    # leaving real residual inventory but no trigger to resize.
                    # Place NEW protection for the residual; never treat gtt_id=0 as
                    # "nothing to do".
                    if client is None:
                        p.protection_pending = True
                        pos._persist(uid)
                    else:
                        await _life._protect(client, p)
                    current = pos.get(uid, symbol) or p
                    if (
                        not int(current.gtt_id or 0)
                        or bool(getattr(current, "protection_pending", False))
                    ):
                        state.log(
                            uid,
                            "order_failed",
                            f"{symbol}: terminal partial exit left {current.qty} qty "
                            "without confirmed broker protection; new entries blocked",
                        )
                else:
                    await _resize_broker_stop(client, uid, p, old_qty)

                state.log(uid, "info",
                          f"{symbol}: partial exit, {p.qty} qty remains; "
                          + (f"realized ₹{booked.realized_delta:.2f} booked"
                             if ledger_outcome == BOOKED else "PnL reconciliation required"))
                if ledger_outcome == BOOKED:
                    await _life.apply_broker_charges(client, uid, p, oid, order)
                return
            pos.close(uid, symbol, reason=f"{p.exit_reason or 'broker exit'}; fill @ ₹{avg:.2f}",
                      exit_price=avg)
            if ledger_outcome in (BOOKED, ALREADY):
                # The ledger holds this day's realized figure. Claim the registry
                # flag anyway so no other exit path adds the same money twice.
                pos.claim_realized(uid, symbol)
            elif ledger_outcome == UNKNOWN:
                # Neither store can be trusted to hold this exit's money. Booking a
                # guess into the daily-loss breaker is worse than blocking on it.
                closed = pos.get(uid, symbol)
                if closed is not None:
                    closed.pnl_reconciliation_required = True
                    pos._persist(uid)
            elif prior == 0 and len(p.exit_fills) == 1:
                _record_realized(uid, p, avg)
            # The exit may have come from somewhere other than the GTT — a hand-placed
            # SELL, another app, the Kite web order book. Anything still resting is now
            # an ORPHAN: a SELL with no position behind it, i.e. a naked short if it
            # fires. If the GTT is what filled, this cancel is a harmless no-op.
            if p.gtt_id and client is not None:
                gid = int(p.gtt_id)
                from app.services.snapback_family_mode import (
                    INTENT_CANCEL,
                    canonical_broker_capability,
                )
                with canonical_broker_capability(
                    f"cleanup-flat-protection:{p.account_id or 'paper'}:{symbol}:{gid}",
                    intent=INTENT_CANCEL,
                ):
                    outcome = await pstop.cancel_stop_result(client, gid)
                if outcome != pstop.CANCELLED and await pstop.stop_status(
                        client, gid, tradingsymbol=symbol,
                        direction=p.direction, exchange=p.exchange,
                        product=str(getattr(p, "product", "") or "NRML").upper(),
                        qty=int(p.qty)) == pstop.STOP_ACTIVE:
                    state.log(uid, "order_failed",
                              f"⚠ {symbol} is flat but its GTT #{gid} is still ACTIVE at "
                              f"Zerodha — it would sell an option you no longer own. Cancel it "
                              f"there now.")
                pos.update_stop(uid, symbol, p.stop_premium, gtt_id=0)
                _stop_probe.pop(_stop_probe_key(client, uid, p, gid), None)
            if p.guard_key:
                state.clear_auto_open(uid, p.guard_key)
            if p.token:
                try:
                    from app.services.exchanges.kite import ticker_manager
                    await ticker_manager.release(
                        uid, [p.token],
                        owner=ticker_manager.protection_owner(p.symbol),
                    )
                except Exception as _exc:  # noqa: BLE001
                    log.debug("suppressed: %s", _exc)
            state.log(uid, "order_placed",
                      f"{symbol} exit filled at broker @ ₹{avg:.2f} — position reconciled closed")
            # Costs last. A resting GTT with nothing behind it is a naked short, so
            # cancelling it must not wait on a charges round-trip.
            if ledger_outcome == BOOKED:
                await _life.apply_broker_charges(client, uid, p, oid, order)
            return

        filled = int(float(order.get("filled_quantity") or 0) or 0)

        # A COMPLETE that is not the entry and carries no order_id lands here too. It must
        # not RESURRECT a position we already closed: mark_filled would flip it back to
        # OPEN at the exit price, and the next tick would sell it a second time.
        if (status == _FILLED_STATUS and is_entry
                and p.status in (pos.PENDING, pos.OPEN)):
            avg = float(order.get("average_price") or 0.0)
            old_qty = int(p.qty or 0)
            pos.mark_filled(uid, symbol, avg, filled_qty=filled, order_id=oid)
            qty_note = f" ({filled} qty)" if filled and filled != old_qty else ""
            state.log(uid, "info",
                      f"Fill confirmed: {symbol} @ ₹{avg:.2f}{qty_note} (#{oid})")
            await _resize_broker_stop(client, uid, p, old_qty)
        elif status in _DEAD_STATUSES and is_entry:
            if filled > 0:
                # PARTIALLY filled then cancelled: we DO hold something. Treating this
                # as a rejection would leave a real position with no registry entry, no
                # stop, no monitor and no expiry square-off — invisible and unguarded.
                avg = float(order.get("average_price") or 0.0)
                old_qty = int(p.qty or 0)
                pos.mark_filled(uid, symbol, avg, filled_qty=filled, order_id=oid)
                state.log(uid, "info",
                          f"⚠ {symbol} {status.lower()} after a PARTIAL fill — holding "
                          f"{filled} qty @ ₹{avg:.2f}; protection kept on the filled part")
                # …and resized to it. The GTT was armed for the full intended size.
                await _resize_broker_stop(client, uid, p, old_qty)
                return
            # Nothing filled → the position does not exist. Any protective GTT armed for
            # it is now an ORPHAN: a resting SELL with nothing to sell, which opens a
            # naked short if it ever triggers. Cancel it before forgetting the position.
            if p.gtt_id and client is not None:
                from app.services.snapback_family_mode import (
                    INTENT_CANCEL,
                    canonical_broker_capability,
                )
                with canonical_broker_capability(
                    f"cleanup-rejected-protection:{p.account_id or 'paper'}:{symbol}:{p.gtt_id}",
                    intent=INTENT_CANCEL,
                ):
                    outcome = await pstop.cancel_stop_result(client, p.gtt_id)
                if outcome == pstop.CANCELLED:
                    state.log(uid, "info", f"Protective GTT #{p.gtt_id} cancelled ({symbol} never filled)")
                else:
                    state.log(uid, "order_failed",
                              f"⚠ {symbol} never filled but its GTT #{p.gtt_id} could NOT be "
                              f"cancelled ({outcome}) — check Zerodha for a resting SELL")
                pos.update_stop(uid, symbol, p.stop_premium, gtt_id=0)
            elif p.gtt_id:
                state.log(uid, "order_failed",
                          f"⚠ {symbol} never filled and its GTT #{p.gtt_id} was left armed "
                          f"(no broker client on this postback path) — cancel it in Zerodha")
            pos.mark_rejected(uid, symbol, reason=str(order.get("status_message") or status))
            # Entry never filled → release the auto-open guard so the slot can re-enter.
            if p.guard_key:
                state.clear_auto_open(uid, p.guard_key)
            state.log(uid, "order_failed", f"{symbol} {status.lower()} — guard released")
    except Exception as exc:  # noqa: BLE001
        if strict:
            raise
        log.warning("kite monitor on_order_update error for %s: %s", uid, exc)
    finally:
        if strict_projection_needed:
            # Best-effort legacy mutators above call _persist(), which intentionally
            # swallows write errors. The signed postback path closes that hole here:
            # if the registry cannot be durably projected, propagate failure so the
            # endpoint returns routed=false and recovery/reconciliation remains visible.
            pos.persist_strict(uid)


#: Account/credential/venue/product/symbol/GTT-scoped verdict cache.  GTT ids are
#: broker-account state; keying only by uid+symbol+id lets a credential/account switch
#: reuse a verdict from the wrong broker book when ids collide.
_stop_probe: Dict[tuple, Tuple[float, str]] = {}
_PROBE_TTL_S = 15.0


def _stop_probe_key(client, uid: str, p: pos.OpenPosition, trigger_id: int) -> tuple:
    return (
        uid,
        str(getattr(client, "_account_id", "") or getattr(p, "account_id", "") or ""),
        str(getattr(client, "_account_generation", "") or ""),
        str(getattr(p, "exchange", "") or "").strip().upper(),
        str(getattr(p, "product", "") or "").strip().upper(),
        str(getattr(p, "symbol", "") or "").strip().upper(),
        int(trigger_id),
    )

#: Account/credential/venue/product scoped snapshots. Live exits bypass the cache.
_holdings_probe: Dict[tuple, Tuple[float, Dict[str, int]]] = {}
_HOLDINGS_TTL_S = 10.0


async def _broker_holding(client, uid: str, symbol: str, direction: str = "long", *,
                          exchange: str = "NFO", product: str = "NRML",
                          fresh: bool = False) -> Optional[int]:
    """Net quantity aligned with the position direction, or None if that
    cannot be determined.

    None and 0 mean different things and must not be conflated. 0 is positive evidence
    that the position is gone — Kite keeps a squared-off row in ``net`` with
    ``quantity: 0`` for the rest of the day. None means the question was not answered:
    the read failed, or the symbol has no row at all, which is what a position carried
    from a previous day looks like. Live exits require a fresh, fully attributed row.
    """
    now = time.monotonic()
    is_live = getattr(client, "_is_paper", True) is False
    account_id = str(getattr(client, "_account_id", "") or "")
    if is_live and not account_id:
        return None
    cache_key = (uid, account_id or id(client),
                 str(getattr(client, "_account_generation", "")), exchange, product)
    cached = None if fresh else _holdings_probe.get(cache_key)
    if cached is None or now - cached[0] > _HOLDINGS_TTL_S:
        try:
            raw = await client.get_positions_raw()
        except Exception as exc:  # noqa: BLE001
            log.debug("kite holdings probe failed for %s: %s", uid, exc)
            return None
        if not isinstance(raw, dict) or not isinstance(raw.get("net"), list):
            return None
        book: Dict[str, int] = {}
        ambiguous = set()
        for row in raw["net"]:
            if not isinstance(row, dict):
                continue
            sym = str(row.get("tradingsymbol", "")).strip().upper()
            if not sym:
                continue
            venue = row.get("exchange")
            broker_product = row.get("product")
            if (venue != exchange and (is_live or venue is not None)) or (
                    broker_product != product and (is_live or broker_product is not None)):
                continue
            try:
                from math import isfinite
                quantity = float(row["quantity"])
                if not isfinite(quantity) or not quantity.is_integer():
                    raise ValueError("invalid_position_quantity")
            except (TypeError, ValueError):
                ambiguous.add(sym)
                continue
            except KeyError:
                ambiguous.add(sym)
                continue
            if sym in book:
                ambiguous.add(sym)
            book[sym] = int(quantity)
        for sym in ambiguous:
            book.pop(sym, None)
        _holdings_probe[cache_key] = (now, book)
        cached = _holdings_probe[cache_key]
    quantity = cached[1].get(symbol.strip().upper())
    return None if quantity is None else quantity * (-1 if direction == "short" else 1)


def forget_holdings(uid: str = "") -> None:
    """Drop the cached portfolio read — after our own order changes it, and in tests."""
    if uid:
        for key in list(_holdings_probe):
            if key[0] == uid:
                _holdings_probe.pop(key, None)
    else:
        _holdings_probe.clear()


async def _broker_stop_status(client, uid: str, p: pos.OpenPosition) -> str:
    """``protective_stop.stop_status`` for this position, rate-limited (see _stop_probe).

    The symbol and direction are required, not optional: without them the probe cannot
    consult the order book and a trigger the broker no longer holds comes back
    UNVERIFIED, which stands a price-stop exit down instead of performing it.
    """
    key = _stop_probe_key(client, uid, p, int(p.gtt_id))
    now = time.monotonic()
    hit = _stop_probe.get(key)
    if hit is not None and (now - hit[0]) < _PROBE_TTL_S:
        return hit[1]
    status = await pstop.stop_status(
        client,
        int(p.gtt_id),
        tradingsymbol=p.symbol,
        direction=p.direction,
        exchange=p.exchange,
        product=str(getattr(p, "product", "") or "NRML").upper(),
        qty=int(p.qty),
    )
    _stop_probe[key] = (now, status)
    return status


async def _exit_position(client, uid: str, p: pos.OpenPosition, ltp: float,
                         reason: Optional[str] = None, *,
                         price_stop_exit: bool = False) -> bool:
    """Market-exit the full quantity and clear any broker GTT (trail breach, red count,
    target, expiry, time stop, manual close). Returns True only when an exit order was
    actually placed — a caller that reports "exited" on a bail would tell the user (and
    the activity log) that a position was closed while it is still open.
    For options: always SELL. For futures: exit = opposite side (SELL if long, BUY if short).

    ``price_stop_exit`` is the caller's INTENT, and only the price-trail branch of
    ``on_tick`` may pass True. It is a parameter rather than something inferred from
    ``ltp`` because the two are not the same question: a manual exit passes the stop
    itself as its price, and an expiry square-off or a red-count exit routinely
    happens with the premium already below the stop. Inferring intent from price
    classified all of those as price-stop exits and stood down on them — i.e. the
    exits that the broker's stop will NEVER perform for us were the ones we skipped.
    """
    # Claim the exit synchronously (single-threaded loop → check-then-add is atomic):
    # if another exit path already holds this claim, or the position is no longer live,
    # bail without placing a duplicate SELL. An ambiguous submission keeps a persisted
    # order claim; only an attributable confirmed fill can close the position.
    key = (uid, p.symbol)
    if p.exit_order_id or p.status not in (pos.OPEN, pos.PENDING):
        return False
    mode = getattr(client, "_is_paper", None)
    if mode is not True and mode is not False:
        state.log(uid, "order_failed", f"{p.symbol}: exit broker mode unknown; protection retained")
        return False
    if not _exiting.claim(key, account_id=str(getattr(p, "account_id", "") or "")):
        return False
    is_futures = p.vehicle == "futures"
    exit_side = "sell" if p.direction == "long" else "buy"
    is_live = mode is False
    product = pos.broker_product(getattr(p, "product", ""))
    if not product:
        _exiting.discard(key)
        state.log(uid, "order_failed", f"{p.symbol}: exit product identity unknown; protection retained")
        return False
    if is_live:
        if p.protection_pending:
            _exiting.discard(key)
            state.log(uid, "order_failed", f"{p.symbol}: unknown broker protection outcome; rival exit blocked")
            return False
        from app.services.exchanges.kite import accounts as kite_accounts
        account_id = str(getattr(client, "_account_id", "") or "")
        if (not account_id or account_id != str(getattr(p, "account_id", "") or "")
                or (getattr(client, "_account_generation", "")
                    and not kite_accounts.client_is_current(client, user_id=uid))):
            _exiting.discard(key)
            state.log(uid, "order_failed", f"{p.symbol}: exit account identity unverified; protection retained")
            return False
        try:
            pos.persist_strict(uid)
        except Exception:
            _exiting.discard(key)
            state.log(uid, "order_failed", f"{p.symbol}: durable position storage unavailable; protection retained")
            return False
        if bool(getattr(p, "entry_pending", False)):
            # A parent order can refill exposure after our exit. Cancellation ACK
            # alone is insufficient: consume the broker's final cumulative fill.
            try:
                if not p.order_id:
                    raise ValueError("entry_order_id_missing")
                try:
                    from app.services.snapback_family_mode import (
                        INTENT_CANCEL,
                        canonical_broker_capability,
                    )
                    with canonical_broker_capability(
                        f"cancel-entry-remainder:{p.account_id}:{p.symbol}:{p.order_id}",
                        intent=INTENT_CANCEL,
                    ):
                        await client.cancel_order(p.order_id)
                except Exception:
                    pass  # It may have filled before cancellation; history decides.
                history = await client.get_order_history(p.order_id)
                last = (history or [])[-1]
                if (not isinstance(last, dict)
                        or str(last.get("order_id") or "") != str(p.order_id)
                        or last.get("tradingsymbol") != p.symbol
                        or last.get("exchange") != p.exchange
                        or last.get("product") != product
                        or last.get("transaction_type") != ("BUY" if p.direction == "long" else "SELL")
                        or str(last.get("status") or "").upper() not in {"COMPLETE", "CANCELLED", "REJECTED"}):
                    raise ValueError("entry_remainder_unresolved")
                await on_order_update(uid, last, client=client)
                current = pos.get(uid, p.symbol)
                if (current is None or current.status not in (pos.OPEN, pos.PENDING)
                        or current.qty <= 0 or bool(getattr(current, "entry_pending", False))):
                    _exiting.discard(key)
                    return False
                p = current
            except Exception:
                _exiting.discard(key)
                state.log(uid, "order_failed", f"{p.symbol}: entry remainder cancellation unconfirmed; protection retained")
                return False
        held = await _broker_holding(client, uid, p.symbol, p.direction,
                                     exchange=p.exchange, product=product, fresh=True)
        if held is None or held < 0:
            _exiting.discard(key)
            state.log(uid, "order_failed", f"{p.symbol}: fresh matching broker exposure unavailable; protection retained")
            return False

    # ── the broker's stop gets out of the way FIRST ────────────────────────────
    # `_exiting` only serialises OUR coroutines; Zerodha's GTT engine never takes it.
    # With stop_mode="both" the GTT and this monitor are armed at the SAME price, and
    # market data reaches us before a fill postback does — so on a price breach the
    # GTT has very likely already fired and its SELL is live at the exchange. Selling
    # again here sells twice and leaves a NAKED SHORT option.
    #
    # Cancel first. Every exit intent can race an independently triggered GTT;
    # manual/red-count intent does not make an unconfirmed cancellation safe.
    old_gtt = int(p.gtt_id or 0)
    outcome = pstop.CANCELLED
    if p.gtt_id:
        from app.services.snapback_family_mode import (
            INTENT_REDUCE,
            canonical_broker_capability,
        )
        with canonical_broker_capability(
            f"exit-disarm:{p.account_id or 'paper'}:{p.symbol}:{p.gtt_id}",
            intent=INTENT_REDUCE,
        ):
            outcome = await pstop.cancel_stop_result(client, p.gtt_id)
        if outcome == pstop.CANCELLED:
            # The trigger is gone at the broker NOW. Project that fact before
            # submitting the market exit: a REJECTED/CANCELLED exit postback may
            # arrive while place_order_* is still awaiting, and its recovery path
            # must place a NEW GTT rather than try to modify this dead trigger id.
            p.gtt_id = 0
            _stop_probe.pop(_stop_probe_key(client, uid, p, old_gtt), None)
            try:
                if is_live:
                    pos.persist_strict(uid)
                else:
                    pos._persist(uid)
            except Exception as exc:  # noqa: BLE001
                p.protection_pending = True
                _exiting.discard(key)
                state.log(
                    uid,
                    "order_failed",
                    f"{p.symbol}: GTT #{old_gtt} was cancelled but that disarm "
                    f"could not be persisted ({exc}); no exit order sent",
                )
                return False
        if outcome != pstop.CANCELLED:
            # A failed cancel does not say what we need to know. It covers "it already
            # fired and is selling for us" (we must NOT add a second SELL) and "it is not
            # there any more and nothing will ever exit this position" (we MUST sell) —
            # the user may have deleted it in the Kite app, or it fired and its market
            # SELL was rejected. Only the broker can tell them apart, so ask.
            status = await _broker_stop_status(client, uid, p)
            if status == pstop.STOP_TRIGGERED:
                # The broker's exit is already out. This also catches the OCO's TARGET
                # leg, which fires on the way UP and so is invisible to any
                # price-against-the-stop test.
                _exiting.discard(key)
                state.log(uid, "info",
                          f"{p.symbol}: broker GTT #{p.gtt_id} has TRIGGERED ({outcome}) — "
                          f"its SELL is the exit; not placing a second one. Awaiting its fill.")
                return False
            if status != pstop.STOP_ABSENT:
                _exiting.discard(key)
                unverified = status == pstop.STOP_UNVERIFIED
                _critical(uid, p, f"exit-deferred:{int(old_gtt)}", "EXIT DEFERRED",
                          f"Broker GTT #{int(old_gtt)} could not be cancelled "
                          f"(status {status}); exit held back so it cannot sell twice"
                          + ("; COULD NOT VERIFY the stop at Zerodha" if unverified else ""))
                state.log(uid, "order_failed",
                          f"{p.symbol}: broker GTT #{p.gtt_id} could not be cancelled "
                          f"({outcome}); broker status {status}. Exit deferred until "
                          f"cancellation or absence is confirmed." + (" ⚠ COULD NOT VERIFY — check Zerodha now, this position "
                                      "may have no stop at all." if unverified else
                                      " Broker stop remains active; cancellation must be resolved."))
                return False
            if status == pstop.STOP_ABSENT:
                # Same invariant as a confirmed cancel: this id no longer names
                # usable broker protection, so clear it before an exit rejection
                # can race back through on_order_update().
                p.gtt_id = 0
                _stop_probe.pop(_stop_probe_key(client, uid, p, old_gtt), None)
                try:
                    if is_live:
                        pos.persist_strict(uid)
                    else:
                        pos._persist(uid)
                except Exception as exc:  # noqa: BLE001
                    p.protection_pending = True
                    _exiting.discard(key)
                    state.log(
                        uid,
                        "order_failed",
                        f"{p.symbol}: broker confirms GTT #{old_gtt} absent but "
                        f"that state could not be persisted ({exc}); no exit order sent",
                    )
                    return False
                state.log(uid, "order_failed",
                          f"⚠ {p.symbol}: GTT #{old_gtt} is NOT at the broker any more "
                          f"({outcome}/{status}) — exiting from here instead of waiting for "
                          f"a fill that is never coming")

    # ── never sell what we do not hold ────────────────────────────────────────
    # The last guard, and the only one that depends on neither a postback nor the
    # broker's GTT bookkeeping: ask what we actually own. It catches every remaining
    # way the registry can be ahead of reality — a square-off done in the Kite app
    # (no trigger involved at all, so nothing above notices), an exit whose postback
    # was lost before the next scan's reconcile pass, or a partial exit elsewhere.
    # In each case the alternative is a SELL against nothing: a naked short.
    held = await _broker_holding(client, uid, p.symbol, p.direction,
                                 exchange=p.exchange, product=product, fresh=is_live)
    if held is None and is_live:
        _exiting.discard(key)
        if old_gtt and outcome == pstop.CANCELLED:
            pos.update_stop(uid, p.symbol, p.stop_premium, gtt_id=0)
            if hasattr(p, "protection_pending"):
                p.protection_pending = False  # cancellation is confirmed; recovery may re-arm
        pos._persist(uid)
        state.log(uid, "order_failed", f"{p.symbol}: broker exposure changed or became unavailable while preparing exit; reconciliation required")
        return False
    if held is not None and held < 0:
        _exiting.discard(key)
        p.pnl_reconciliation_required = True
        pos._persist(uid)
        state.log(uid, "order_failed", f"{p.symbol}: broker exposure direction differs; reconcile before exit")
        return False
    if held == 0:
        # Positive evidence the position is gone. Self-heal rather than sell.
        _exiting.discard(key)
        if old_gtt and outcome != pstop.CANCELLED:
            from app.services.snapback_family_mode import (
                INTENT_CANCEL,
                canonical_broker_capability,
            )
            with canonical_broker_capability(
                f"cleanup-reconciled-protection:{p.account_id or 'paper'}:{p.symbol}:{old_gtt}",
                intent=INTENT_CANCEL,
            ):
                await pstop.cancel_stop_result(client, old_gtt)
        pos.update_stop(uid, p.symbol, p.stop_premium, gtt_id=0)
        _stop_probe.pop(_stop_probe_key(client, uid, p, old_gtt), None)
        p.pnl_reconciliation_required = True
        pos.close(uid, p.symbol, reason="reconciled closed at broker (holds none; fills require reconciliation)")
        if p.guard_key:
            state.clear_auto_open(uid, p.guard_key)
        if p.token:
            try:
                from app.services.exchanges.kite import ticker_manager
                await ticker_manager.release(
                        uid, [p.token],
                        owner=ticker_manager.protection_owner(p.symbol),
                    )
            except Exception as _exc:  # noqa: BLE001
                log.debug("suppressed: %s", _exc)
        state.log(uid, "info",
                  f"{p.symbol}: the broker holds none of this — the position was already "
                  f"closed elsewhere. Reconciled CLOSED instead of placing a SELL that "
                  f"would have opened a short.")
        return False
    if held is not None and 0 < held < p.qty:
        # A partial exit happened outside the engine. Sell what is there; selling the
        # registry's larger figure would short the difference.
        was = p.qty
        p.pnl_reconciliation_required = True
        pos.mark_filled(uid, p.symbol, p.fill_price, filled_qty=held)
        state.log(uid, "info",
                  f"{p.symbol}: broker holds {held} of {was} — exiting {held} "
                  f"(the rest was closed outside the engine)")

    # ── did this position close while we were awaiting? ───────────────────────
    # Everything above awaits: the GTT cancel, the status probe, the holdings read. An
    # exit fill can land at any of them, and `on_order_update` — which does NOT take
    # the `_exiting` claim — would then have closed this position and booked its
    # realized PnL already. The `p` we were handed is a snapshot from before those
    # awaits, so re-read the registry rather than trusting it: continuing would place a
    # second SELL and book the same loss twice, which trips the INR daily-loss breaker
    # at half the configured limit.
    live = pos.get(uid, p.symbol)
    if live is None or live.status not in (pos.OPEN, pos.PENDING):
        _exiting.discard(key)
        state.log(uid, "info",
                  f"{p.symbol}: closed by a fill that landed while this exit was being "
                  f"prepared — not placing a second SELL")
        return False

    # Persist the in-flight claim before the network await. A timeout is ambiguous:
    # retain this claim and reconcile instead of retrying a possibly accepted SELL.
    if (is_live and getattr(client, "_account_generation", "")
            and not kite_accounts.client_is_current(client, user_id=uid)):
        _exiting.discard(key)
        state.log(uid, "order_failed", f"{p.symbol}: account execution identity changed during exit preparation")
        return False
    from uuid import uuid4
    p.exit_tag = "kx" + uuid4().hex[:18]  # Kite permits at most 20 alphanumeric chars
    p.exit_requested_ms = int(time.time() * 1000)
    p.exit_requested_qty = int(p.qty)
    p.exit_order_id = "submitting"
    p.exit_reason = reason or f"trail breach @ {ltp:.2f}"
    # Keep immutable local copies. on_order_update() can mutate this SAME
    # OpenPosition object while the REST submission below is still awaiting
    # (notably a fast REJECTED/CANCELLED postback that clears the claim and
    # re-arms broker protection). Reading p.exit_* after the await would then
    # resurrect a claim the postback already resolved.
    submitted_tag = p.exit_tag
    submitted_ms = p.exit_requested_ms
    submitted_qty = p.exit_requested_qty
    try:
        if is_live:
            pos.persist_strict(uid)
        else:
            pos._persist(uid)
    except Exception:
        _exiting.discard(key)
        state.log(uid, "order_failed", f"{p.symbol}: exit intent persistence failed; no order sent")
        return False
    try:
        from app.services.snapback_family_mode import (
            INTENT_REDUCE,
            canonical_broker_capability,
        )
        place = client.place_order_future if is_futures else client.place_order_option
        with canonical_broker_capability(
            f"exit-send:{p.account_id or 'paper'}:{p.symbol}:{submitted_tag}",
            intent=INTENT_REDUCE,
        ):
            result = await place(
                p.symbol,
                exit_side,
                submitted_qty,
                exchange=p.exchange,
                product=product,
                tag=submitted_tag,
            )
    except Exception as exc:  # noqa: BLE001
        current = pos.get(uid, p.symbol)
        if current is not None and current.status in (pos.OPEN, pos.PENDING):
            postback_already_projected = (
                current.exit_order_id != "submitting"
                or current.exit_tag != submitted_tag
                or int(current.exit_requested_ms or 0) != int(submitted_ms)
                or int(current.exit_requested_qty or 0) != int(submitted_qty)
            )
            if postback_already_projected:
                # The broker callback won the race. It may have rejected the
                # order and restored protection, or projected a partial fill.
                # Never overwrite that durable projection with UNKNOWN merely
                # because this REST await also raised.
                _exiting.discard(key)
                forget_holdings(uid)
                state.log(
                    uid,
                    "order_failed",
                    f"{p.symbol}: exit transport returned after broker postback "
                    "already reconciled the request; preserving broker projection",
                )
                return False
            current.exit_order_id = "unknown"
            current.exit_tag = submitted_tag
            current.exit_requested_ms = submitted_ms
            current.exit_requested_qty = submitted_qty
            if current is not p:
                current.pnl_reconciliation_required = True
        pos._persist(uid)
        _exiting.discard(key)
        if old_gtt and outcome == pstop.CANCELLED:
            pos.update_stop(uid, p.symbol, p.stop_premium, gtt_id=0)
        _critical(uid, p, f"exit-unknown:{submitted_tag}", "EXIT OUTCOME UNKNOWN",
                  "Exit order sent but the broker's answer was lost; check the Zerodha "
                  "order book before any retry")
        state.log(uid, "order_failed",
                  f"{p.symbol}: exit outcome UNKNOWN; reconcile broker order book before retry: {exc}")
        return False
    oid = str((result or {}).get("order_id") or "")
    # A broker postback may have completed while place() was awaiting. That
    # includes not only FILLED but a very fast REJECTED/CANCELLED callback. The
    # latter deliberately clears the exit claim and restores a GTT; writing the
    # REST acknowledgement back afterwards would resurrect a dead claim and then
    # erase the freshly restored protection.
    current = pos.get(uid, p.symbol)
    if current is not None and current.status in (pos.OPEN, pos.PENDING):
        postback_already_projected = (
            current.exit_order_id != "submitting"
            or current.exit_tag != submitted_tag
            or int(current.exit_requested_ms or 0) != int(submitted_ms)
            or int(current.exit_requested_qty or 0) != int(submitted_qty)
        )
        if postback_already_projected:
            _exiting.discard(key)
            forget_holdings(uid)
            state.log(
                uid,
                "info",
                f"{p.symbol}: broker postback reconciled exit while REST submission "
                "was awaiting; preserving the newer broker projection",
            )
            return bool(current.exit_order_id)
        if current is not p:
            # A scale-in/replacement raced the network call: preserve pending exit
            # ownership on the row that actually survives in the registry.
            current.pnl_reconciliation_required = True
            current.exit_tag = submitted_tag
            current.exit_requested_ms = submitted_ms
            current.exit_requested_qty = submitted_qty
        current.exit_order_id = oid or "unknown"
        pos._persist(uid)
    _exiting.discard(key)
    forget_holdings(uid)
    if old_gtt and outcome == pstop.CANCELLED:
        pos.update_stop(uid, p.symbol, p.stop_premium, gtt_id=0)
        _stop_probe.pop(_stop_probe_key(client, uid, p, old_gtt), None)
    state.log(uid, "order_placed" if oid else "order_failed",
              f"{p.symbol}: exit {'submitted #' + oid if oid else 'outcome UNKNOWN'}; awaiting confirmed fills")
    return bool(oid)


#: A market exit that is still resting at the exchange this long after it was sent is
#: not exiting anything. Kite turns an option MARKET order into a protected LIMIT, so
#: in a fast fall it can sit OPEN while the position stays naked: the broker stop was
#: cancelled to make room for it, and ``exit_order_id`` keeps every re-exit out.
RESTING_EXIT_CANCEL_AFTER_MS = 8_000
#: How often one position's resting exit is re-read from the tick path.
_RESTING_EXIT_PROBE_MS = 5_000
_ORDER_TERMINAL = {"COMPLETE", "CANCELLED", "REJECTED"}
_resting_probe: Dict[Tuple[str, str], int] = {}


async def resolve_resting_exit(client, uid: str, p: "pos.OpenPosition", *,
                               order: Optional[dict] = None,
                               now_ms: Optional[int] = None) -> bool:
    """Cancel an exit order that has been resting unfilled, and settle its outcome.

    Returns True when a cancellation was attempted. The final cumulative fill is read
    back from the broker and fed through ``on_order_update`` exactly as the postback
    would have been: a zero fill clears the exit claim and re-arms the stop (the
    REJECTED/CANCELLED branch), a partial fill takes the partial path and protects the
    residual. A status that is still not terminal after the cancel changes nothing,
    so a later pass retries; the claim is never cleared on a guess.
    """
    oid = str(getattr(p, "exit_order_id", "") or "")
    if not oid or oid in {"submitting", "unknown"} or client is None:
        return False
    mode = getattr(client, "_is_paper", None)
    if mode is not True and mode is not False:
        return False
    if order is None:
        try:
            history = await client.get_order_history(oid)
        except Exception as exc:  # noqa: BLE001
            log.debug("resting-exit read failed for %s/%s: %s", uid, p.symbol, exc)
            return False
        order = (history or [])[-1] if history else None
    if not isinstance(order, dict):
        return False
    status = str(order.get("status") or "").strip().upper()
    if not status or status in _ORDER_TERMINAL:
        return False  # terminal: the ordinary reconciliation consumes it
    if status.startswith("AMO"):
        return False  # an after-market order is meant to wait for the open
    requested = int(getattr(p, "exit_requested_ms", 0) or 0)
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    if requested <= 0 or now - requested < RESTING_EXIT_CANCEL_AFTER_MS:
        return False
    account_id = str(getattr(p, "account_id", "") or "")
    if mode is False and (not account_id
                          or account_id != str(getattr(client, "_account_id", "") or "")):
        return False
    key = (uid, p.symbol)
    # Same claim a fresh exit takes, so no second SELL can be placed in the window
    # between the cancel landing and the stop being re-armed.
    if not _exiting.claim(key, account_id=account_id):
        return False
    try:
        from app.services.snapback_family_mode import (
            INTENT_CANCEL,
            canonical_broker_capability,
        )
        try:
            with canonical_broker_capability(
                f"cancel-resting-exit:{account_id or 'paper'}:{p.symbol}:{oid}",
                intent=INTENT_CANCEL,
            ):
                await client.cancel_order(oid)
        except Exception as exc:  # noqa: BLE001
            log.debug("resting-exit cancel for %s/%s raised: %s", uid, p.symbol, exc)
            # It may have filled first; the order history below decides.
        try:
            history = await client.get_order_history(oid)
            final = (history or [])[-1] if history else None
        except Exception:  # noqa: BLE001
            final = None
        if (not isinstance(final, dict)
                or str(final.get("order_id") or oid) != oid
                or str(final.get("status") or "").strip().upper() not in _ORDER_TERMINAL):
            state.log(uid, "order_failed",
                      f"{p.symbol}: exit #{oid} was still {status.lower()} after "
                      f"{(now - requested) // 1000}s and its cancellation is not confirmed; "
                      f"will retry")
            return True
        final = {**final, "tradingsymbol": final.get("tradingsymbol") or p.symbol,
                 "order_id": oid}
        state.log(uid, "order_failed",
                  f"{p.symbol}: exit #{oid} rested unfilled for {(now - requested) // 1000}s "
                  f"and was cancelled ({str(final.get('status')).lower()}); re-protecting")
    finally:
        _exiting.discard(key)
    await on_order_update(uid, final, client=client)
    return True


async def _watch_resting_exit(client, uid: str, p: "pos.OpenPosition") -> None:
    """Tick-path hook: re-read a resting exit at most every few seconds."""
    requested = int(getattr(p, "exit_requested_ms", 0) or 0)
    now = int(time.time() * 1000)
    if requested <= 0 or now - requested < RESTING_EXIT_CANCEL_AFTER_MS:
        return
    key = (uid, p.symbol)
    if now - _resting_probe.get(key, 0) < _RESTING_EXIT_PROBE_MS:
        return
    _resting_probe[key] = now
    await resolve_resting_exit(client, uid, p, now_ms=now)


async def on_tick(uid: str, token: int, ltp: float, *, client) -> Optional[str]:
    """Handle one price tick (workstream C/D). Returns the symbol exited, or None.

    Finds the held position for ``token`` and exits at market if the premium has
    breached its trail. A position still pending its fill is not exited (we don't
    yet hold it).
    """
    try:
        for p in pos.open_positions(uid):
            if p.token != token:
                continue
            if p.status != pos.OPEN:
                return None  # not filled yet — nothing to protect
            if p.exit_order_id and p.exit_order_id not in {"submitting", "unknown"}:
                # An exit is already working at the broker; no second SELL. But a
                # market exit that rests unfilled must not hold the position naked.
                await _watch_resting_exit(client, uid, p)
                return None
            # Price trail breach (original)
            price_exit = pos.should_exit(p.stop_premium, ltp, p.direction)
            # Target, when the signal set one (Navigator only). Checked AFTER the stop
            # below so a bar that somehow satisfies both is treated as a loss, never a
            # win. Skipped entirely when a broker GTT holds the target, because that
            # OCO already books it and the exchange cancels the other leg — running
            # both would be two sell paths for one exit.
            target = float(getattr(p, "target_premium", 0.0) or 0.0)
            target_exit = bool(target) and not p.gtt_id and (
                ltp >= target if p.direction == "long" else ltp <= target)
            # Red-count awareness (shared logic): if last scan reported enough reds for this position's entry-time exit_mode, exit.
            # This makes the 1/2/3-red (or +signal) counter drive auto-exits dynamically on live positions.
            reds = getattr(p, 'current_red_count', 0)
            mode = getattr(p, 'exit_mode', 'one_red')
            thresh = get_exit_threshold(mode)
            red_exit = reds >= thresh
            has_counter = bool(getattr(p, 'counter_signal_fired', False))
            red_exit = should_exit_on_reds(reds, mode, has_counter_arrow=has_counter)
            if price_exit or red_exit or target_exit:
                if price_exit:
                    close_reason = (f"trail breach @ ₹{ltp:.2f} "
                                    f"{'≤' if p.direction == 'long' else '≥'} ₹{p.stop_premium:.2f}")
                elif red_exit:
                    close_reason = f"red count exit {reds}/{thresh} ({mode})"
                    extra = " + counter-signal" if mode == "three_red_signal" else ""
                    close_reason = f"red count exit {reds}/{thresh} ({mode}{extra})"
                else:
                    close_reason = (f"target reached @ ₹{ltp:.2f} "
                                    f"{'≥' if p.direction == 'long' else '≤'} ₹{target:.2f}")
                # Only the price branch may claim to be a price-stop exit: it is the one
                # exit the broker's own GTT would also perform.
                exited = await _exit_position(client, uid, p, ltp, reason=close_reason,
                                              price_stop_exit=bool(price_exit))
                return p.symbol if exited else None
            return None
    except Exception as exc:  # noqa: BLE001
        log.debug("kite monitor on_tick error for %s: %s", uid, exc)
    return None


async def on_ticks(uid: str, ticks: list, *, client) -> None:
    """Fan a batch of decoded ticks through ``on_tick``."""
    for t in ticks or []:
        try:
            token = int(t.get("instrument_token") or 0)
            ltp = float(t.get("last_price") or 0.0)
        except Exception:
            continue
        if token and ltp:
            await on_tick(uid, token, ltp, client=client)
