"""Gamma-Unwind V1 runtime and Sterling execution adapter.

The user-supplied GAMMA-UNWIND client is signal/alert only.  Its signal
classification and follow-up rules are preserved in app.engines.gamma_move.
This module adds Sterling's existing execution authority, broker reconciliation
and persistence without changing the reference signal tier.

Only ENTRY tier can increase exposure. STRONG_WATCHLIST is alert-only and
WATCHLIST is dashboard-only.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timezone, timedelta
from typing import Any, Optional

from app.core.logging import get_logger
from app.engines.gamma_move import (
    CONTRACT_VERSION, GammaMoveConfig, GammaSignal, PositionState, STRATEGY_ID,
    TIER_ENTRY, align_to_tick, d68_action, realised_inr, trading_days_between,
)
from app.services import gamma_move_positions as positions_store
from app.services.gamma_move import get_config

log = get_logger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))
_OWNER = STRATEGY_ID
_sessions: dict[str, "Session"] = {}
_locks: dict[str, asyncio.Lock] = {}
_schedule_done: set[tuple[str, str, str]] = set()


def _now() -> datetime:
    return datetime.now(IST)


def _now_ms() -> int:
    return int(_now().timestamp() * 1000)


def _today_str() -> str:
    return _now().date().isoformat()


def _lock_for(uid: str) -> asyncio.Lock:
    return _locks.setdefault(uid, asyncio.Lock())


@dataclass
class Session:
    uid: str
    cfg: GammaMoveConfig
    day: str = field(default_factory=_today_str)
    signals: dict[str, GammaSignal] = field(default_factory=dict)
    subscribed: set[int] = field(default_factory=set)
    notes: list[dict] = field(default_factory=list)
    record: Any = None

    def note(self, kind: str, message: str) -> None:
        self.notes.append({"kind": kind, "message": message, "at_ms": _now_ms()})
        del self.notes[:-60]


def _signal_key(uid: str) -> str:
    return f"gamma_move_signals:{uid}"


def _persist_signals(session: Session) -> None:
    try:
        from app.services import db
        rows = [s.as_dict() for s in session.signals.values()]
        db.set_config(
            _signal_key(session.uid),
            json.dumps(rows, separators=(",", ":"), allow_nan=False),
        )
        from app.services import gamma_move_history
        gamma_move_history.append_signals(session.uid, session.signals.values())
    except Exception as exc:
        log.warning("gamma_unwind: signal persistence failed for %s: %s",
                    session.uid, exc)


def session_for(uid: str, cfg: Optional[GammaMoveConfig] = None) -> Session:
    cfg = cfg or get_config(uid)
    today = _today_str()
    session = _sessions.get(uid)
    if session is None:
        session = Session(
            uid=uid,
            cfg=cfg,
            day=today,
            record=positions_store.load_record(uid),
        )
        try:
            from app.services import db
            raw = db.get_config(_signal_key(uid))
            rows = json.loads(raw) if isinstance(raw, str) and raw else (raw or [])
            all_signals: list[GammaSignal] = []
            for row in rows if isinstance(rows, list) else []:
                sig = GammaSignal.from_dict(row)
                if sig.at_ms:
                    all_signals.append(sig)
                    if (
                        datetime.fromtimestamp(sig.at_ms / 1000, IST)
                        .date().isoformat() == today
                    ):
                        session.signals[sig.id] = sig
            if all_signals:
                # Migrate every legacy row by its event timestamp. This can
                # recover prior days from the pre-ledger single-key store.
                from app.services import gamma_move_history
                gamma_move_history.append_signals(uid, all_signals)
        except Exception as exc:
            log.debug("gamma_unwind: signal hydration failed for %s: %s",
                      uid, exc)
        _sessions[uid] = session
    elif session.day != today:
        # E1 identity is session/day scoped for the live board. Historical rows
        # are already durable in gamma_move_history, so do not mix yesterday
        # into today's operator view.
        session.day = today
        session.signals.clear()
        session.note("session", f"Gamma-Unwind day rolled to {today}")
    session.cfg = cfg
    return session

def invalidate_signal_state(uid: str, reason: str = "settings changed") -> None:
    """Clear today's live armable E1 cache without deleting history."""
    session = _sessions.get(uid)
    if session is None:
        return
    if session.signals:
        session.signals.clear()
        _persist_signals(session)
    session.note("config", f"Gamma-Unwind live signal state reset: {reason}")


def clear(uid: str | None = None) -> None:
    if uid:
        _sessions.pop(uid, None)
    else:
        _sessions.clear()


def is_paper(uid: str) -> Optional[bool]:
    try:
        from app.services.exchanges.kite import accounts
        acct = accounts.get_active(uid)
        mode = getattr(acct, "is_paper", None) if acct else None
        return mode if mode is True or mode is False else None
    except Exception:
        return None


def auto_execute(uid: str) -> bool:
    try:
        from app.services.kite_engine import state as engine_state
        shared = bool(getattr(engine_state.get_config(uid), "auto_execute", False))
        return shared and bool(get_config(uid).auto_execute)
    except Exception as exc:
        log.error("gamma_unwind: auto state unavailable for %s: %s", uid, exc)
        return False


async def _client(uid: str):
    from app.services.exchanges.kite import accounts
    acct = accounts.get_active(uid)
    if not acct:
        raise RuntimeError("No active Kite account")
    return await accounts.acquire_client(acct)


def _position_account_matches(client, account_id: str) -> bool:
    mode = getattr(client, "_is_paper", None)
    if mode is True:
        return True
    if mode is not False:
        return False
    owner = str(account_id or "").strip()
    current = str(getattr(client, "_account_id", "") or "").strip()
    return bool(owner and current and owner == current)


def _safety(uid: str, idem: str) -> tuple[bool, str]:
    try:
        from app.services.live_safety import assert_safe_to_trade
        decision = assert_safe_to_trade(
            [], idem, check_daily_loss=True, uid=uid
        )
        return bool(decision.allowed), str(decision.reason or "")
    except Exception as exc:
        log.error("gamma_unwind: safety check failed closed: %s", exc)
        return False, f"safety check unavailable: {exc}"


