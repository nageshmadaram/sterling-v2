"""Recover durable live entries and protect only broker-confirmed holdings.

Broker observations are journaled before projection. Recovery repeats projection,
never submission. An uncertain initial GTT request requires reconciliation, not a
second trigger. A signed ledger settles fills and costs. Live scale-ins stay blocked:
that is a protection question, not an accounting one.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import fields
from datetime import datetime, timedelta, timezone

from app.core.logging import get_logger
from app.services.kite_engine import execution_lease, fill_ledger
from app.services.kite_engine import lifecycle_alerts as _alerts
from app.services.kite_engine import order_journal as journal, positions, state

log = get_logger(__name__)

_locks: dict[tuple[str, str], asyncio.Lock] = {}


def _ident(p) -> str:
    """What makes this holding distinct from an earlier one in the same contract."""
    return str(getattr(p, "order_id", "") or getattr(p, "opened_ms", "") or "")


def account_id(client) -> str:
    value = str(getattr(client, "_account_id", "") or "")
    if not value:
        raise ValueError("live_account_identity_missing")
    return value


def _intent_product(intent) -> str:
    # Older Sterling Kite intents omitted product because their contract was
    # NRML. Explicitly unknown values are not that historical default.
    product = positions.broker_product(intent.payload.get("product", "NRML"))
    if not product:
        raise ValueError("journal_product_identity_unknown")
    return product


def register_pending(intent, order_id: str) -> positions.OpenPosition:
    """Idempotent projection of an accepted entry; ACK carries zero filled qty."""
    product = _intent_product(intent)
    prior = positions.get(intent.uid, intent.symbol)
    if prior and prior.order_id == order_id and prior.account_id == intent.account_id:
        if prior.exchange != intent.exchange or positions.broker_product(prior.product) != product:
            raise ValueError("position_journal_identity_mismatch")
        return prior
    if prior and prior.status in (positions.OPEN, positions.PENDING):
        raise ValueError("live_scale_in_or_account_collision")
    allowed = {f.name for f in fields(positions.OpenPosition)}
    payload = {k: v for k, v in intent.payload.items() if k in allowed}
    payload.update(uid=intent.uid, account_id=intent.account_id, product=product,
                   symbol=intent.symbol, exchange=intent.exchange, qty=0,
                   order_id=order_id, entry_requested_qty=intent.quantity,
                   entry_pending=True, status=positions.PENDING, qty_by_order={})
    p = positions.register(positions.OpenPosition(**payload))
    positions.persist_strict(intent.uid)
    if p.guard_key:
        state.mark_auto_open(intent.uid, p.guard_key)
    return p


async def _protect(client, p) -> None:
    from app.services.exchanges.kite import constants as K, ticker_manager
    from app.services.kite_engine import protective_stop as stops

    if p.qty <= 0 or p.status != positions.OPEN:
        return
    if p.token:
        try:
            subscription = await ticker_manager.subscribe(
                p.uid, [p.token], mode=K.MODE_LTP,
                owner=ticker_manager.protection_owner(p.symbol),
            )
            if not isinstance(subscription, dict) or subscription.get("ok") is not True:
                raise RuntimeError(f"ticker_subscription_refused:{subscription!r}")
        except Exception:
            state.log(p.uid, "order_failed", f"{p.symbol}: live tick subscription unavailable")
    if p.stop_mode not in {"broker", "both"} or p.stop_premium <= 0:
        return
    if p.protection_pending:
        return  # prior placement could have succeeded; do not create a rival stop
    account_id = str(getattr(p, "account_id", "") or "")
    if not account_id:
        await _place_protection(client, p)   # paper: no broker to race for
        return
    # Two processes placing a stop for one position leaves TWO live triggers, and
    # the second one sells an option we no longer own the moment it fires.
    try:
        with execution_lease.guard(execution_lease.PROTECTION, account_id=account_id,
                                   uid=p.uid, symbol=p.symbol) as token:
            if token is None:
                state.log(p.uid, "info",
                          f"{p.symbol}: another engine process is arming protection; "
                          f"not placing a second trigger")
                return
            await _place_protection(client, p)
    except Exception as exc:  # noqa: BLE001
        state.log(p.uid, "order_failed",
                  f"{p.symbol}: protection ownership cannot be established ({exc}); "
                  f"no trigger sent")


class _PlaceGttRecorder:
    """Pass-through broker client that remembers why ``place_gtt`` raised.

    ``place_stop`` reports only "no id", which covers both a request the broker
    refused (nothing exists) and one whose answer was lost (something may exist).
    The two need opposite handling, so the cause is read from the exception itself.
    """

    def __init__(self, client) -> None:
        self._client = client
        self.error = None

    def __getattr__(self, name):
        return getattr(self._client, name)

    async def place_gtt(self, **kwargs):
        try:
            return await self._client.place_gtt(**kwargs)
        except Exception as exc:  # noqa: BLE001
            self.error = exc
            raise


def _is_definitive_refusal(exc: BaseException) -> bool:
    """True when ``exc`` proves a GTT placement created NOTHING at the broker.

    The broker-write guard refused before the call left this process, the request
    never reached the wire (connect / pool failures), or Kite answered with a 4xx.
    A read timeout, a 5xx or any other transport error proves nothing: the trigger
    may exist, so the outcome stays UNKNOWN and the position stays fenced.
    """
    if isinstance(exc, PermissionError):
        return True
    try:
        import httpx

        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
            return True
    except Exception:  # noqa: BLE001
        pass
    from app.services.exchanges.kite.errors import KiteError

    if isinstance(exc, KiteError):
        try:
            return 400 <= int(exc.status_code or 0) < 500
        except (TypeError, ValueError):
            return False
    return False


async def _place_protection(client, p) -> None:
    """Arm or resize the broker trigger. Caller owns the protection lease."""
    from app.services.kite_engine import protective_stop as stops

    product = positions.broker_product(p.product)
    if not product:
        state.log(p.uid, "order_failed", f"{p.symbol}: protection product identity unknown; no trigger sent")
        return

    ref_last_price = float(getattr(p, "current_price", None) or p.fill_price or p.entry_premium or 0.0)

    kwargs = dict(
        tradingsymbol=p.symbol,
        exchange=p.exchange,
        qty=p.qty,
        trigger_premium=p.stop_premium,
        last_price=ref_last_price,
        direction=p.direction,
        target_premium=p.target_premium,
        product=product,
    )
    from app.services.snapback_family_mode import (
        INTENT_PROTECT,
        canonical_broker_capability,
    )

    with canonical_broker_capability(
        f"protect:{p.account_id or 'paper'}:{p.symbol}",
        intent=INTENT_PROTECT,
    ):
        if p.gtt_id:
            if not await stops.move_stop(client, trigger_id=p.gtt_id, **kwargs):
                p.protection_pending = True
                positions.persist_strict(p.uid)
                _alerts.emit(
                    p.uid,
                    dedupe_key=(
                        f"supertrend:protection-failed:{p.symbol}:{_ident(p)}:{int(p.gtt_id)}:"
                        f"{float(p.stop_premium):.2f}:{int(p.qty)}"
                    ),
                    event_type="PROTECTION FAILED",
                    title=p.symbol,
                    detail=_alerts.build_detail(
                        **_alerts.stop_fields(p), qty=p.qty,
                        reason=(f"Broker GTT #{int(p.gtt_id)} update not confirmed; "
                                "new entries must remain halted")),
                    severity="CRITICAL",
                )
            else:
                p.protection_pending = False
                positions.persist_strict(p.uid)
                _alerts.emit(
                    p.uid,
                    dedupe_key=(
                        f"supertrend:protection:{p.symbol}:{_ident(p)}:{int(p.gtt_id)}:"
                        f"{float(p.stop_premium):.2f}:{int(p.qty)}"
                    ),
                    # A modify of a resting trigger is a re-sync (quantity resize or
                    # recovery), not a stop move: the stop alert is "TSL UPDATED".
                    event_type="PROTECTION SYNCED",
                    title=p.symbol,
                    detail=_alerts.build_detail(
                        **_alerts.stop_fields(p), qty=p.qty,
                        notes=[f"GTT #{int(p.gtt_id)}"]),
                )
            return
        def record_submission():
            p.protection_pending = True
            positions.persist_strict(p.uid)  # durable before the broker write

        recorder = _PlaceGttRecorder(client)
        gid = await stops.place_stop(recorder, **kwargs, before_submit=record_submission)
        if not gid and recorder.error is not None and _is_definitive_refusal(recorder.error):
            # The broker provably created nothing (a 4xx, or the request never left
            # this process): there is no unknown outcome to protect against, and a
            # latched flag would block every exit and every retry for good.
            p.protection_pending = False
            positions.persist_strict(p.uid)
        if gid:
            p.gtt_id = int(gid)
            p.protection_pending = False
            positions.persist_strict(p.uid)
            _alerts.emit(
                p.uid,
                dedupe_key=(
                    f"supertrend:protection:{p.symbol}:{_ident(p)}:{int(p.gtt_id)}:"
                    f"{float(p.stop_premium):.2f}:{int(p.qty)}"
                ),
                event_type="SL ARMED",
                title=p.symbol,
                detail=_alerts.build_detail(
                    **_alerts.stop_fields(p), qty=p.qty, notes=[f"GTT #{int(p.gtt_id)}"]),
            )
        else:
            state.log(p.uid, "order_failed", f"{p.symbol}: broker protection unavailable; reconcile before retry")
            _alerts.emit(
                p.uid,
                dedupe_key=(
                    f"supertrend:protection-uncertain:{p.symbol}:{_ident(p)}:"
                    f"{float(p.stop_premium):.2f}:{int(p.qty)}"
                ),
                event_type="PROTECTION UNCERTAIN" if p.protection_pending else "PROTECTION FAILED",
                title=p.symbol,
                detail=_alerts.build_detail(
                    **_alerts.stop_fields(p), qty=p.qty,
                    reason="Broker protection unavailable; reconciliation required"),
                severity="CRITICAL",
            )


def exchange_ms(order: dict) -> int:
    """Epoch ms the EXCHANGE stamped on this event, or 0 when it did not.

    The ledger replays increments in exchange order, so a real timestamp is what
    lets a late event land in its true place. 0 means "unknown" and must stay 0 —
    substituting arrival time here would look like ordering information and
    silently defeat the replay.
    """
    from app.services.exchanges.kite.client import _parse_kite_ts
    raw = order.get("exchange_timestamp") or order.get("exchange_update_timestamp")
    if not raw:
        return 0
    try:
        return int(_parse_kite_ts(raw))
    except Exception as exc:  # noqa: BLE001
        log.debug("suppressed exchange timestamp parse: %s", exc)
        return 0


async def apply_broker_charges(client, uid: str, p,
                                order_id: str, order: dict) -> None:
    """Ask the broker what this order actually cost and record it against the fills.

    Realized PnL is gross of costs until this lands, so it reads better than the
    truth — the direction that delays the daily-loss breaker rather than tripping
    it early. Best effort: charges are computed after the fact and a failure here
    must not disturb an exit that has already happened. ``Inventory.fees_complete``
    is what says whether the figure is net.
    """
    if client is None or getattr(client, "_is_paper", True) is not False:
        return
    charges = getattr(client, "order_charges", None)
    if charges is None:
        return
    try:
        rows = await charges([{
            "order_id": order_id, "exchange": p.exchange, "tradingsymbol": p.symbol,
            "transaction_type": str(order.get("transaction_type") or "").upper(),
            "variety": str(order.get("variety") or "regular"),
            "product": str(order.get("product") or "NRML"),
            "order_type": str(order.get("order_type") or "MARKET"),
            "quantity": int(order.get("filled_quantity") or 0),
            "average_price": float(order.get("average_price") or 0.0)}])
        total = float((rows or [{}])[0].get("charges", {}).get("total") or 0.0)
        if total <= 0:
            return
        fill_ledger.apply_fees(account_id=p.account_id, uid=uid, symbol=p.symbol,
                               order_id=order_id, fees=total, source="broker")
    except Exception as exc:  # noqa: BLE001
        state.log(uid, "info",
                  f"{p.symbol}: broker charges for #{order_id} unavailable ({exc}); "
                  f"realized PnL stays gross of costs")


def _book_entry(uid: str, account_id: str, intent, order: dict) -> bool:
    """Record the journal's confirmed entry quantity/value in the signed ledger.

    The journal already holds the broker's cumulative figures and rejects older
    or contradictory evidence, so it is the right thing to forward: the ledger
    derives the increment itself and is safe to call again on every replay.

    False means the cost basis did NOT reach the ledger. That is not cosmetic:
    every later exit for this holding then settles on the legacy accumulator, so
    the caller flags the position rather than letting it look accounted for.
    """
    if intent.filled_quantity <= 0 or intent.filled_value <= 0 or not intent.order_id:
        return True
    try:
        applied = fill_ledger.record(
            account_id=account_id, uid=uid, symbol=intent.symbol,
            exchange=intent.exchange, side=intent.side.upper(),
            order_id=intent.order_id, cumulative_quantity=intent.filled_quantity,
            cumulative_value=intent.filled_value, source="entry",
            lot_size=int(intent.payload.get("lot_size") or 0),
            exchange_ts_ms=exchange_ms(order))
    except Exception as exc:  # noqa: BLE001
        state.log(uid, "order_failed",
                  f"{intent.symbol}: entry fill could not be booked to the ledger "
                  f"({exc}); cost basis needs reconciliation")
        return False
    if applied.reconciliation_required:
        state.log(uid, "order_failed",
                  f"{intent.symbol}: contradictory entry fill evidence quarantined "
                  f"({applied.inventory.reconciliation_reason})")
        return False
    return True


def _book_exit(uid: str, account_id: str, intent, order: dict) -> bool:
    """Record the journal's confirmed exit quantity/value in the signed ledger."""
    if intent.filled_quantity <= 0 or intent.filled_value <= 0 or not intent.order_id:
        return True
    try:
        applied = fill_ledger.record(
            account_id=account_id, uid=uid, symbol=intent.symbol,
            exchange=intent.exchange, side=intent.side.upper(),
            order_id=intent.order_id, cumulative_quantity=intent.filled_quantity,
            cumulative_value=intent.filled_value, source="exit",
            lot_size=int(intent.payload.get("lot_size") or 0),
            exchange_ts_ms=exchange_ms(order))
    except Exception as exc:  # noqa: BLE001
        state.log(uid, "order_failed",
                  f"{intent.symbol}: exit fill could not be booked to the ledger "
                  f"({exc}); cost basis needs reconciliation")
        return False
    if applied.reconciliation_required:
        state.log(uid, "order_failed",
                  f"{intent.symbol}: contradictory exit fill evidence quarantined "
                  f"({applied.inventory.reconciliation_reason})")
        return False
    return True


