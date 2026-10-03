"""Session runner for Gamma Move: scanning, arming, and watching positions out."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.core.logging import get_logger
from app.engines.gamma_move import (CONTRACT_VERSION, GammaMoveConfig, GammaMoveStrategy,
                                    GammaSignal, Intent, PositionState, SessionState,
                                    STRATEGY_ID, align_to_tick, exit_order_price, q2)
from app.services import gamma_move_positions as positions_store
from app.services.gamma_move import get_config, ist_today

log = get_logger(__name__)
_IST = timezone(timedelta(hours=5, minutes=30))
_OWNER = STRATEGY_ID
_sessions: dict[str, "Session"] = {}
_locks: dict[str, asyncio.Lock] = {}
_last_auto_scan_monotonic: dict[str, float] = {}


def _lock_for(uid: str) -> asyncio.Lock:
    lock = _locks.get(uid)
    if lock is None:
        lock = _locks[uid] = asyncio.Lock()
    return lock


def _now_ms() -> int:
    return int(datetime.now(_IST).timestamp() * 1000)


def _today_str() -> str:
    return datetime.now(_IST).strftime("%Y-%m-%d")


def _is_market_open(cfg: GammaMoveConfig) -> bool:
    now = datetime.now(_IST)
    if now.weekday() >= 5:
        return False
    return cfg.session_start <= now.strftime("%H:%M") <= cfg.session_end


async def _client(uid: str):
    from app.services.exchanges.kite import accounts
    acct = accounts.get_active(uid)
    if not acct:
        raise RuntimeError("No active Kite account")
    return await accounts.acquire_client(acct)


def _position_account_matches(client, account_id: str) -> bool:
    """True only when this client may mutate the position it is being asked to manage."""
    mode = getattr(client, "_is_paper", None)
    if mode is True:
        return True
    if mode is not False:
        return False
    owner = str(account_id or "").strip()
    current = str(getattr(client, "_account_id", "") or "").strip()
    return bool(owner and current and owner == current)


def is_paper(uid: str) -> Optional[bool]:
    """Account execution mode: True=paper, False=live, None=unknown."""
    try:
        from app.services.exchanges.kite import accounts
        acct = accounts.get_active(uid)
        if acct is None:
            return None
        mode = getattr(acct, "is_paper", None)
        return mode if mode is True or mode is False else None
    except Exception as exc:
        log.warning("gamma_move: account mode unavailable for %s: %s", uid, exc)
        return None


def auto_execute(uid: str) -> bool:
    """True only when both master AUTO and Gamma Move AUTO are armed."""
    try:
        from app.services.kite_engine import state as engine_state
        shared = bool(getattr(engine_state.get_config(uid), "auto_execute", False))
        strategy = bool(get_config(uid).auto_execute)
        return shared and strategy
    except Exception as exc:
        log.error("gamma_move: auto-execute state unavailable for %s: %s", uid, exc)
        return False


def spread_blocks_entry(quote: dict, cfg: GammaMoveConfig) -> Optional[str]:
    """Refuse a quote whose executable spread cannot satisfy the configured cap.

    The scanner's earlier quote is not execution evidence: the book can disappear
    between scan and arm. When the gate is enabled, missing/torn depth is UNKNOWN
    and must fail closed. A non-positive cap explicitly disables this check.
    """
    from app.services.gamma_move_scanner import _spread_pct
    cap = float(cfg.max_spread_pct or 0.0)
    if cap <= 0:
        return None
    spread = _spread_pct(quote or {})
    if spread is None:
        return "spread unavailable — two-sided depth is required"
    if spread > cap:
        return f"spread {spread:.1f}% exceeds {cap:g}%"
    return None


def _safety(uid: str, idempotency_key: Optional[str]) -> tuple[bool, str]:
    try:
        from app.services.live_safety import assert_safe_to_trade
        decision = assert_safe_to_trade([], idempotency_key,
                                        check_daily_loss=True, uid=uid)
        return bool(decision.allowed), str(decision.reason or "")
    except Exception as exc:
        log.error("gamma_move: safety check failed closed for %s: %s", uid, exc)
        return False, f"safety check unavailable: {exc}"


async def _confirm_fill(client, order_id: str) -> tuple[str, float]:
    if str(order_id).startswith("PAPER-"):
        return "COMPLETE", 0.0
    try:
        history = await client.get_order_history(order_id)
    except Exception as exc:
        log.warning("gamma_move: order status unavailable for %s: %s", order_id, exc)
        return "UNKNOWN", 0.0
    if not history:
        return "UNKNOWN", 0.0
    last = history[-1] if isinstance(history, list) else history
    status = str((last or {}).get("status") or "").upper()
    try:
        avg = float((last or {}).get("average_price") or 0.0)
    except (TypeError, ValueError):
        avg = 0.0
    return status, avg


class Session:
    def __init__(self, uid: str, cfg: GammaMoveConfig):
        self.uid = uid
        self.cfg = cfg
        self.strategy = GammaMoveStrategy(cfg, SessionState(day=_today_str()))
        self.signals: dict[str, GammaSignal] = {}
        self.subscribed: set[int] = set()
        self.notes: list[dict] = []

    def note(self, kind: str, message: str) -> None:
        self.notes.append({"kind": kind, "message": message, "at_ms": _now_ms()})
        del self.notes[:-40]


def session_for(uid: str, cfg: Optional[GammaMoveConfig] = None) -> Session:
    """Reuse the in-memory session across midnight."""
    cfg = cfg or get_config(uid)
    s = _sessions.get(uid)
    if s is None:
        s = _sessions[uid] = Session(uid, cfg)
        s.strategy.state.record = positions_store.load_record(uid)
        from app.services import db
        try:
            raw = db.get_config(f"gamma_move_signals:{uid}")
            if raw:
                import json
                rows = json.loads(raw) if isinstance(raw, str) else raw
                for r in rows:
                    sig = GammaSignal.from_dict(r)
                    s.signals[sig.id] = sig
        except Exception as exc:
            log.debug("gamma_move: signal hydration failed for %s: %s", uid, exc)
    s.cfg = cfg
    s.strategy.cfg = cfg
    s.strategy.state.roll(_today_str())
    return s


def clear(uid: Optional[str] = None) -> None:
    if uid:
        _sessions.pop(uid, None)
    else:
        _sessions.clear()


def _replay_owns_the_board() -> bool:
    try:
        from app.services.simulation import simulation_runner
        return bool(simulation_runner.has_session_view)
    except Exception:
        return False


async def scan_once(uid: str) -> dict:
    if _replay_owns_the_board():
        return {"scanned": 0, "armed": 0, "signals": [],
                "message": "replay is driving this board — live scan is off"}
    cfg = get_config(uid)
    if not cfg.enabled:
        return {"scanned": 0, "armed": 0, "signals": [], "message": "engine disabled"}
    async with _lock_for(uid):
        session = session_for(uid, cfg)
        from app.services.gamma_move_scanner import scan_once as _scan
        signals = await _scan(uid, cfg, session.strategy)
        session.signals = {s.id: s for s in signals}
        from app.services import db
        try:
            import json
            db.set_config(f"gamma_move_signals:{uid}",
                          json.dumps([s.as_dict() for s in signals], separators=(",", ":")))
        except Exception as exc:
            log.warning("gamma_move: could not persist scan signals for %s: %s", uid, exc)
        await _subscribe_watched(uid, session)
        armed = [s for s in signals if s.state == "armed"]
        return {"scanned": len(signals), "armed": len(armed),
                "signals": [s.as_dict() for s in signals]}


async def _subscribe_watched(uid: str, session: Session) -> None:
    tokens = {int(s.candidate.instrument.instrument_id)
              for s in session.signals.values()
              if s.candidate.instrument.instrument_id.isdigit()}
    tokens |= {int(p.instrument.instrument_id)
               for p in session.strategy.state.positions.values()
               if p.instrument.instrument_id.isdigit()}
    new = tokens - session.subscribed
    stale = session.subscribed - tokens
    if new:
        try:
            from app.services.exchanges.kite import ticker_manager
            from app.services.exchanges.kite import constants as K
            await ticker_manager.subscribe(uid, sorted(new), K.MODE_FULL, owner=_OWNER)
            session.subscribed |= new
        except Exception as exc:
            log.warning("gamma_move subscribe failed for %s: %s", uid, exc)
    if stale:
        await _release(uid, session, stale)


async def _release(uid: str, session: Session, tokens: set) -> None:
    if not tokens:
        return
    try:
        from app.services.exchanges.kite import ticker_manager
        await ticker_manager.release(uid, sorted(tokens), owner=_OWNER)
    except Exception as exc:
        log.debug("gamma_move release failed for %s: %s", uid, exc)
    session.subscribed -= tokens


async def release_subscriptions(uid: str) -> None:
    session = _sessions.get(uid)
    if session:
        await _release(uid, session, set(session.subscribed))


async def arm(uid: str, signal_id: str) -> dict:
    if _replay_owns_the_board():
        return {"ok": False, "message": "replay is driving this board — live entry is off"}
    cfg = get_config(uid)
    if not cfg.enabled:
        return {"ok": False, "message": "Gamma Move engine is switched off"}
    async with _lock_for(uid):
        session = session_for(uid, cfg)
        signal = session.signals.get(signal_id)
        if signal is None:
            return {"ok": False, "message": f"no signal {signal_id} in this session"}
        inst = signal.candidate.instrument
        client = await _client(uid)

        # Canonical transport is necessary but not sufficient for real-money
        # promotion. The authoritative validation artifact still says
        # NOT VALIDATED, so a live account may observe/scan but cannot originate
        # new Gamma Move exposure until that evidence changes.
        if getattr(client, "_is_paper", None) is False:
            from app.services.gamma_move import descriptor
            if not bool(descriptor().get("validated")):
                session.note(
                    "blocked",
                    f"{inst.tradingsymbol}: Gamma Move validation verdict is NOT VALIDATED",
                )
                return {
                    "ok": False,
                    "message": "strategy_promotion_required: Gamma Move is not validated",
                    "code": "strategy_promotion_required",
                }

        try:
            quotes = await client.get_quote([f"{inst.exchange}:{inst.tradingsymbol}"]) or {}
        except Exception as exc:
            session.note("error", f"quote failed for {inst.tradingsymbol}: {exc}")
            return {"ok": False, "message": str(exc)}
        q = quotes.get(f"{inst.exchange}:{inst.tradingsymbol}") or quotes.get(inst.tradingsymbol) or {}
        why = spread_blocks_entry(q, cfg)
        if why:
            session.note("blocked", f"entry refused for {inst.tradingsymbol}: {why}")
            return {"ok": False, "message": why}
        blocker = session.strategy.admit(signal, _today_str())
        if blocker:
            return {"ok": False, "message": blocker}
        if signal.entry is None or signal.quantity is None or signal.quantity <= 0:
            return {"ok": False, "message": "signal has no priced entry or size"}
        if positions_store.get(uid, inst.tradingsymbol) and \
                positions_store.get(uid, inst.tradingsymbol).is_open:
            return {"ok": False, "message": f"already holding {inst.tradingsymbol}"}
        idem = f"{STRATEGY_ID}:{uid}:{signal.id}:{_today_str()}"
        ok, why = _safety(uid, idem)
        if not ok:
            session.note("blocked", f"entry refused for {inst.tradingsymbol}: {why}")
            return {"ok": False, "message": why}
        limit = align_to_tick(signal.entry, inst.tick_size, side="buy")
        from app.services.canonical_strategy_entry import submit_entry
        execution = await submit_entry(
            client=client,
            uid=uid,
            strategy_id=STRATEGY_ID,
            generation_id=CONTRACT_VERSION,
            signal_id=signal.id,
            exchange=inst.exchange,
            symbol=inst.tradingsymbol,
            quantity=int(signal.quantity),
            capital_price=float(limit),
            order_type="limit_order",
            limit_price=float(limit),
            product="MIS",
            tag=STRATEGY_ID,
            reason="Gamma Move signal, spread, sizing and safety gates passed",
        )
        if not execution.success:
            session.note(
                "blocked" if execution.status == "HALTED" else "error",
                f"entry refused for {inst.tradingsymbol}: {execution.error}",
            )
            return {
                "ok": False,
                "message": execution.error or execution.status,
                "status": execution.status,
            }
        order_id = str(execution.order_id or "")
        if not order_id:
            session.note("error", f"{inst.tradingsymbol}: canonical execution returned no order id")
            return {"ok": False, "message": "canonical execution returned no order id"}
        pos = session.strategy.on_entry(signal, limit, _now_ms(), _today_str())
        pos.order_id = order_id
        pos.stop_mode = cfg.stop_mode
        pos.idempotency_key = idem
        pos.account_id = str(getattr(client, "_account_id", "") or "")
        positions_store.put(uid, pos)
        from app.services.canonical_fill_reconciliation import (
            mark_projected as mark_fill_projected,
            resolve_entry_fill,
        )
        resolution = await resolve_entry_fill(
            uid=uid,
            client=client,
            order_id=order_id,
            expected_quantity=int(signal.quantity),
            reference_price=float(limit),
        )
        if resolution.filled_quantity <= 0:
            if resolution.projection_version:
                mark_fill_projected(resolution)
            if resolution.terminal:
                positions_store.mark_rejected(
                    uid, inst.tradingsymbol, resolution.status
                )
                session.strategy.state.positions.pop(inst.tradingsymbol, None)
                session.strategy.state.trades_today = max(
                    0, session.strategy.state.trades_today - 1)
                session.note(
                    "error",
                    f"{inst.tradingsymbol} {resolution.status.lower()} at the broker",
                )
                return {
                    "ok": False,
                    "message": f"order {resolution.status.lower()}",
                    "status": resolution.status,
                }
            session.note(
                "warning",
                f"{inst.tradingsymbol}: entry fill unresolved; no protection placed "
                "and no retry will be sent",
            )
            return {
                "ok": False,
                "message": resolution.reason or "entry fill unresolved; reconciliation required",
                "status": "PENDING_RECONCILIATION",
                "order_id": order_id,
            }

        pos.quantity = int(resolution.filled_quantity)
        pos.lots = max(1, pos.quantity // max(1, inst.lot_size))
        filled = positions_store.mark_filled(
            uid,
            inst.tradingsymbol,
            resolution.average_price or limit,
        )
        if filled is not None:
            pos.stop, pos.fill_price, pos.status = (
                filled.stop,
                filled.fill_price,
                filled.status,
            )
        if not resolution.settled or resolution.reconciliation_required:
            pos.status = positions_store.PENDING
            positions_store.put(uid, pos)
        gtt_id = await _place_protection(uid, client, pos, cfg)
        if resolution.projection_version:
            mark_fill_projected(resolution)
        await _subscribe_watched(uid, session)
        if not resolution.settled or resolution.reconciliation_required:
            session.note(
                "warning",
                f"{inst.tradingsymbol}: protected {pos.quantity} confirmed units; "
                "entry remainder requires reconciliation",
            )
        else:
            session.note(
                "entry",
                f"bought {pos.quantity} {inst.tradingsymbol} @ {pos.effective_entry} "
                f"(stop {pos.stop})",
            )
        return {
            "ok": True,
            "order_id": order_id,
            "symbol": inst.tradingsymbol,
            "quantity": pos.quantity,
            "entry": pos.effective_entry,
            "stop": pos.stop,
            "gtt_id": gtt_id,
            "paper": getattr(client, "_is_paper", None) is True,
            "status": (
                pos.status
                if resolution.settled and not resolution.reconciliation_required
                else "partial_reconciliation_required"
            ),
        }


async def _place_protection(uid: str, client, pos, cfg) -> int:
    if cfg.stop_mode == "monitor" or pos.stop <= 0 or pos.quantity <= 0:
        return 0
    if not _position_account_matches(client, pos.account_id):
        session = _sessions.get(uid)
        if session:
            session.note(
                "warning",
                f"{pos.instrument.tradingsymbol}: protection refused — broker account ownership unverified",
            )
        return 0
    try:
        from app.services.kite_engine.protective_stop import place_stop
        from app.services.snapback_family_mode import (
            INTENT_PROTECT,
            canonical_broker_capability,
        )
        with canonical_broker_capability(
            f"gamma-move-protect:{getattr(client, '_account_id', '') or 'paper'}:"
            f"{pos.instrument.tradingsymbol}",
            intent=INTENT_PROTECT,
        ):
            gtt_id = await place_stop(
                client, tradingsymbol=pos.instrument.tradingsymbol,
                exchange=pos.instrument.exchange, qty=int(pos.quantity),
                trigger_premium=float(pos.stop), last_price=float(pos.effective_entry),
                direction="long", target_premium=float(pos.target or 0.0),
                product="MIS")
    except Exception as exc:
        gtt_id = None
        log.warning("gamma_move: protective GTT failed for %s: %s",
                    pos.instrument.tradingsymbol, exc)
    if gtt_id:
        pos.gtt_id = int(gtt_id)
        pos.gtt_at = float(pos.stop)
        pos.gtt_qty = int(pos.quantity)
        positions_store.put(uid, pos)
        return int(gtt_id)
    session = _sessions.get(uid)
    if session:
        session.note("warning", f"{pos.instrument.tradingsymbol}: no broker stop")
    return 0


async def adopt(uid: str, symbol: str, quantity: int, entry_price: float) -> dict:
    if _replay_owns_the_board():
        return {"ok": False, "message": "replay is driving this board — live adopt is off"}
    if quantity <= 0 or entry_price <= 0:
        return {"ok": False, "message": "quantity and entry_price must be positive"}
    cfg = get_config(uid)
    async with _lock_for(uid):
        session = session_for(uid, cfg)
        match = next((s for s in session.signals.values()
                      if s.candidate.instrument.tradingsymbol == symbol), None)
        if match is None:
            return {"ok": False, "message": f"{symbol} is not a contract this scan knows"}
        inst = match.candidate.instrument
        stop = q2(float(entry_price) * (1 - cfg.stop_percent / 100.0))
        pos = PositionState(signal_id=match.id, instrument=inst, entry=q2(entry_price),
                            stop=stop, quantity=int(quantity),
                            lots=max(1, int(quantity) // max(1, inst.lot_size)),
                            entered_ms=_now_ms(), entry_day=_today_str(),
                            status=positions_store.OPEN,
                            fill_price=q2(entry_price), stop_mode=cfg.stop_mode)
        client = await _client(uid)
        pos.account_id = str(getattr(client, "_account_id", "") or "")
        broker_qty = await _broker_long_quantity(client, pos)
        if broker_qty is None or broker_qty < int(quantity):
            return {
                "ok": False,
                "message": (
                    f"cannot adopt {symbol}: broker quantity "
                    f"{broker_qty if broker_qty is not None else 'unknown'} "
                    f"is below requested {quantity}"
                ),
            }
        session.strategy.state.positions[symbol] = pos
        positions_store.put(uid, pos)
        gtt_id = await _place_protection(uid, client, pos, cfg)
        session.note("adopt", f"adopted {quantity} {symbol} @ {entry_price}, stop {stop}")
        await _subscribe_watched(uid, session)
        return {"ok": True, "symbol": symbol, "stop": stop, "gtt_id": gtt_id}


async def orphan_positions(uid: str, cfg: GammaMoveConfig) -> list[dict]:
    try:
        client = await _client(uid)
        book = await client.get_positions()
    except Exception as exc:
        log.debug("gamma_move position fetch failed for %s: %s", uid, exc)
        return []
    raw_rows = book.get("net") or [] if isinstance(book, dict) else (book or [])
    known = {
        sym for sym, p in positions_store.load(uid).items()
        if p.is_open and _position_account_matches(client, p.account_id)
    }
    out = []
    for r in raw_rows:
        if isinstance(r, dict):
            sym = str(r.get("tradingsymbol") or r.get("symbol") or "").strip().upper()
            qty = int(r.get("quantity") or r.get("size") or 0)
            exchange = str(r.get("exchange") or "").strip().upper()
            product = str(r.get("product") or r.get("position_type") or "").strip().upper()
            entry_price = float(r.get("average_price") or r.get("entry_price") or 0.0)
        else:
            sym = str(getattr(r, "symbol", "") or "").strip().upper()
            qty = int(getattr(r, "size", 0) or 0)
            exchange = str(getattr(r, "exchange", "") or "").strip().upper()
            underlying = str(getattr(r, "underlying", "") or "")
            if not exchange and ":" in underlying:
                exchange = underlying.split(":", 1)[0].strip().upper()
            product = str(
                getattr(r, "product", "")
                or getattr(r, "position_type", "")
                or ""
            ).strip().upper()
            entry_price = float(getattr(r, "entry_price", 0.0) or 0.0)
        if qty <= 0 or exchange not in ("NFO", "BFO") or product != "MIS" or sym in known:
            continue
        if not (sym.endswith("CE") or sym.endswith("PE")):
            continue
        out.append({"symbol": sym, "quantity": qty, "entry_price": q2(entry_price)})
    return out


def _clear_exit_identity(pos: PositionState) -> None:
    pos.exit_order_id = ""
    pos.exit_requested_qty = 0
    pos.exit_requested_price = 0.0
    pos.exit_pending_reason = ""
    pos.exiting = False


async def _settle_exit_order(
    uid: str,
    client,
    pos: PositionState,
    cfg: GammaMoveConfig,
    session: Session,
    *,
    timeout_s: float = 5.0,
) -> tuple[bool, str]:
    """Project only broker-confirmed SELL quantity into Gamma Move state."""
    from app.services.canonical_fill_reconciliation import resolve_acknowledged_order

    requested = int(pos.exit_requested_qty or 0)
    if not pos.exit_order_id or requested <= 0:
        return False, "no_pending_exit"

    resolution = await resolve_acknowledged_order(
        client=client,
        order_id=pos.exit_order_id,
        expected_quantity=requested,
        reference_price=float(pos.exit_requested_price or pos.effective_entry),
        timeout_s=timeout_s,
    )
    if not resolution.settled:
        positions_store.put(uid, pos)
        if getattr(client, "_is_paper", None) is False:
            try:
                from app.services import live_safety
                live_safety.set_kill_switch(
                    True,
                    f"Gamma Move exit unresolved for {pos.instrument.tradingsymbol} "
                    f"order {pos.exit_order_id}",
                )
            except Exception as exc:                               # noqa: BLE001
                log.error("gamma_move: could not engage kill switch: %s", exc)
        return False, resolution.reason or "exit_reconciliation_required"

    sold = min(int(resolution.filled_quantity), int(pos.quantity))
    if sold <= 0:
        status = resolution.status or "unfilled"
        _clear_exit_identity(pos)
        positions_store.put(uid, pos)
        await _place_protection(uid, client, pos, cfg)
        session.note(
            "warning",
            f"{pos.instrument.tradingsymbol}: exit {status.lower()} with no fill; "
            "position remains protected",
        )
        return False, status

    fill = float(resolution.average_price or pos.exit_requested_price or 0.0)
    if fill <= 0:
        positions_store.put(uid, pos)
        return False, "exit_fill_price_unknown"

    original_qty = int(pos.quantity)
    remaining = max(0, original_qty - sold)
    reason = pos.exit_pending_reason or "exit"
    today = _today_str()

    if remaining > 0:
        partial_pnl = (fill - pos.effective_entry) * sold
        pos.partial_realised_inr += partial_pnl
        pos.quantity = remaining
        lot = max(1, int(pos.instrument.lot_size or 1))
        pos.lots = max(1, (remaining + lot - 1) // lot)
        session.strategy.state.record.add_realised(partial_pnl, today)
        positions_store.save_record(uid, session.strategy.state.record)
        _clear_exit_identity(pos)
        positions_store.put(uid, pos)
        await _place_protection(uid, client, pos, cfg)
        session.note(
            "warning",
            f"{pos.instrument.tradingsymbol}: exit filled {sold}/{original_qty} @ "
            f"{fill:.2f}; {remaining} remains and was re-protected",
        )
        return False, "partial_exit"

    _clear_exit_identity(pos)
    positions_store.put(uid, pos)
    pnl = session.strategy.on_exit(pos, fill, today)
    positions_store.close(uid, pos.instrument.tradingsymbol, reason)
    positions_store.save_record(uid, session.strategy.state.record)
    session.note(
        "exit",
        f"sold {sold} {pos.instrument.tradingsymbol} @ {fill:.2f} "
        f"({reason}), Rs {pnl:,.0f}",
    )
    return True, resolution.status


async def reconcile(uid: str) -> dict:
    cfg = get_config(uid)
    session = session_for(uid, cfg)
    restored = 0
    reprotected = 0
    ownership_errors: list[str] = []
    try:
        client = await _client(uid)
    except Exception as exc:
        return {
            "restored": 0,
            "orphans": [],
            "vanished": [],
            "ownership_errors": [],
            "error": str(exc),
        }

    # The in-memory strategy session is uid-scoped, while the active broker
    # account may change underneath it. Rebuild the managed view from durable
    # positions owned by the current account; foreign rows remain persisted and
    # can be restored if that account becomes active again.
    session.strategy.state.positions.clear()
    for sym, pos in positions_store.load(uid).items():
        if not pos.is_open:
            continue
        if not _position_account_matches(client, pos.account_id):
            ownership_errors.append(sym)
            continue
        session.strategy.state.positions[sym] = pos
        restored += 1
    orphans = await orphan_positions(uid, cfg)
    vanished = []
    try:
        book = await client.get_positions()
        raw_rows = book.get("net") or [] if isinstance(book, dict) else (book or [])
        live: dict[tuple[str, str], int] = {}
        for r in raw_rows:
            if isinstance(r, dict):
                sym = str(r.get("tradingsymbol") or r.get("symbol") or "").strip().upper()
                exchange = str(r.get("exchange") or "").strip().upper()
                product = str(r.get("product") or r.get("position_type") or "").strip().upper()
                raw_qty = r.get("quantity")
                if raw_qty is None:
                    raw_qty = r.get("net_quantity")
                if raw_qty is None:
                    raw_qty = r.get("size")
            else:
                sym = str(getattr(r, "symbol", "") or "").strip().upper()
                exchange = str(getattr(r, "exchange", "") or "").strip().upper()
                underlying = str(getattr(r, "underlying", "") or "")
                if not exchange and ":" in underlying:
                    exchange = underlying.split(":", 1)[0].strip().upper()
                product = str(
                    getattr(r, "product", "")
                    or getattr(r, "position_type", "")
                    or ""
                ).strip().upper()
                raw_qty = getattr(r, "quantity", None)
                if raw_qty is None:
                    raw_qty = getattr(r, "size", 0)
            if not sym or not exchange or product != "MIS":
                continue
            qty = int(float(raw_qty or 0))
            key = (exchange, sym)
            live[key] = live.get(key, 0) + qty
        for sym, pos in list(session.strategy.state.positions.items()):
            if pos.status == positions_store.PENDING and pos.order_id:
                try:
                    from app.services.canonical_fill_reconciliation import (
                        mark_projected as mark_fill_projected,
                        resolve_known_entry,
                    )
                    resolution = await resolve_known_entry(
                        uid=uid,
                        client=client,
                        order_id=pos.order_id,
                        reference_price=float(pos.entry),
                        timeout_s=0.0,
                    )
                except ValueError as exc:
                    if "canonical_intent_not_found" not in str(exc):
                        session.note("warning", f"{sym}: pending entry reconcile failed: {exc}")
                        continue
                except Exception as exc:                           # noqa: BLE001
                    session.note("warning", f"{sym}: pending entry reconcile failed: {exc}")
                    continue
                else:
                    if resolution.filled_quantity <= 0:
                        if resolution.projection_version:
                            mark_fill_projected(resolution)
                        if resolution.terminal:
                            positions_store.mark_rejected(uid, sym, resolution.status)
                            session.strategy.state.positions.pop(sym, None)
                            restored = max(0, restored - 1)
                        else:
                            session.note(
                                "warning",
                                f"{sym}: entry remains under broker reconciliation",
                            )
                        continue

                    pos.quantity = int(resolution.filled_quantity)
                    lot = max(1, int(pos.instrument.lot_size or 1))
                    pos.lots = max(1, (pos.quantity + lot - 1) // lot)
                    filled_pos = positions_store.mark_filled(
                        uid,
                        sym,
                        resolution.average_price or pos.entry,
                    ) or pos
                    if not resolution.settled or resolution.reconciliation_required:
                        filled_pos.status = positions_store.PENDING
                        positions_store.put(uid, filled_pos)
                    if cfg.stop_mode != "monitor":
                        await _ensure_protection_synced(uid, client, filled_pos, cfg)
                    if resolution.projection_version:
                        mark_fill_projected(resolution)
                    pos = filled_pos
                    if pos.status == positions_store.PENDING:
                        session.note(
                            "warning",
                            f"{sym}: protected confirmed partial fill; "
                            "entry remainder is still unresolved",
                        )
                        continue

            if pos.exiting:
                if not pos.exit_order_id:
                    session.note(
                        "warning",
                        f"{sym}: exit was claimed before restart but no broker order id "
                        "was persisted; manual reconciliation required",
                    )
                    if getattr(client, "_is_paper", None) is False:
                        try:
                            from app.services import live_safety
                            live_safety.set_kill_switch(
                                True,
                                f"Gamma Move exit identity missing for {sym}",
                            )
                        except Exception as exc:                    # noqa: BLE001
                            log.error("gamma_move: could not engage kill switch: %s", exc)
                    continue
                try:
                    completed, status = await _settle_exit_order(
                        uid, client, pos, cfg, session, timeout_s=0.0
                    )
                except Exception as exc:                           # noqa: BLE001
                    session.note(
                        "warning",
                        f"{sym}: pending exit reconciliation failed: {exc}",
                    )
                    continue
                current = positions_store.get(uid, sym)
                if completed or current is None or not current.is_open:
                    restored = max(0, restored - 1)
                    continue
                pos = current
                if pos.exiting:
                    session.note(
                        "warning",
                        f"{sym}: exit order {pos.exit_order_id} remains unresolved "
                        f"({status})",
                    )
                    continue

            key = (
                str(pos.instrument.exchange or "").strip().upper(),
                str(sym or "").strip().upper(),
            )
            if live.get(key, 0) <= 0 and getattr(client, "_is_paper", None) is False:
                vanished.append(sym)
                positions_store.close(uid, sym, "not at the broker on reconcile")
                session.strategy.state.positions.pop(sym, None)
                continue
            if cfg.stop_mode != "monitor" and (
                not pos.gtt_id
                or int(pos.gtt_qty or 0) != int(pos.quantity)
                or abs(float(pos.stop) - float(pos.gtt_at or 0.0)) >= 0.05
            ):
                if await _ensure_protection_synced(uid, client, pos, cfg):
                    reprotected += 1
    except Exception as exc:
        log.warning("gamma_move: reconcile could not read the broker for %s: %s", uid, exc)
    if restored or orphans or vanished or reprotected:
        session.note("reconcile",
                     f"restored {restored}, {len(orphans)} unaccounted, "
                     f"{len(vanished)} gone at the broker, "
                     f"{reprotected} re-protected")
    await _subscribe_watched(uid, session)
    return {
        "restored": restored,
        "orphans": orphans,
        "vanished": vanished,
        "reprotected": reprotected,
        "ownership_errors": ownership_errors,
    }


async def on_ticks(uid: str, ticks: list) -> str:
    session = _sessions.get(uid)
    if not session or not session.strategy.state.positions:
        return "idle"
    cfg = session.cfg
    by_token = {int(t.get("instrument_token") or 0): t for t in ticks or []}
    today = _today_str()
    session_over = not _is_market_open(cfg)
    client = None
    acted = "watching"
    for pos in list(session.strategy.state.positions.values()):
        if pos.status == positions_store.PENDING:
            # Confirmed partial quantity may already have protection, but the
            # original BUY remainder is unresolved. Do not race it with a SELL.
            continue
        tid = int(pos.instrument.instrument_id) if pos.instrument.instrument_id.isdigit() else 0
        tick = by_token.get(tid)
        if not tick:
            continue
        ltp = float(tick.get("last_price") or 0)
        if ltp <= 0:
            continue
        decision = session.strategy.on_price(pos, ltp, _now_ms(), today,
                                             session_over=session_over)
        if decision.intent is not Intent.EXIT or not decision.exit_position:
            await _sync_trail(uid, pos, cfg)
            continue
        # on_price() claimed this exit in memory. Persist the claim before any
        # broker interaction so a restart cannot race a second SELL.
        positions_store.put(uid, pos)
        client = client or await _client(uid)
        broker_qty = await _broker_long_quantity(client, pos)
        if broker_qty is None or broker_qty < int(pos.quantity):
            pos.exiting = False
            positions_store.put(uid, pos)
            session.note(
                "warning",
                f"{pos.instrument.tradingsymbol}: exit held — broker quantity "
                f"{broker_qty if broker_qty is not None else 'unknown'} is below "
                f"local quantity {pos.quantity}",
            )
            continue
        if not await _cancel_protection(uid, client, pos):
            pos.exiting = False
            positions_store.put(uid, pos)
            continue
        price = exit_order_price(ltp, pos.instrument.tick_size)
        try:
            from app.services.snapback_family_mode import (
                INTENT_REDUCE,
                canonical_broker_capability,
            )
            with canonical_broker_capability(
                f"gamma-move-exit:{getattr(client, '_account_id', '') or 'paper'}:"
                f"{pos.instrument.tradingsymbol}:{pos.signal_id}",
                intent=INTENT_REDUCE,
            ):
                res = await client.place_order(
                    f"{pos.instrument.exchange}:{pos.instrument.tradingsymbol}", "sell",
                    float(pos.quantity), order_type="limit_order", limit_price=float(price),
                    product="MIS", tag=f"{STRATEGY_ID[:14]}-exit")
            oid = str((res or {}).get("order_id") or "")
        except Exception as exc:
            oid, err = "", str(exc)
            pos.exiting = False
            positions_store.put(uid, pos)
            await _place_protection(uid, client, pos, cfg)
            session.note("error", f"exit rejected for {pos.instrument.tradingsymbol}: {err}")
            continue
        if not oid:
            pos.exiting = False
            positions_store.put(uid, pos)
            await _place_protection(uid, client, pos, cfg)
            continue

        pos.exit_order_id = oid
        pos.exit_requested_qty = int(pos.quantity)
        pos.exit_requested_price = float(price)
        pos.exit_pending_reason = str(decision.exit_reason or "exit")
        positions_store.put(uid, pos)
        completed, status = await _settle_exit_order(
            uid, client, pos, cfg, session
        )
        if completed:
            acted = "exited"
        elif status == "partial_exit":
            acted = "partial_exit"
    if not session.strategy.state.positions:
        await _subscribe_watched(uid, session)
    return acted


async def _broker_long_quantity(client, pos) -> Optional[int]:
    """Broker-held MIS long quantity, or None when live inventory is unreadable.

    Authorization is bucket-specific: a same-symbol NRML holding cannot authorize
    an MIS SELL, and a same-symbol row on another exchange cannot authorize either.
    """
    if getattr(client, "_is_paper", None) is True:
        return int(pos.quantity)
    if not _position_account_matches(client, pos.account_id):
        return None
    try:
        book = await client.get_positions()
    except Exception as exc:
        log.warning("gamma_move: broker quantity unavailable for %s: %s",
                    pos.instrument.tradingsymbol, exc)
        return None

    rows = (book.get("net") or []) if isinstance(book, dict) else (book or [])
    want_symbol = str(pos.instrument.tradingsymbol or "").strip().upper()
    want_exchange = str(pos.instrument.exchange or "").strip().upper()
    want_product = "MIS"
    matched = False
    net_qty = 0

    for row in rows:
        if isinstance(row, dict):
            sym = str(row.get("tradingsymbol") or row.get("symbol") or "").strip().upper()
            exchange = str(row.get("exchange") or "").strip().upper()
            product = str(row.get("product") or row.get("position_type") or "").strip().upper()
            raw_qty = row.get("quantity")
            if raw_qty is None:
                raw_qty = row.get("net_quantity")
            if raw_qty is None:
                raw_qty = row.get("size")
        else:
            sym = str(getattr(row, "symbol", "") or "").strip().upper()
            exchange = str(getattr(row, "exchange", "") or "").strip().upper()
            product = str(
                getattr(row, "product", "")
                or getattr(row, "position_type", "")
                or ""
            ).strip().upper()
            raw_qty = getattr(row, "quantity", None)
            if raw_qty is None:
                raw_qty = getattr(row, "size", 0)

        if sym != want_symbol or exchange != want_exchange or product != want_product:
            continue
        try:
            qty = int(float(raw_qty or 0))
        except (TypeError, ValueError):
            return None
        matched = True
        net_qty += qty

    return max(0, net_qty) if matched else 0


async def _sync_trail(uid: str, pos, cfg, client=None) -> bool:
    """Synchronize the existing GTT's stop and protected quantity."""
    if not pos.gtt_id or pos.stop <= 0:
        return False
    stop_aligned = abs(pos.stop - float(pos.gtt_at or 0.0)) < 0.05
    qty_aligned = int(pos.gtt_qty or 0) == int(pos.quantity)
    if stop_aligned and qty_aligned:
        return True
    try:
        from app.services.kite_engine.protective_stop import move_stop
        from app.services.snapback_family_mode import (
            INTENT_PROTECT,
            canonical_broker_capability,
        )
        client = client or await _client(uid)
        if not _position_account_matches(client, pos.account_id):
            session = _sessions.get(uid)
            if session:
                session.note(
                    "warning",
                    f"{pos.instrument.tradingsymbol}: trail refused — broker account ownership unverified",
                )
            return False
        with canonical_broker_capability(
            f"gamma-move-trail:{getattr(client, '_account_id', '') or 'paper'}:"
            f"{pos.instrument.tradingsymbol}:{pos.gtt_id}",
            intent=INTENT_PROTECT,
        ):
            moved = await move_stop(
                client, trigger_id=int(pos.gtt_id),
                tradingsymbol=pos.instrument.tradingsymbol,
                exchange=pos.instrument.exchange, qty=int(pos.quantity),
                trigger_premium=float(pos.stop),
                last_price=float(pos.effective_entry), direction="long",
                target_premium=float(pos.target or 0.0), product="MIS")
        if moved:
            pos.gtt_at = float(pos.stop)
            pos.gtt_qty = int(pos.quantity)
            positions_store.put(uid, pos)
            return True
        session = _sessions.get(uid)
        if session:
            session.note(
                "warning",
                f"{pos.instrument.tradingsymbol}: broker stop/quantity update was not confirmed",
            )
        return False
    except Exception as exc:
        log.warning("gamma_move: could not move the broker stop for %s: %s",
                    pos.instrument.tradingsymbol, exc)
        return False