def has_tick_work(uid: str) -> bool:
    try:
        from app.services import gamma_move_scanner as scanner
        return scanner.has_watchers(uid) or bool(positions_store.open_positions(uid))
    except Exception:
        return bool(positions_store.open_positions(uid))


async def _subscribe_watched(uid: str, session: Session) -> None:
    from app.services import gamma_move_scanner as scanner
    tokens: set[int] = set()
    for rt in scanner.runtimes(uid).values():
        try:
            tokens.add(int(rt.contract.instrument.instrument_id))
        except (TypeError, ValueError):
            pass
        if rt.contract.underlying_token:
            tokens.add(int(rt.contract.underlying_token))
    for pos in positions_store.open_positions(uid):
        try:
            tokens.add(int(pos.instrument.instrument_id))
        except (TypeError, ValueError):
            pass
        if pos.underlying_token:
            tokens.add(int(pos.underlying_token))

    new = tokens - session.subscribed
    stale = session.subscribed - tokens
    try:
        from app.services.exchanges.kite import ticker_manager
        from app.services.exchanges.kite import constants as K
        if new:
            await ticker_manager.subscribe(
                uid, sorted(new), K.MODE_FULL, owner=_OWNER
            )
            session.subscribed |= new
        if stale:
            await ticker_manager.release(uid, sorted(stale), owner=_OWNER)
            session.subscribed -= stale
    except Exception as exc:
        # Paper mode has no live broker websocket; REST polling is used below.
        if is_paper(uid) is not True:
            log.warning("gamma_unwind: subscription update failed for %s: %s",
                        uid, exc)


async def release_subscriptions(uid: str) -> None:
    session = _sessions.get(uid)
    if not session or not session.subscribed:
        return
    try:
        from app.services.exchanges.kite import ticker_manager
        await ticker_manager.release(
            uid, sorted(session.subscribed), owner=_OWNER
        )
    except Exception:
        pass
    session.subscribed.clear()


async def scan_once(
    uid: str,
    *,
    scheduled_slot: str | None = None,
) -> dict:
    """Build the reference watchlist without time-shifting its inputs.

    At a scheduled slot (within 90 seconds), use live quotes exactly like the
    supplied client. Later manual/restart recovery reconstructs the latest due
    slot from historical data instead of selecting contracts from the current
    spot/OI state.
    """
    cfg = get_config(uid)
    if not cfg.enabled:
        return {"watching": 0, "signals": [], "message": "engine disabled"}
    async with _lock_for(uid):
        session = session_for(uid, cfg)
        from app.services import gamma_move_scanner as scanner
        now = _now()
        slot_name = scheduled_slot
        if slot_name is None:
            due = [
                (target, raw)
                for raw in cfg.build_watchlists_times
                if (target := _slot_time(now, raw)) is not None
                and target <= now
                and target.time() <= dtime(15, 20)
            ]
            if due:
                due.sort()
                slot_name = due[-1][1]

        target = _slot_time(now, slot_name) if slot_name else None
        if target is not None and (now - target).total_seconds() > 90:
            result = await scanner.recover_scheduled_watchlist(
                uid,
                cfg,
                build_time=target,
                recorded_signals=list(session.signals.values()),
            )
        else:
            result = await scanner.scan_once(
                uid,
                cfg,
                recorded_signals=list(session.signals.values()),
            )
        await _subscribe_watched(uid, session)
        return {
            **result,
            "signals": [s.as_dict() for s in session.signals.values()],
        }


async def _paper_poll_ticks(uid: str) -> list[dict]:
    if is_paper(uid) is not True:
        return []
    from app.services import gamma_move_scanner as scanner
    rts = scanner.runtimes(uid)
    if not rts:
        return []
    client = await _client(uid)
    token_to_key: dict[int, str] = {}
    for rt in rts.values():
        token_to_key[int(rt.contract.instrument.instrument_id)] = (
            f"{rt.contract.instrument.exchange}:"
            f"{rt.contract.instrument.tradingsymbol}"
        )
        token_to_key[int(rt.contract.underlying_token)] = (
            f"NSE:{rt.contract.underlying}"
        )
    quotes = await client.get_quote(list(token_to_key.values())) or {}
    out = []
    for token, key in token_to_key.items():
        q = quotes.get(key) or quotes.get(key.split(":", 1)[-1]) or {}
        if not q:
            continue
        tick = dict(q)
        tick["instrument_token"] = token
        if "volume_traded" not in tick:
            tick["volume_traded"] = q.get("volume") or 0
        out.append(tick)
    return out


async def _register_signals(uid: str, signals: list[GammaSignal]) -> None:
    if not signals:
        return
    session = session_for(uid)
    fresh: list[GammaSignal] = []
    for sig in signals:
        if sig.id in session.signals:
            continue
        session.signals[sig.id] = sig
        fresh.append(sig)
        session.note(
            sig.tier.lower(),
            f"{sig.contract.instrument.tradingsymbol}: {sig.tier} E1",
        )
    if fresh:
        _persist_signals(session)
    if auto_execute(uid):
        for sig in fresh:
            if sig.tier == TIER_ENTRY:
                result = await arm(uid, sig.id)
                if not result.get("ok"):
                    session.note(
                        "blocked",
                        f"AUTO {sig.contract.instrument.tradingsymbol}: "
                        f"{result.get('message')}",
                    )


def _product(pos_or_hold) -> str:
    hold = pos_or_hold.hold if hasattr(pos_or_hold, "hold") else str(pos_or_hold)
    return "MIS" if hold == "intraday" else "NRML"