# A Gamma entry younger than this may still be inside arm(): the order is at the
# broker and the registry row is about to be written. Recovery must not call it
# an orphan until it has had time to finish.
ORPHAN_ENTRY_GRACE_MS = 120_000


async def _observe_foreign_intent(uid: str, aid: str, intent, order: dict,
                                  *, recovering: bool = False) -> bool:
    """Journal a non-SuperTrend order's broker evidence; project nothing into the
    SuperTrend registry.

    Conflicting evidence stays quarantined (the intent remains unresolved, so
    entries stay blocked). A Gamma entry that FILLED but has no Gamma row, as when
    the submit timed out and the process then lost the row, would otherwise be a
    live position that is unprotected, unmanaged and invisible: recovery pages it
    loudly and halts new entries until the operator adopts or closes it.
    """
    oid = str(order.get("order_id") or "")
    symbol = str(order.get("tradingsymbol") or "")
    async with _locks.setdefault((uid, aid), asyncio.Lock()):
        observed = journal.observe_order(
            intent.intent_key,
            status=str(order.get("status") or ""),
            order_id=oid,
            filled_quantity=order.get("filled_quantity", 0),
            average_price=order.get("average_price", 0),
            raw=order,
        )
        if observed.reconciliation_required:
            state.log(
                uid,
                "order_failed",
                f"{symbol}: conflicting {intent.strategy_id} broker fill evidence",
            )
            return True
        verdict = _gamma_projection_verdict(uid, observed.intent, recovering=recovering)
        if verdict == _DEFER:
            # Gamma's own arm/reconcile projects this fill into its registry and
            # then acknowledges the journal version; acknowledging it here would
            # hide an entry nobody has recorded yet.
            return True
        journal.mark_projected(intent.intent_key, observed.intent.projection_version)
    if verdict == _ORPHAN:
        _flag_orphan_entry(uid, observed.intent)
    return True