async def _ensure_protection_synced(uid: str, client, pos, cfg) -> bool:
    if cfg.stop_mode == "monitor":
        return True
    ok = (
        bool(await _place_protection(uid, client, pos, cfg))
        if not pos.gtt_id
        else await _sync_trail(uid, pos, cfg, client=client)
    )
    if not ok and getattr(client, "_is_paper", None) is False:
        try:
            from app.services import live_safety
            live_safety.set_kill_switch(
                True,
                f"Gamma Move protection not synchronized for "
                f"{pos.instrument.tradingsymbol} qty={pos.quantity}",
            )
        except Exception as exc:
            log.error("gamma_move: could not engage kill switch after protection failure: %s", exc)
    return ok


async def _cancel_protection(uid: str, client, pos) -> bool:
    if not pos.gtt_id:
        return _position_account_matches(client, pos.account_id)

    if not _position_account_matches(client, pos.account_id):
        return False

    from app.services.kite_engine import protective_stop as pstop
    from app.services.snapback_family_mode import (
        INTENT_REDUCE,
        canonical_broker_capability,
    )

    gid = int(pos.gtt_id)
    try:
        with canonical_broker_capability(
            f"gamma-move-exit-disarm:{getattr(client, '_account_id', '') or 'paper'}:"
            f"{pos.instrument.tradingsymbol}:{gid}",
            intent=INTENT_REDUCE,
        ):
            outcome = await pstop.cancel_stop_result(client, gid)
    except Exception as exc:
        log.warning("gamma_move: could not cancel broker stop %s for %s: %s",
                    gid, pos.instrument.tradingsymbol, exc)
        outcome = pstop.UNKNOWN

    if outcome != pstop.CANCELLED:
        status = await pstop.stop_status(
            client,
            gid,
            tradingsymbol=pos.instrument.tradingsymbol,
            direction="long",
            exchange=pos.instrument.exchange,
            product="MIS",
            qty=int(pos.quantity),
        )
        if status != pstop.STOP_ABSENT:
            session = _sessions.get(uid)
            if session:
                session.note(
                    "warning",
                    f"{pos.instrument.tradingsymbol}: exit held — "
                    f"broker GTT #{gid} is {status}",
                )
            return False

    pos.gtt_id = 0
    pos.gtt_at = 0.0
    pos.gtt_qty = 0
    positions_store.put(uid, pos)
    return True