async def arm(uid: str, signal_id: str) -> dict:
    cfg = get_config(uid)
    if not cfg.enabled:
        return {"ok": False, "message": "Gamma Unwind is switched off"}

    async with _lock_for(uid):
        session = session_for(uid, cfg)
        sig = session.signals.get(signal_id)
        if sig is None:
            return {"ok": False, "message": f"no E1 signal {signal_id}"}
        if sig.tier != TIER_ENTRY:
            return {
                "ok": False,
                "message": (
                    f"tier {sig.tier} is alert-only; only ENTRY may execute"
                ),
            }

        inst = sig.contract.instrument
        existing = positions_store.get(uid, inst.tradingsymbol)
        if existing and existing.is_open:
            return {
                "ok": False,
                "message": f"already holding {inst.tradingsymbol}",
            }

        lot_size = int(inst.lot_size or 0)
        if lot_size <= 0:
            return {
                "ok": False,
                "message": (
                    "broker lot size is unavailable; signal may be observed "
                    "but execution is fail-closed"
                ),
            }
        qty = int(cfg.execution_lots) * lot_size
        if qty <= 0:
            return {"ok": False, "message": "execution size resolves to zero"}

        client = await _client(uid)
        hold = (
            "intraday"
            if sig.contract.dte is not None and sig.contract.dte <= 1
            else "swing"
        )

        # The supplied reference deliberately has NO premium stop before T1 for
        # swing. Live execution cannot invent one merely to satisfy protection.
        # Paper mode may model the exact D68 daily-close rule.
        if getattr(client, "_is_paper", None) is False and hold == "swing":
            return {
                "ok": False,
                "code": "reference_swing_protection_unavailable",
                "message": (
                    "live swing ENTRY blocked: Gamma-Unwind uses the D68 "
                    "underlying daily-close stop and no premium stop before T1"
                ),
            }

        idem = f"{STRATEGY_ID}:{uid}:{sig.id}"
        safe, reason = _safety(uid, idem)
        if not safe:
            return {"ok": False, "message": reason}

        limit = align_to_tick(sig.entry, inst.tick_size, side="buy")
        from app.services.canonical_strategy_entry import submit_entry
        execution = await submit_entry(
            client=client,
            uid=uid,
            strategy_id=STRATEGY_ID,
            generation_id=CONTRACT_VERSION,
            signal_id=sig.id,
            exchange=inst.exchange,
            symbol=inst.tradingsymbol,
            quantity=qty,
            capital_price=float(limit),
            order_type="limit_order",
            limit_price=float(limit),
            product=_product(hold),
            tag=STRATEGY_ID,
            reason=(
                "Gamma-Unwind E1 tier=ENTRY; canonical Sterling safety passed"
            ),
        )
        if not execution.success:
            return {
                "ok": False,
                "status": execution.status,
                "message": execution.error or execution.status,
            }
        order_id = str(execution.order_id or "")
        if not order_id:
            return {
                "ok": False,
                "message": "canonical execution returned no order id",
            }

        pos = PositionState.from_signal(
            sig, fill=limit, quantity=qty, lots=cfg.execution_lots,
            at_ms=_now_ms(), day=_today_str(),
        )
        pos.order_id = order_id
        pos.idempotency_key = idem
        pos.account_id = str(getattr(client, "_account_id", "") or "")
        pos.stop_mode = cfg.stop_mode
        positions_store.put(uid, pos)

        from app.services.canonical_fill_reconciliation import (
            mark_projected, resolve_entry_fill,
        )
        resolution = await resolve_entry_fill(
            uid=uid,
            client=client,
            order_id=order_id,
            expected_quantity=qty,
            reference_price=float(limit),
        )
        if resolution.filled_quantity <= 0:
            if resolution.projection_version:
                mark_projected(resolution)
            if resolution.terminal:
                positions_store.mark_rejected(
                    uid, inst.tradingsymbol, resolution.status
                )
                return {
                    "ok": False,
                    "status": resolution.status,
                    "message": f"order {resolution.status.lower()}",
                }
            session.note(
                "warning",
                f"{inst.tradingsymbol}: entry unresolved; no retry will be sent",
            )
            return {
                "ok": False,
                "status": "PENDING_RECONCILIATION",
                "order_id": order_id,
                "message": (
                    resolution.reason
                    or "entry fill unresolved; reconciliation required"
                ),
            }

        pos.quantity = int(resolution.filled_quantity)
        lot = max(1, int(inst.lot_size or 1))
        pos.lots = max(1, (pos.quantity + lot - 1) // lot)
        pos = positions_store.mark_filled(
            uid, inst.tradingsymbol,
            resolution.average_price or limit,
        ) or pos
        if not resolution.settled or resolution.reconciliation_required:
            pos.status = positions_store.PENDING
            positions_store.put(uid, pos)

        if pos.hold == "intraday":
            protected = await _ensure_protection(uid, client, pos)
            if (getattr(client, "_is_paper", None) is False
                    and cfg.stop_mode != "monitor" and not protected):
                try:
                    from app.services import live_safety
                    live_safety.set_kill_switch(
                        True,
                        f"Gamma-Unwind protection unavailable for "
                        f"{inst.tradingsymbol}",
                    )
                except Exception:
                    pass
                session.note(
                    "warning",
                    f"{inst.tradingsymbol}: broker protection not confirmed",
                )

        if resolution.projection_version:
            mark_projected(resolution)
        await _subscribe_watched(uid, session)
        session.note(
            "entry",
            f"{inst.tradingsymbol}: bought {pos.quantity} @ "
            f"{pos.effective_entry}; {pos.hold}",
        )
        return {
            "ok": True,
            "order_id": order_id,
            "symbol": inst.tradingsymbol,
            "quantity": pos.quantity,
            "entry": pos.effective_entry,
            "hold": pos.hold,
            "t1": pos.t1,
            "t2": pos.t2,
            "stop": pos.stop or None,
            "paper": getattr(client, "_is_paper", None) is True,
            "status": (
                pos.status
                if resolution.settled and not resolution.reconciliation_required
                else "partial_reconciliation_required"
            ),
        }


async def _ensure_protection(uid: str, client, pos: PositionState) -> bool:
    """Place/move premium protection only where the reference defines one."""
    if pos.stop <= 0 or pos.quantity <= 0:
        return True
    if pos.stop_mode == "monitor":
        return True
    if not _position_account_matches(client, pos.account_id):
        return False
    try:
        from app.services.kite_engine.protective_stop import place_stop, move_stop
        from app.services.snapback_family_mode import (
            INTENT_PROTECT, canonical_broker_capability,
        )
        with canonical_broker_capability(
            f"gamma-unwind-protect:"
            f"{getattr(client, '_account_id', '') or 'paper'}:"
            f"{pos.instrument.tradingsymbol}",
            intent=INTENT_PROTECT,
        ):
            if pos.gtt_id:
                ok = await move_stop(
                    client,
                    trigger_id=int(pos.gtt_id),
                    tradingsymbol=pos.instrument.tradingsymbol,
                    exchange=pos.instrument.exchange,
                    qty=int(pos.quantity),
                    trigger_premium=float(pos.stop),
                    last_price=float(pos.effective_entry),
                    direction="long",
                    target_premium=0.0,
                    product=_product(pos),
                )
                if ok:
                    pos.gtt_at = float(pos.stop)
                    pos.gtt_qty = int(pos.quantity)
                    positions_store.put(uid, pos)
                    return True
                return False

            gid = await place_stop(
                client,
                tradingsymbol=pos.instrument.tradingsymbol,
                exchange=pos.instrument.exchange,
                qty=int(pos.quantity),
                trigger_premium=float(pos.stop),
                last_price=float(pos.effective_entry),
                direction="long",
                target_premium=0.0,
                product=_product(pos),
            )
        if gid:
            pos.gtt_id = int(gid)
            pos.gtt_at = float(pos.stop)
            pos.gtt_qty = int(pos.quantity)
            positions_store.put(uid, pos)
            return True
    except Exception as exc:
        log.warning("gamma_unwind: protection failed for %s: %s",
                    pos.instrument.tradingsymbol, exc)
    return False


async def _cancel_protection(client, pos: PositionState) -> bool:
    if not pos.gtt_id:
        return True
    if not _position_account_matches(client, pos.account_id):
        return False
    try:
        from app.services.kite_engine import protective_stop as pstop
        from app.services.snapback_family_mode import (
            INTENT_REDUCE, canonical_broker_capability,
        )
        gid = int(pos.gtt_id)
        with canonical_broker_capability(
            f"gamma-unwind-disarm:"
            f"{getattr(client, '_account_id', '') or 'paper'}:"
            f"{pos.instrument.tradingsymbol}:{gid}",
            intent=INTENT_REDUCE,
        ):
            outcome = await pstop.cancel_stop_result(client, gid)
        if outcome != pstop.CANCELLED:
            status = await pstop.stop_status(
                client, gid,
                tradingsymbol=pos.instrument.tradingsymbol,
                direction="long",
                exchange=pos.instrument.exchange,
                product=_product(pos),
                qty=int(pos.quantity),
            )
            if status != pstop.STOP_ABSENT:
                return False
        pos.gtt_id = 0
        pos.gtt_at = 0.0
        pos.gtt_qty = 0
        positions_store.put(uid, pos)
        return True
    except Exception as exc:
        log.warning("gamma_unwind: protection cancel failed: %s", exc)
        return False


async def _broker_long_quantity(client, pos: PositionState) -> Optional[int]:
    if getattr(client, "_is_paper", None) is True:
        return int(pos.quantity)
    if not _position_account_matches(client, pos.account_id):
        return None
    try:
        book = await client.get_positions()
    except Exception:
        return None
    rows = (book.get("net") or []) if isinstance(book, dict) else (book or [])
    total = 0
    matched = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("tradingsymbol") or "").upper() !=                 pos.instrument.tradingsymbol.upper():
            continue
        if str(row.get("exchange") or "").upper() !=                 pos.instrument.exchange.upper():
            continue
        if str(row.get("product") or "").upper() != _product(pos):
            continue
        matched = True
        raw = row.get("quantity")
        if raw is None:
            raw = row.get("net_quantity")
        try:
            total += int(float(raw or 0))
        except (TypeError, ValueError):
            return None
    return max(0, total) if matched else 0