_DEFER = "defer"
_ORPHAN = "orphan"
_SETTLED = "settled"


def _gamma_projection_verdict(uid: str, intent, *, recovering: bool) -> str:
    """Who projects this observed order: Gamma (defer), nobody (orphan), or no one needed."""
    if str(intent.strategy_id or "") != "gamma_move":
        return _SETTLED
    if str(intent.side or "").upper() != "BUY" or int(intent.filled_quantity or 0) <= 0:
        return _SETTLED
    try:
        from app.services import gamma_move_positions as gamma_store
        row = gamma_store.get(uid, intent.symbol)
    except Exception:  # noqa: BLE001
        return _DEFER  # cannot tell: fail closed, leave the projection outstanding
    if row is not None and intent.order_id and str(row.order_id or "") == str(intent.order_id):
        # The row exists. While it is live Gamma's reconcile projects the fill;
        # a closed or rejected row has nothing left to project.
        return _DEFER if row.status in ("pending", "open") else _SETTLED
    if not recovering:
        return _DEFER  # the order stream can beat arm()'s row write by milliseconds
    age_ms = int(time.time() * 1000) - int(intent.created_ms or 0)
    if age_ms < ORPHAN_ENTRY_GRACE_MS:
        return _DEFER
    return _ORPHAN


def _flag_orphan_entry(uid: str, intent) -> None:
    """A filled Gamma entry with no registry row: alert and halt new entries."""
    state.log(uid, "order_failed",
              f"{intent.symbol}: Gamma entry filled ({intent.filled_quantity}) with no "
              "position row; it is unprotected until adopted or closed")
    try:
        from app.services import live_safety
        live_safety.set_kill_switch(
            True,
            f"Gamma-Unwind entry filled with no position row for {intent.symbol}",
        )
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.services.notifications.telegram_lifecycle import enqueue_best_effort
        enqueue_best_effort(
            uid,
            dedupe_key=f"gamma:orphan-entry:{intent.intent_key}",
            strategy="Gamma-Unwind",
            event_type="ORPHAN POSITION",
            title=intent.symbol,
            detail=(
                f"Entry order {intent.order_id} filled {int(intent.filled_quantity)} but "
                "Gamma has no position row · no stop, no management · adopt or close it "
                "in Kite · new entries halted"
            ),
            severity="CRITICAL",
        )
    except Exception:  # noqa: BLE001
        pass