def scan_state(uid: str) -> dict:
    from app.services.gamma_move_scanner import scan_stats
    return scan_stats(uid)


def session_status(uid: str) -> Optional[dict]:
    session = _sessions.get(uid)
    if not session:
        return None
    st = session.strategy.state
    positions = []
    for sym, p in st.positions.items():
        positions.append({
            "symbol": sym, "signal_id": p.signal_id, "entry": p.entry, "stop": p.stop,
            "trail": p.trail, "target": p.target, "quantity": p.quantity,
            "lots": p.lots, "entered_ms": p.entered_ms, "entry_day": p.entry_day,
            "sessions_held": p.sessions_held, "exiting": p.exiting,
            "high_water": p.high_water, "status": p.status, "order_id": p.order_id,
            "fill_price": p.fill_price, "effective_entry": p.effective_entry,
            "gtt_id": p.gtt_id, "stop_mode": p.stop_mode,
        })
    return {
        "day": st.day, "phase": st.phase.value, "halt_reason": st.halt_reason,
        "trades_today": st.trades_today,
        "candidates": [s.as_dict() for s in session.signals.values()],
        "positions": positions, "record": st.record.as_dict(),
        "subscribed": sorted(session.subscribed), "notes": session.notes[-10:],
    }


def _kite_user_ids() -> list[str]:
    try:
        from app.services.exchanges.kite import accounts
        accounts.bootstrap()
        return sorted({a.user_id for a in accounts.all_accounts() if a.is_active})
    except Exception:
        return []