async def _settle_known_exit(uid: str, client, pos: PositionState) -> tuple[bool, str]:
    """Resolve one acknowledged Gamma-Unwind SELL without ever sending another."""
    oid = str(pos.exit_order_id or "")
    if not oid:
        return False, "exit_identity_missing"

    from app.services.canonical_fill_reconciliation import resolve_acknowledged_order

    requested = int(pos.exit_requested_qty or pos.quantity)
    resolution = await resolve_acknowledged_order(
        client=client,
        order_id=oid,
        expected_quantity=requested,
        reference_price=float(
            pos.exit_requested_price or pos.effective_entry
        ),
        timeout_s=5.0,
    )

    if not resolution.settled:
        # Keep the exact SELL identity durable. No replacement GTT is armed
        # while that SELL can still fill, otherwise the exit and stop could both
        # execute and create a naked short.
        positions_store.put(uid, pos)
        if getattr(client, "_is_paper", None) is False:
            try:
                from app.services import live_safety
                live_safety.set_kill_switch(
                    True,
                    f"Gamma-Unwind exit unresolved for "
                    f"{pos.instrument.tradingsymbol} order {oid}",
                )
            except Exception:
                pass
        return False, "exit_reconciliation_required"

    sold = min(int(resolution.filled_quantity), int(pos.quantity))
    reason = str(pos.exit_pending_reason or "exit")
    fill = float(
        resolution.average_price
        or pos.exit_requested_price
        or pos.effective_entry
    )

    if sold <= 0:
        pos.exiting = False
        pos.exit_order_id = ""
        pos.exit_requested_qty = 0
        pos.exit_requested_price = 0.0
        pos.exit_pending_reason = ""
        positions_store.put(uid, pos)
        await _ensure_protection(uid, client, pos)
        return False, f"exit_{str(resolution.status or 'unfilled').lower()}"

    session = session_for(uid)
    pnl = realised_inr(pos, fill, sold)

    if sold < int(pos.quantity):
        pos.quantity = int(pos.quantity) - sold
        lot = max(1, int(pos.instrument.lot_size or 1))
        pos.lots = max(1, (pos.quantity + lot - 1) // lot)
        pos.partial_realised_inr += pnl
        pos.exiting = False
        pos.exit_order_id = ""
        pos.exit_requested_qty = 0
        pos.exit_requested_price = 0.0
        pos.exit_pending_reason = ""
        positions_store.put(uid, pos)
        session.record.add_realised(pnl, _today_str())
        positions_store.save_record(uid, session.record)
        protected = await _ensure_protection(uid, client, pos)
        if (pos.stop > 0 and pos.stop_mode != "monitor"
                and not protected
                and getattr(client, "_is_paper", None) is False):
            try:
                from app.services import live_safety
                live_safety.set_kill_switch(
                    True,
                    f"Gamma-Unwind partial exit remainder unprotected for "
                    f"{pos.instrument.tradingsymbol}",
                )
            except Exception:
                pass
        session.note(
            "warning",
            f"{pos.instrument.tradingsymbol}: exit filled {sold}/{requested} "
            f"@ {fill:g}; {pos.quantity} remains",
        )
        return False, "partial_exit"

    positions_store.close(uid, pos.instrument.tradingsymbol, reason)
    session.record.record(pnl, _today_str())
    positions_store.save_record(uid, session.record)
    session.note(
        "exit",
        f"{pos.instrument.tradingsymbol}: {reason} @ {fill:g}",
    )
    return True, "closed"


async def _execute_exit(uid: str, pos: PositionState, *,
                        market_ltp: float, reason: str,
                        grade_price: float | None = None) -> tuple[bool, str]:
    session = session_for(uid)
    client = await _client(uid)

    if pos.exiting:
        return await _settle_known_exit(uid, client, pos)
    if pos.status == positions_store.PENDING:
        return False, "entry_reconciliation_pending"

    broker_qty = await _broker_long_quantity(client, pos)
    if broker_qty is None or broker_qty < int(pos.quantity):
        return False, "broker_quantity_unverified"
    if not await _cancel_protection(client, pos):
        return False, "protection_not_released"

    pos.exiting = True
    pos.exit_pending_reason = str(reason)
    # This value is only a grading/reference fallback; live P&L uses broker fill.
    pos.exit_requested_price = float(grade_price or market_ltp)
    pos.exit_requested_qty = int(pos.quantity)
    positions_store.put(uid, pos)

    try:
        from app.services.snapback_family_mode import (
            INTENT_REDUCE, canonical_broker_capability,
        )
        with canonical_broker_capability(
            f"gamma-unwind-exit:"
            f"{getattr(client, '_account_id', '') or 'paper'}:"
            f"{pos.instrument.tradingsymbol}:{pos.signal_id}",
            intent=INTENT_REDUCE,
        ):
            res = await client.place_order(
                f"{pos.instrument.exchange}:{pos.instrument.tradingsymbol}",
                "sell",
                float(pos.quantity),
                order_type="market_order",
                product=_product(pos),
                tag="gamma-unwind-exit",
                allow_amo=False,
            )
        oid = str((res or {}).get("order_id") or "")
    except Exception as exc:
        pos.exiting = False
        positions_store.put(uid, pos)
        await _ensure_protection(uid, client, pos)
        session.note(
            "error",
            f"exit rejected for {pos.instrument.tradingsymbol}: {exc}",
        )
        return False, "submit_failed"

    if not oid:
        pos.exiting = False
        positions_store.put(uid, pos)
        await _ensure_protection(uid, client, pos)
        return False, "missing_order_id"

    pos.exit_order_id = oid
    positions_store.put(uid, pos)
    return await _settle_known_exit(uid, client, pos)


async def _manage_position_tick(uid: str, pos: PositionState,
                                ltp: float) -> str:
    tick = float(pos.instrument.tick_size or 0.05)
    eps = tick * 1e-6

    if pos.trade_state == "OPEN":
        if pos.hold == "intraday" and pos.stop > 0:
            strict = pos.stop >= pos.entry - eps
            stop_hit = (
                ltp < pos.stop - eps
                if strict
                else ltp <= pos.stop + eps
            )
            if stop_hit:
                ok, status = await _execute_exit(
                    uid, pos, market_ltp=ltp,
                    reason="SL", grade_price=pos.stop,
                )
                return "exited" if ok else status

        if ltp >= pos.t1 - eps:
            pos.trade_state = "T1_TRAIL"
            pos.stop = float(pos.effective_entry)
            pos.high_water = max(pos.high_water, ltp)
            positions_store.put(uid, pos)
            session_for(uid).note(
                "T1",
                f"{pos.instrument.tradingsymbol}: T1 hit; reference says "
                "book part (fraction unspecified), SL moved to entry",
            )
            # Do not invent a partial-booking fraction. Move only the reference
            # stop. If broker-side protection is configured, synchronize it.
            if pos.stop_mode != "monitor":
                protected = await _ensure_protection(
                    uid, await _client(uid), pos
                )
                if not protected and is_paper(uid) is not True:
                    try:
                        from app.services import live_safety
                        live_safety.set_kill_switch(
                            True,
                            f"Gamma-Unwind T1 protection not synchronized for "
                            f"{pos.instrument.tradingsymbol}",
                        )
                    except Exception:
                        pass

    if ltp >= pos.t2 - eps:
        ok, status = await _execute_exit(
            uid, pos, market_ltp=ltp,
            reason="T2", grade_price=ltp,
        )
        return "exited" if ok else status

    if (pos.trade_state == "T1_TRAIL"
            and ltp <= pos.effective_entry + eps):
        ok, status = await _execute_exit(
            uid, pos, market_ltp=ltp,
            reason="TRAIL", grade_price=pos.effective_entry,
        )
        return "exited" if ok else status

    return "watching"


async def on_ticks(uid: str, ticks: list) -> str:
    from app.services import gamma_move_scanner as scanner
    signals = scanner.process_ticks(uid, ticks)
    await _register_signals(uid, signals)

    by_token = {
        int(t.get("instrument_token") or 0): t for t in ticks or []
    }
    acted = "signal" if signals else "watching"
    for pos in list(positions_store.open_positions(uid)):
        try:
            token = int(pos.instrument.instrument_id)
        except (TypeError, ValueError):
            continue
        tick = by_token.get(token)
        if not tick:
            continue
        ltp = float(tick.get("last_price") or 0.0)
        if ltp <= 0:
            continue
        state = await _manage_position_tick(uid, pos, ltp)
        if state != "watching":
            acted = state
    return acted


async def _quote_ltp(client, pos: PositionState) -> float:
    key = f"{pos.instrument.exchange}:{pos.instrument.tradingsymbol}"
    quotes = await client.get_quote([key]) or {}
    row = quotes.get(key) or quotes.get(pos.instrument.tradingsymbol) or {}
    return float(row.get("last_price") or 0.0)


async def _underlying_daily_close(client, pos: PositionState,
                                  day: date) -> float | None:
    if not pos.underlying_token:
        return None
    try:
        raw = await client.get_historical(
            int(pos.underlying_token), "day",
            day.isoformat(), day.isoformat(), False, False,
        )
        rows = raw.get("candles") if isinstance(raw, dict) else raw
        if rows:
            row = rows[-1]
            return float(row.get("close") if isinstance(row, dict) else row[4])
    except Exception as exc:
        log.warning("gamma_unwind: underlying close unavailable for %s: %s",
                    pos.underlying, exc)
    return None


async def _reference_time_sweeps(uid: str,
                                 now: datetime | None = None) -> None:
    """Run the reference time exits with durable retry semantics.

    Schedule bookkeeping may suppress duplicate *successful* work, but it must
    never suppress recovery from a missing quote/history row, a rejected submit,
    or an acknowledged SELL that is still reconciling.
    """
    now = now or _now()
    today = now.date()
    session = session_for(uid)
    cfg = session.cfg
    if not positions_store.open_positions(uid):
        return
    client = await _client(uid)

    # First settle any reduction already acknowledged by the broker. This runs
    # every scheduler cycle even after the clock slot itself has been claimed.
    for pos in list(positions_store.open_positions(uid)):
        if not pos.exiting:
            continue
        await _settle_known_exit(uid, client, pos)

    # Reference D125/D26: 15:20 intraday, expiry and day-5 exits.
    if now.time() >= dtime(15, 20):
        key = (uid, today.isoformat(), "15:20")
        if key not in _schedule_done:
            complete = True
            for pos in list(positions_store.open_positions(uid)):
                expiry = (
                    date.fromisoformat(pos.instrument.expiry[:10])
                    if pos.instrument.expiry else today
                )
                day_k = trading_days_between(
                    date.fromisoformat(pos.entry_day[:10]), today
                )
                pos.sessions_held = day_k
                positions_store.put(uid, pos)

                reason = None
                if pos.hold == "intraday":
                    reason = "EOD"
                elif today >= expiry and day_k < cfg.swing_days:
                    reason = "EXPIRY"
                elif day_k >= cfg.swing_days:
                    reason = "DAY5"
                if reason is None:
                    continue

                # A reduction that is still working was reconciled above. Its
                # durable order id is sufficient for later cycles; never send a
                # second SELL.
                current = positions_store.get(
                    uid, pos.instrument.tradingsymbol
                ) or pos
                if current.exiting:
                    continue

                ltp = await _quote_ltp(client, current)
                if ltp <= 0:
                    complete = False
                    continue
                await _execute_exit(
                    uid, current, market_ltp=ltp,
                    reason=reason, grade_price=ltp,
                )
                after = positions_store.get(
                    uid, current.instrument.tradingsymbol
                )
                if after is not None and after.is_open and not after.exiting:
                    complete = False

            if complete:
                _schedule_done.add(key)

    # D68 daily-close underlying zone stop, only before T1.
    if now.time() >= dtime(15, 35):
        key = (uid, today.isoformat(), "15:35")
        if key not in _schedule_done:
            complete = True
            for pos in list(positions_store.open_positions(uid)):
                if pos.hold != "swing" or pos.trade_state != "OPEN":
                    continue
                if pos.exiting:
                    continue

                close = await _underlying_daily_close(client, pos, today)
                if close is None:
                    complete = False
                    continue
                action = d68_action(
                    pos.instrument.option_type,
                    pos.zone_edge,
                    close,
                    pos.swing_armed,
                )
                if action == "arm":
                    pos.swing_armed = True
                    positions_store.put(uid, pos)
                    session.note(
                        "SWING_ARMED",
                        f"{pos.instrument.tradingsymbol}: underlying closed "
                        f"through zone edge {pos.zone_edge:g}",
                    )
                elif action == "stop":
                    ltp = await _quote_ltp(client, pos)
                    if ltp <= 0:
                        complete = False
                        continue
                    await _execute_exit(
                        uid, pos, market_ltp=ltp,
                        reason="SWING_STOP", grade_price=ltp,
                    )
                    after = positions_store.get(
                        uid, pos.instrument.tradingsymbol
                    )
                    if after is not None and after.is_open and not after.exiting:
                        complete = False

            if complete:
                _schedule_done.add(key)


def _slot_time(now: datetime, hhmm: str) -> datetime | None:
    try:
        hh, mm = map(int, str(hhmm).split(":"))
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return None
        return now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    except Exception:
        return None


def _claim_latest_due_slot(
    uid: str,
    now: datetime,
    kind: str,
    slots: tuple[str, ...],
    *,
    not_after: dtime | None = None,
) -> str | None:
    """Claim the latest scheduled slot that is due, exactly once.

    A 5-second scheduler must not depend on waking inside a 6-second window.
    On restart, earlier due slots are marked consumed and only the latest due
    one is executed, matching the current market state instead of replaying
    stale 09:20 and 13:15 builds back-to-back.
    """
    if not_after is not None and now.time() > not_after:
        return None
    due: list[tuple[datetime, str]] = []
    day = now.date().isoformat()
    for raw in slots:
        target = _slot_time(now, raw)
        if target is None or target > now:
            continue
        key = (uid, day, f"{kind}:{raw}")
        if key not in _schedule_done:
            due.append((target, raw))
    if not due:
        return None
    due.sort()
    latest = due[-1][1]
    # Earlier missed slots are superseded by the latest current-state run.
    for _target, raw in due:
        _schedule_done.add((uid, day, f"{kind}:{raw}"))
    return latest


def _kite_user_ids() -> list[str]:
    try:
        from app.services.exchanges.kite import accounts
        accounts.bootstrap()
        return sorted({
            a.user_id for a in accounts.all_accounts() if a.is_active
        })
    except Exception:
        return []


async def scan_all_once(*, respect_interval: bool = False) -> dict[str, dict]:
    del respect_interval  # reference cadence is clock-driven, not global interval.
    out: dict[str, dict] = {}
    now = _now()
    for uid in _kite_user_ids():
        try:
            cfg = get_config(uid)
            session = session_for(uid, cfg)
            from app.services import gamma_move_scanner as scanner

            level_slot = _claim_latest_due_slot(
                uid,
                now,
                "levels",
                cfg.mark_levels_times,
            )
            build_slot = _claim_latest_due_slot(
                uid,
                now,
                "watchlist",
                cfg.build_watchlists_times,
                not_after=dtime(15, 20),
            )

            if level_slot is not None:
                out.setdefault(uid, {})["levels"] = await scanner.mark_levels(
                    uid, cfg, today=now.date()
                )

            # Reference builds run at 09:20 and 13:15. A process that starts
            # after one of those slots executes the latest due build once.
            # If a mid-session process has no runtime at all, recover once even
            # when the scheduler bookkeeping predates this process.
            recover_missing = (
                cfg.enabled
                and not scanner.has_watchers(uid)
                and dtime(9, 20) <= now.time() <= dtime(15, 20)
            )
            if build_slot is not None or recover_missing:
                built = await scan_once(uid, scheduled_slot=build_slot)
                if uid in out and isinstance(out[uid], dict):
                    out[uid]["watchlist"] = built
                else:
                    out[uid] = built

            if is_paper(uid) is True and scanner.has_watchers(uid):
                ticks = await _paper_poll_ticks(uid)
                if ticks:
                    await on_ticks(uid, ticks)

            await _reference_time_sweeps(uid, now)
            await _subscribe_watched(uid, session)
        except Exception as exc:
            log.warning("gamma_unwind cycle failed for %s: %s", uid, exc)
            out[uid] = {"error": str(exc)}
    return out


async def auto_scan_loop(interval: int = 300) -> None:
    del interval
    log.info(
        "gamma_unwind scheduler started: reference schedule + paper E1 polling"
    )
    try:
        await reconcile_all()
    except Exception as exc:
        log.warning("gamma_unwind startup reconcile failed: %s", exc)
    while True:
        try:
            await scan_all_once(respect_interval=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("gamma_unwind scheduler error: %s", exc)
        await asyncio.sleep(5)


def session_status(uid: str) -> Optional[dict]:
    session = _sessions.get(uid)
    if not session:
        return None
    positions = []
    for pos in positions_store.open_positions(uid):
        positions.append({
            "symbol": pos.instrument.tradingsymbol,
            "signal_id": pos.signal_id,
            "underlying": pos.underlying,
            "entry": pos.entry,
            "effective_entry": pos.effective_entry,
            "quantity": pos.quantity,
            "lots": pos.lots,
            "entered_ms": pos.entered_ms,
            "entry_day": pos.entry_day,
            "hold": pos.hold,
            "trade_state": pos.trade_state,
            "zone_edge": pos.zone_edge,
            "base_low": pos.base_low,
            "t1": pos.t1,
            "t2": pos.t2,
            "stop": pos.stop or None,
            "sessions_held": pos.sessions_held,
            "swing_armed": pos.swing_armed,
            "status": pos.status,
            "order_id": pos.order_id,
            "gtt_id": pos.gtt_id,
            "gtt_at": pos.gtt_at,
            "gtt_qty": pos.gtt_qty,
            "broker_stop": bool(
                pos.gtt_id
                and int(pos.gtt_qty or 0) == int(pos.quantity)
                and pos.stop > 0
                and abs(float(pos.gtt_at or 0.0) - float(pos.stop)) < 0.05
            ),
            "stop_mode": pos.stop_mode,
        })
    rows = [s.as_dict() for s in session.signals.values()]
    return {
        "day": session.day,
        "phase": "watching" if has_tick_work(uid) else "idle",
        "halt_reason": "",
        "trades_today": 0,
        "signals": rows,
        "candidates": rows,
        "positions": positions,
        "record": session.record.as_dict(),
        "subscribed": sorted(session.subscribed),
        "notes": session.notes[-12:],
    }


def scan_state(uid: str) -> dict:
    from app.services.gamma_move_scanner import scan_stats
    return scan_stats(uid)


async def reconcile(uid: str) -> dict:
    """Reconcile durable broker truth; never blind-retry an ambiguous order."""
    session = session_for(uid)
    restored = 0
    ownership_errors: list[str] = []
    vanished: list[str] = []
    try:
        client = await _client(uid)
    except Exception as exc:
        return {
            "restored": 0,
            "ownership_errors": [],
            "vanished": [],
            "error": str(exc),
        }

    for pos in list(positions_store.open_positions(uid)):
        if not _position_account_matches(client, pos.account_id):
            ownership_errors.append(pos.instrument.tradingsymbol)
            continue

        if pos.exiting:
            done, _ = await _settle_known_exit(uid, client, pos)
            if done:
                continue
            current = positions_store.get(uid, pos.instrument.tradingsymbol)
            if current is not None and current.exiting:
                continue
            pos = current or pos

        # Resolve an ambiguous/partial ENTRY from its known order identity.
        if pos.status == positions_store.PENDING and pos.order_id:
            try:
                from app.services.canonical_fill_reconciliation import (
                    mark_projected, resolve_known_entry,
                )
                resolution = await resolve_known_entry(
                    uid=uid,
                    client=client,
                    order_id=pos.order_id,
                    reference_price=float(pos.entry),
                    timeout_s=0.0,
                )
            except Exception as exc:
                session.note(
                    "warning",
                    f"{pos.instrument.tradingsymbol}: entry reconcile failed: {exc}",
                )
                continue
            if resolution.filled_quantity <= 0:
                if resolution.projection_version:
                    mark_projected(resolution)
                if resolution.terminal:
                    positions_store.mark_rejected(
                        uid, pos.instrument.tradingsymbol, resolution.status
                    )
                continue
            pos.quantity = int(resolution.filled_quantity)
            filled = positions_store.mark_filled(
                uid, pos.instrument.tradingsymbol,
                resolution.average_price or pos.entry,
            ) or pos
            if not resolution.settled or resolution.reconciliation_required:
                filled.status = positions_store.PENDING
                positions_store.put(uid, filled)
                if filled.hold == "intraday":
                    protected = await _ensure_protection(uid, client, filled)
                    if (filled.stop > 0 and filled.stop_mode != "monitor"
                            and not protected
                            and getattr(client, "_is_paper", None) is False):
                        try:
                            from app.services import live_safety
                            live_safety.set_kill_switch(
                                True,
                                f"Gamma-Unwind partial entry unprotected for "
                                f"{filled.instrument.tradingsymbol}",
                            )
                        except Exception:
                            pass
                continue
            pos = filled
            if resolution.projection_version:
                mark_projected(resolution)

        if getattr(client, "_is_paper", None) is False:
            broker_qty = await _broker_long_quantity(client, pos)
            if broker_qty == 0:
                vanished.append(pos.instrument.tradingsymbol)
                positions_store.close(
                    uid, pos.instrument.tradingsymbol,
                    "not at broker on reconcile",
                )
                continue
            if broker_qty is None or broker_qty < int(pos.quantity):
                session.note(
                    "warning",
                    f"{pos.instrument.tradingsymbol}: broker quantity "
                    "does not match durable Gamma-Unwind state",
                )
                continue

        if pos.stop > 0 and pos.stop_mode != "monitor":
            protected = await _ensure_protection(uid, client, pos)
            if (not protected
                    and getattr(client, "_is_paper", None) is False):
                try:
                    from app.services import live_safety
                    live_safety.set_kill_switch(
                        True,
                        f"Gamma-Unwind protection not synchronized for "
                        f"{pos.instrument.tradingsymbol}",
                    )
                except Exception:
                    pass
        restored += 1

    await _subscribe_watched(uid, session)
    return {
        "restored": restored,
        "ownership_errors": ownership_errors,
        "vanished": vanished,
    }


async def reconcile_all() -> dict[str, dict]:
    out = {}
    for uid in _kite_user_ids():
        try:
            out[uid] = await reconcile(uid)
        except Exception as exc:
            out[uid] = {"error": str(exc)}
    return out


async def orphan_positions(uid: str, cfg: GammaMoveConfig | None = None) -> list:
    """Compatibility surface: report broker options not owned by this registry."""
    del cfg
    try:
        client = await _client(uid)
        book = await client.get_positions()
    except Exception:
        return []
    rows = (book.get("net") or []) if isinstance(book, dict) else (book or [])
    managed = {
        p.instrument.tradingsymbol.upper()
        for p in positions_store.open_positions(uid)
    }
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("tradingsymbol") or "").upper()
        if not symbol or symbol in managed:
            continue
        try:
            qty = int(float(
                row.get("quantity")
                if row.get("quantity") is not None
                else row.get("net_quantity") or 0
            ))
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        out.append({
            "symbol": symbol,
            "quantity": qty,
            "entry_price": float(row.get("average_price") or 0.0),
        })
    return out


async def adopt(uid: str, symbol: str,
                quantity: int, entry_price: float) -> dict:
    session = session_for(uid)
    sig = next((
        s for s in session.signals.values()
        if s.contract.instrument.tradingsymbol == symbol
        and s.tier == TIER_ENTRY
    ), None)
    if sig is None:
        return {
            "ok": False,
            "message": (
                "adopt requires a current ENTRY E1 so zone/base/hold "
                "context is known"
            ),
        }
    lot = max(1, int(sig.contract.instrument.lot_size or 1))
    lots = max(1, (int(quantity) + lot - 1) // lot)
    pos = PositionState.from_signal(
        sig,
        fill=float(entry_price),
        quantity=int(quantity),
        lots=lots,
        at_ms=_now_ms(),
        day=_today_str(),
    )
    client = await _client(uid)
    pos.account_id = str(getattr(client, "_account_id", "") or "")
    pos.status = positions_store.OPEN
    pos.stop_mode = get_config(uid).stop_mode

    if getattr(client, "_is_paper", None) is False and pos.hold == "swing":
        return {
            "ok": False,
            "code": "reference_swing_protection_unavailable",
            "message": (
                "live swing adopt blocked: exact D68 pre-T1 protection "
                "cannot be represented as a broker premium stop"
            ),
        }

    positions_store.put(uid, pos)
    if pos.hold == "intraday":
        await _ensure_protection(uid, client, pos)
    await _subscribe_watched(uid, session)
    return {
        "ok": True,
        "symbol": symbol,
        "hold": pos.hold,
        "t1": pos.t1,
        "t2": pos.t2,
        "stop": pos.stop or None,
    }