async def consume_order(client, uid: str, order: dict, *, recovering: bool = False) -> bool:
    """True means a journal-owned entry or exit was handled (including blocked evidence).

    ``recovering`` is set only by :func:`recover`; it lets an observed fill that no
    strategy will ever project be declared an orphan instead of waiting for a row.
    """
    aid = account_id(client)
    oid = str(order.get("order_id") or "")
    symbol = str(order.get("tradingsymbol") or "")
    intent = journal.find(uid=uid, account_id=aid, order_id=oid,
                          tag=str(order.get("tag") or ""))
    if intent is None:
        return False

    # Validate immutable broker identity before any strategy-specific
    # projection. Snapback shares this canonical journal even though it owns a
    # different position schema.
    try:
        product = _intent_product(intent)
    except ValueError:
        state.log(uid, "order_failed", f"{intent.symbol}: journal product identity unknown")
        return True
    if (symbol != intent.symbol or order.get("exchange") != intent.exchange
            or str(order.get("transaction_type") or "").upper() != intent.side.upper()
            or str(order.get("product") or "").strip().upper() != product
            or int(order.get("quantity") or 0) != intent.quantity):
        state.log(uid, "order_failed", f"{intent.symbol}: journal/broker identity mismatch")
        return True

    # Every other strategy (Snapback, Gamma Unwind, ...) owns its own position
    # schema. Their broker evidence is observed in the canonical journal so that
    # recovery can settle ANY strategy's intent, but a SuperTrend position is
    # never registered for it: that would create a phantom position and leave the
    # real strategy out of sync with its own broker order.
    from app.services.kite_engine.canonical_entry import STRATEGY_ID as KITE_STRATEGY_ID
    if intent.strategy_id != KITE_STRATEGY_ID:
        return await _observe_foreign_intent(uid, aid, intent, order, recovering=recovering)
    prior = positions.get(uid, symbol)
    if prior and prior.status in (positions.OPEN, positions.PENDING) and (
        prior.account_id != aid or prior.exchange != intent.exchange
        or positions.broker_product(prior.product) != product
    ):
        state.log(uid, "order_failed", f"{intent.symbol}: registry/journal identity mismatch")
        return True
    async with _locks.setdefault((uid, aid), asyncio.Lock()):
        observed = journal.observe_order(
            intent.intent_key, status=str(order.get("status") or ""), order_id=oid,
            filled_quantity=order.get("filled_quantity", 0),
            average_price=order.get("average_price", 0), raw=order)
        if observed.reconciliation_required:
            state.log(uid, "order_failed", f"{symbol}: conflicting broker fill evidence")
            return True

        is_exit = (
            intent.payload.get("intent_type") == "EXIT"
            or intent.payload.get("exposure_effect") in ("CLOSE_POSITION", "REDUCE_EXPOSURE")
        )
        if is_exit:
            p = positions.get(uid, symbol)
            latest = observed.intent
            filled = latest.filled_quantity
            booked_ok = _book_exit(uid, aid, latest, order)
            if p:
                from app.services import db
                inv = db.get_inventory_strict(aid, uid, symbol) if hasattr(db, "get_inventory_strict") else None
                if inv and "net_quantity" in inv:
                    p.qty = abs(inv["net_quantity"])
                elif filled > 0:
                    p.qty = max(0, p.qty - filled) if p.qty > 0 else 0

                if p.qty == 0 and latest.state in journal.TERMINAL:
                    p.status = positions.CLOSED
                    p.entry_pending = False
                    p.protection_pending = False
                    if not p.closed_ms:
                        p.closed_ms = int(time.time() * 1000)
                positions.persist_strict(uid)

                if filled > 0:
                    avg_exit = (
                        float(latest.filled_value) / int(filled)
                        if int(filled) > 0 else 0.0
                    )
                    closed = p.qty == 0 and latest.state in journal.TERMINAL
                    entry_px = float(p.fill_price or p.entry_premium or 0.0)
                    pnl = _alerts.realized_pnl(entry_px, avg_exit, filled, p.direction)
                    reason = str(p.exit_reason or "")
                    stop_out = any(w in reason.lower() for w in ("stop", "breach", "trail", "gtt"))
                    label = f"{_alerts.stop_kind(p)} hit" if stop_out else ""
                    _alerts.emit(
                        uid,
                        dedupe_key=(
                            f"supertrend:exit:{symbol}:{oid}:{int(filled)}:"
                            f"{avg_exit:.2f}"
                        ),
                        event_type="POSITION CLOSED" if closed else "PARTIAL EXIT",
                        title=symbol,
                        detail=_alerts.build_detail(
                            entry=entry_px, exit=avg_exit, pnl=pnl, qty=filled,
                            qty_total=intent.quantity,
                            reason=(f"{label} · {reason}" if label and reason else (label or reason))),
                        severity=(
                            "WARNING"
                            if (stop_out or (pnl is not None and pnl < 0))
                            and not (pnl is not None and pnl > 0)
                            else "INFO"
                        ),
                    )

                if p.qty == 0 and latest.state in journal.TERMINAL:
                    if p.guard_key:
                        state.clear_auto_open(uid, p.guard_key)
                    if p.token:
                        try:
                            from app.services.exchanges.kite import ticker_manager
                            await ticker_manager.release(
                                uid,
                                [p.token],
                                owner=ticker_manager.protection_owner(p.symbol),
                            )
                        except Exception as exc:  # noqa: BLE001
                            log.debug(
                                "SuperTrend exit ticker release failed for %s: %s",
                                p.symbol, exc,
                            )
            if not booked_ok:
                if p:
                    p.pnl_reconciliation_required = True
                    positions.persist_strict(uid)
            elif latest.state in journal.TERMINAL and p:
                await apply_broker_charges(client, uid, p, oid, order)
            journal.mark_projected(intent.intent_key, latest.projection_version)
            return True

        if not observed.accepted and not observed.intent.projection_pending:
            p = positions.get(uid, symbol)
            if p and p.order_id == oid and p.account_id == aid and not p.gtt_id:
                await _protect(client, p)
            return True
        # Replaying a terminal event for an already closed position must not reopen it.
        p = register_pending(observed.intent, oid)
        if p.status in (positions.CLOSED, positions.REJECTED):
            journal.mark_projected(intent.intent_key, observed.intent.projection_version)
            return True
        latest = observed.intent
        filled = latest.filled_quantity
        p.qty = filled
        p.qty_by_order = {oid: filled} if filled else {}
        p.fill_price = latest.filled_value / filled if filled else 0.0
        p.entry_pending = latest.state not in journal.TERMINAL
        p.status = (positions.OPEN if filled else
                    positions.PENDING if p.entry_pending else positions.REJECTED)
        positions.persist_strict(uid)
        if filled > 0:
            event_type = (
                "ENTRY FILLED"
                if latest.state in journal.TERMINAL and filled >= int(intent.quantity)
                else "PARTIAL ENTRY FILL"
            )
            _alerts.emit(
                uid,
                dedupe_key=(
                    f"supertrend:entry:{symbol}:{oid}:{int(filled)}:"
                    f"{float(p.fill_price or 0.0):.2f}"
                ),
                event_type=event_type,
                title=symbol,
                detail=_alerts.build_detail(
                    entry=p.fill_price, sl=p.stop_premium, qty=filled,
                    qty_total=intent.quantity),
            )
        # Settle the money before the projection is acknowledged. A crash in
        # between leaves the intent projection_pending, so recovery replays this
        # same cumulative evidence and the ledger books it exactly once.
        if not _book_entry(uid, aid, latest, order):
            p.pnl_reconciliation_required = True
            positions.persist_strict(uid)
        elif latest.state in journal.TERMINAL:
            await apply_broker_charges(client, uid, p, oid, order)
        journal.mark_projected(intent.intent_key, latest.projection_version)
        if p.status == positions.REJECTED and p.guard_key:
            state.clear_auto_open(uid, p.guard_key)
        await _protect(client, p)
    return True