async def scan_all_once(*, respect_interval: bool = False) -> dict[str, str]:
    if _replay_owns_the_board():
        return {"*": "replay is driving this board — live scan is off"}
    uids = _kite_user_ids()
    if not uids:
        return {"*": "no active accounts"}
    out: dict[str, str] = {}
    now_mono = time.monotonic()
    for uid in uids:
        cfg = get_config(uid)
        if respect_interval:
            last = _last_auto_scan_monotonic.get(uid)
            due_after = max(60, int(cfg.scan_interval_seconds))
            if last is not None and now_mono - last < due_after:
                out[uid] = "waiting"
                continue
            _last_auto_scan_monotonic[uid] = now_mono

        # Engine enablement gates NEW observations/entries, not ownership of
        # positions already opened. Reconcile first on the same bounded cadence
        # so a broker-side GTT/manual close cannot leave a phantom holding that
        # later blocks or duplicates exposure.
        try:
            maintenance = await reconcile(uid)
        except Exception as exc:
            out[uid] = f"reconcile error: {exc}"
            log.warning("gamma_move reconcile failed for %s: %s", uid, exc)
            continue

        if not cfg.enabled:
            open_count = len(positions_store.open_positions(uid))
            out[uid] = f"disabled; managing {open_count} open position(s)"
            continue
        if not _is_market_open(cfg):
            out[uid] = (
                f"outside session; reconciled {maintenance.get('restored', 0)} "
                "managed position(s)"
            )
            continue
        try:
            res = await scan_once(uid)
            note = f"{res['armed']} armed of {res['scanned']}"
            if res["armed"] and auto_execute(uid):
                taken = await _auto_enter(uid)
                note += f", auto-entered {taken}"
            out[uid] = note
        except Exception as exc:
            out[uid] = f"error: {exc}"
            log.warning("gamma_move scan failed for %s: %s", uid, exc)
    return out