async def _restore_monitor_subscription(p) -> None:
    """Restore tick monitoring for an owned open position after restart."""
    if not p.token or p.qty <= 0 or p.status != positions.OPEN:
        return
    try:
        from app.services.exchanges.kite import constants as K, ticker_manager
        subscription = await ticker_manager.subscribe(
            p.uid,
            [p.token],
            mode=K.MODE_LTP,
            owner=ticker_manager.protection_owner(p.symbol),
        )
        if not isinstance(subscription, dict) or subscription.get("ok") is not True:
            raise RuntimeError(f"ticker_subscription_refused:{subscription!r}")
    except Exception:
        state.log(
            p.uid,
            "order_failed",
            f"{p.symbol}: live tick subscription could not be restored",
        )


async def _reconcile_pending_protection(client, p) -> None:
    """Resolve protection uncertainty from positive broker evidence only."""
    from app.services.kite_engine import protective_stop as stops

    if not p.protection_pending or p.qty <= 0 or p.status != positions.OPEN:
        return
    product = positions.broker_product(p.product)
    if not product:
        state.log(
            p.uid,
            "order_failed",
            f"{p.symbol}: protection reconciliation blocked; product identity is unknown",
        )
        return

    if p.gtt_id:
        verdict = await stops.stop_status(
            client,
            int(p.gtt_id),
            tradingsymbol=p.symbol,
            direction=p.direction,
            exchange=p.exchange,
            product=product,
            qty=int(p.qty),
        )
        if verdict == stops.STOP_ACTIVE:
            # The exact trigger still exists. Retrying a MODIFY on this known id
            # cannot create a second trigger, so synchronize it to current state.
            # Keep protection_pending=True until _place_protection confirms the
            # modify. A crash before that confirmation must remain recoverable.
            await _place_protection(client, p)
            return
        if verdict == stops.STOP_ABSENT:
            # Positive broker evidence proves the old id cannot act and no exact
            # exit order is working. It is safe to place replacement protection.
            p.gtt_id = 0
            p.protection_pending = False
            positions.persist_strict(p.uid)
            await _protect(client, p)
            return
        return

    # No persisted GTT id means Sterling cannot prove ownership of any broker
    # trigger. A field-identical GTT may belong to a manual order or another
    # strategy that shares the same contract. Do not adopt, modify, or delete it.
    # Keep the durable uncertainty fence set and halt new exposure until an
    # operator reconciles ownership.
    state.log(
        p.uid,
        "order_failed",
        f"{p.symbol}: broker protection ownership is unknown; "
        "operator reconciliation is required before new entries",
    )
    try:
        from app.services import live_safety
        live_safety.set_kill_switch(
            True,
            f"SuperTrend protection ownership is unknown for {p.symbol}",
        )
    except Exception:
        pass
    _alerts.emit(
        p.uid,
        dedupe_key=f"supertrend:protection-ownership:{p.symbol}:{_ident(p)}",
        event_type="PROTECTION OWNERSHIP UNKNOWN",
        title=p.symbol,
        detail=_alerts.build_detail(
            **_alerts.stop_fields(p), qty=p.qty,
            reason="Cannot prove which broker trigger protects this position; "
                   "new entries are halted until it is reconciled"),
        severity="CRITICAL",
    )