async def _auto_enter(uid: str) -> int:
    session = _sessions.get(uid)
    if not session:
        return 0
    armed = sorted((s for s in session.signals.values() if s.state == "armed"),
                   key=lambda s: -s.candidate.oi)
    taken = 0
    for signal in armed:
        result = await arm(uid, signal.id)
        if result.get("ok"):
            taken += 1
        elif "limit" in str(result.get("message", "")) or "cap" in str(result.get("message", "")):
            break
    return taken


async def reconcile_all() -> dict[str, dict]:
    out = {}
    for uid in _kite_user_ids():
        try:
            out[uid] = await reconcile(uid)
        except Exception as exc:
            log.warning("gamma_move reconcile failed for %s: %s", uid, exc)
    return out


async def auto_scan_loop(interval: int = 300) -> None:
    log.info("gamma_move auto-scan loop started (every %ss)", interval)
    try:
        await reconcile_all()
    except Exception as exc:
        log.warning("gamma_move startup reconcile failed: %s", exc)
    while True:
        try:
            # Per-user cadence is enforced inside scan_all_once. A scheduler
            # tick must not use one global config to govern every tenant.
            await scan_all_once(respect_interval=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("gamma_move auto-scan cycle failed: %s", exc)
        await asyncio.sleep(max(5, min(30, int(interval))))