#: How old an unanswered submit must be before an empty day order book settles it.
_NO_EVIDENCE_SETTLE_AFTER_S = 120
_IST_OFFSET = timezone(timedelta(hours=5, minutes=30))


def _settles_without_broker_evidence(intent) -> bool:
    """May an intent with no order in the broker's day book be closed as not sent?

    The order book lists every order of the current trading day, so a request that
    was sent and accepted today and is older than a couple of minutes must appear
    in it. Only a send claimed or left UNKNOWN today with no order id qualifies; an
    older or flagged intent is ambiguous and stays with the operator.
    """
    if intent.state not in ("SUBMITTING", "UNKNOWN") or intent.order_id:
        return False
    if intent.reconciliation_required:
        return False
    now = time.time()
    created = float(intent.created_ms or 0) / 1000.0
    if created <= 0 or now - created < _NO_EVIDENCE_SETTLE_AFTER_S:
        return False
    return (datetime.fromtimestamp(created, _IST_OFFSET).date()
            == datetime.fromtimestamp(now, _IST_OFFSET).date())


async def recover(client, uid: str) -> None:
    """Find accepted/unknown entries even when the registry was never written."""
    aid = account_id(client)
    candidates = {i.intent_key: i for i in
                  journal.unresolved(uid, account_id=aid) + journal.pending_projection(uid, aid)}
    # Restore monitoring for every owned open position first. A broker GTT can
    # survive a restart while the process-owned ticker subscription cannot.
    for p in positions.open_positions(uid):
        if p.account_id == aid:
            await _restore_monitor_subscription(p)

    # Then reconcile an unknown protection outcome from broker evidence.
    # A lost acknowledgement must never be repaired by blind placement.
    for p in positions.open_positions(uid):
        if p.account_id == aid and p.protection_pending:
            await _reconcile_pending_protection(client, p)

    # Repair a known missing trigger after restart/cancel, never an unknown submit.
    for p in positions.open_positions(uid):
        if p.account_id == aid and not p.gtt_id and not p.protection_pending:
            await _protect(client, p)
    if not candidates:
        return
    book = await client.get_orders()
    if not isinstance(book, list):
        raise ValueError("malformed_broker_order_book")
    for intent in candidates.values():
        if intent.state == "RESERVED":
            continue  # no send claim; only the originating validated request may claim
        matches = [o for o in book if isinstance(o, dict) and
                   ((intent.order_id and o.get("order_id") == intent.order_id)
                    or o.get("tag") == intent.tag
                    or intent.tag in (o.get("tags") or ()))]
        if not matches and intent.order_id:
            history = await client.get_order_history(intent.order_id)
            matches = [history[-1]] if history and isinstance(history[-1], dict) else []
        if len(matches) == 1:
            await consume_order(client, uid, matches[0], recovering=True)
        elif not matches and _settles_without_broker_evidence(intent):
            try:
                journal.transition(
                    intent.intent_key, "REJECTED",
                    error="not found in the broker day order book; settled as not sent")
            except ValueError:
                continue  # concurrent evidence moved it; the next pass reads that
            state.log(uid, "info",
                      f"{intent.symbol}: no broker order for an unanswered submit; "
                      "settled as not sent")
        else:
            state.log(uid, "order_failed", f"{intent.symbol}: broker entry unresolved; no resubmission")
