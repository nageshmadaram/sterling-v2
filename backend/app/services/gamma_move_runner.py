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
import time
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
# Ephemeral last-traded premium for durable positions. Persisting every tick
# would turn market data into database write amplification; after a restart the
# next tick restores this display-only value.
_position_ltp: dict[tuple[str, str], float] = {}
_position_ltp_ms: dict[tuple[str, str], int] = {}

# E1 is a live tick event. An ENTRY row older than this is history, not an order
# ticket: price, OI and the forming 5m bar have all moved on.
ENTRY_SIGNAL_MAX_AGE_S = 180
_ENTRY_CLOCK_SKEW_S = 60
# One exit attempt in flight per position, and a short back-off after a failed
# attempt, so a tick stream cannot turn a broker hiccup into a REST storm or a
# double SELL.
_EXIT_RETRY_COOLDOWN_S = 3.0
_exit_locks: dict[tuple[str, str], asyncio.Lock] = {}
_exit_retry_at: dict[tuple[str, str], float] = {}
# Broker-triggered stop fills that could not be located yet (attempt counters).
_TRIGGERED_FILL_MAX_ATTEMPTS = 60
_triggered_fill_attempts: dict[tuple[str, str], int] = {}
# A synchronised broker stop is re-verified this often, not every 5s scheduler
# pass (each check is a broker read plus a GTT write).
_PROTECTION_RECHECK_S = 60.0
_protection_checked_at: dict[tuple[str, str], float] = {}

# Outcomes of feeding an exit into the account daily-loss accounting.
BOOK_LEDGER = "ledger"
BOOK_LEGACY = "legacy"
BOOK_ALREADY = "already"
BOOK_UNKNOWN = "unknown"


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
        for key in [key for key in _position_ltp if key[0] == uid]:
            _position_ltp.pop(key, None)
            _position_ltp_ms.pop(key, None)
        for table in (
            _exit_locks, _exit_retry_at, _triggered_fill_attempts,
            _protection_checked_at,
        ):
            for key in [key for key in table if key[0] == uid]:
                table.pop(key, None)
    else:
        _sessions.clear()
        _position_ltp.clear()
        _position_ltp_ms.clear()
        _exit_locks.clear()
        _exit_retry_at.clear()
        _triggered_fill_attempts.clear()
        _protection_checked_at.clear()


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
    positions = bool(positions_store.open_positions(uid))
    try:
        from app.services import gamma_move_scanner as scanner
        return positions or (
            bool(get_config(uid).enabled) and scanner.has_watchers(uid)
        )
    except Exception:
        return positions


async def _subscribe_watched(uid: str, session: Session) -> None:
    from app.services import gamma_move_scanner as scanner
    tokens: set[int] = set()
    if bool(get_config(uid).enabled):
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
    positions = positions_store.open_positions(uid)
    if not rts and not positions:
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
    # Position management must not depend on the current watchlist surviving a
    # rebuild. A paper position still needs T1/T2/SL ticks even when there are
    # zero active watch contracts.
    for pos in positions:
        try:
            token_to_key[int(pos.instrument.instrument_id)] = (
                f"{pos.instrument.exchange}:{pos.instrument.tradingsymbol}"
            )
        except (TypeError, ValueError):
            pass
        if pos.underlying_token:
            token_to_key[int(pos.underlying_token)] = f"NSE:{pos.underlying}"
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
                    _alert_entry_not_taken(uid, sig, result)


def _alert_entry_not_taken(uid: str, sig: GammaSignal, result: dict) -> None:
    """Tell the operator, once, why an AUTO ENTRY signal was not entered.

    Without this an unattended AUTO session silently skips every swing ENTRY
    (live Gamma trades intraday only) and the operator only learns from the board.
    """
    from app.services.notifications.telegram_lifecycle import enqueue_best_effort
    reason = str(result.get("message") or result.get("code") or "entry not accepted")
    enqueue_best_effort(
        uid,
        dedupe_key=f"gamma:entry-not-taken:{sig.id}",
        strategy="Gamma-Unwind",
        event_type="ENTRY NOT TAKEN",
        title=sig.contract.instrument.tradingsymbol,
        detail=f"{hold_for_dte(sig.contract.dte).upper()} · {reason}",
        severity="WARNING",
    )


def _product(pos_or_hold) -> str:
    # Holding period determines strategy exits, not the broker margin product.
    # Zerodha GTT supports NRML; MIS cannot provide this broker protection.
    if hasattr(pos_or_hold, "product"):
        product = str(pos_or_hold.product or "").upper()
        if product not in {"MIS", "NRML"}:
            raise ValueError("gamma_position_product_unknown")
        return product
    if str(pos_or_hold) not in {"intraday", "swing"}:
        raise ValueError("gamma_hold_unknown")
    return "NRML"


def hold_for_dte(dte: int | None) -> str:
    return "intraday" if dte is not None and dte <= 1 else "swing"


_SWING_LIVE_MESSAGE = (
    "Swing hold (expiry more than 1 trading day away). Live Gamma enters "
    "intraday contracts only: a swing has no broker premium stop before T1."
)
_MONITOR_LIVE_MESSAGE = (
    "Live intraday ENTRY is blocked. Select broker or both stop mode "
    "so the broker holds the premium stop."
)
_CUTOFF_MESSAGE = (
    "Gamma-Unwind intraday ENTRY is blocked at or after 15:20 IST. "
    "The reference strategy requires same-day exit."
)


def stale_signal_gate(at_ms: int, now_ms: int | None = None) -> dict | None:
    """Block an E1 row that is no longer a current order ticket."""
    age_ms = (_now_ms() if now_ms is None else now_ms) - int(at_ms or 0)
    if (
        age_ms > ENTRY_SIGNAL_MAX_AGE_S * 1000
        or age_ms < -_ENTRY_CLOCK_SKEW_S * 1000
    ):
        return {
            "code": "signal_stale",
            "reason": (
                "This E1 signal is no longer current "
                f"(older than {ENTRY_SIGNAL_MAX_AGE_S // 60} minutes). "
                "Wait for a fresh ENTRY signal."
            ),
        }
    return None


def hold_gate(cfg: GammaMoveConfig, hold: str, paper: bool | None,
              now: datetime | None = None, *, live_enabled: bool = True) -> dict | None:
    """Why this hold cannot be entered in the current broker mode, if it cannot."""
    now = now or _now()
    if hold == "intraday" and now.time() >= dtime(15, 20):
        return {"code": "intraday_entry_cutoff", "reason": _CUTOFF_MESSAGE}
    if paper is False:
        if not live_enabled:
            return {
                "code": "live_execution_disabled",
                "reason": "Live execution is not enabled for this release.",
            }
        if hold == "swing":
            return {
                "code": "reference_swing_protection_unavailable",
                "reason": _SWING_LIVE_MESSAGE,
            }
        if cfg.stop_mode == "monitor":
            return {
                "code": "live_intraday_broker_protection_required",
                "reason": _MONITOR_LIVE_MESSAGE,
            }
    return None


def entry_gate(cfg: GammaMoveConfig, *, tier: str, dte: int | None, at_ms: int,
               paper: bool | None, live_enabled: bool = True,
               now: datetime | None = None) -> dict:
    """Single source of truth for "can this row be entered right now, and if not
    why not". The board and the alerts read this; arm() enforces the same rules.
    """
    now = now or _now()
    hold = hold_for_dte(dte)
    blocked = None
    if tier != TIER_ENTRY:
        blocked = {
            "code": "tier_alert_only",
            "reason": f"Tier {tier} is alert-only; only 4/4 ENTRY can execute.",
        }
    elif paper is None:
        blocked = {"code": "broker_mode_unknown", "reason": "Broker mode is unknown."}
    else:
        blocked = (
            stale_signal_gate(at_ms, int(now.timestamp() * 1000))
            or hold_gate(cfg, hold, paper, now, live_enabled=live_enabled)
        )
    if blocked is None:
        return {"eligible": True, "code": "", "reason": "", "hold": hold}
    return {"eligible": False, "hold": hold, **blocked}


async def arm(uid: str, signal_id: str) -> dict:
    cfg = get_config(uid)
    if not cfg.enabled:
        return {"ok": False, "message": "Gamma Unwind is switched off"}
    legacy = positions_store.legacy_open_rows(uid)
    if legacy:
        symbols = sorted({
            str((row.get("instrument") or {}).get("tradingsymbol") or "")
            for row in legacy
        })
        return {
            "ok": False,
            "code": "legacy_position_reconciliation_required",
            "message": (
                "retired Gamma Move position state still exists; reconcile broker "
                "exposure before opening Gamma-Unwind risk"
                + (f" ({', '.join(s for s in symbols if s)})" if any(symbols) else "")
            ),
        }

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

        stale = stale_signal_gate(sig.at_ms)
        if stale:
            return {"ok": False, "code": stale["code"], "message": stale["reason"]}

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
        hold = hold_for_dte(sig.contract.dte)
        block = hold_gate(cfg, hold, getattr(client, "_is_paper", None))
        if block:
            return {"ok": False, "code": block["code"], "message": block["reason"]}

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
            _alert_unresolved(uid, pos, what="ENTRY", oid=order_id)
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
                    "error",
                    f"{inst.tradingsymbol}: broker protection not confirmed; "
                    "position remains managed but new entries are halted",
                )
                if resolution.projection_version:
                    mark_projected(resolution)
                # Keep the tick monitor subscribed as the in-process fallback.
                # This does not make the position production-safe across a
                # restart, so the API must not report the entry as successful.
                await _subscribe_watched(uid, session)
                return {
                    "ok": False,
                    "code": "protection_not_confirmed",
                    "status": "MANAGED_UNPROTECTED",
                    "order_id": order_id,
                    "symbol": inst.tradingsymbol,
                    "quantity": pos.quantity,
                    "entry": pos.effective_entry,
                    "hold": pos.hold,
                    "stop": pos.stop or None,
                    "message": (
                        "entry filled, but broker protection could not be confirmed; "
                        "new entries are halted and broker reconciliation is required"
                    ),
                }

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
    old_gtt = int(pos.gtt_id or 0)
    old_stop = float(pos.gtt_at or 0.0)
    if old_gtt and old_stop > 0 and float(pos.stop) < old_stop - 1e-9:
        # A trailing stop only ratchets up. The resting broker trigger is already
        # tighter than the plan; never loosen it.
        log.warning(
            "gamma_unwind: refusing to loosen broker stop for %s (%.2f -> %.2f)",
            pos.instrument.tradingsymbol, old_stop, float(pos.stop),
        )
        return True
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
                    tick_size=float(pos.instrument.tick_size),
                )
                if ok:
                    pos.gtt_at = float(pos.stop)
                    pos.gtt_qty = int(pos.quantity)
                    positions_store.put(uid, pos)
                    from app.services.notifications.telegram_lifecycle import enqueue_best_effort
                    enqueue_best_effort(
                        uid,
                        dedupe_key=(
                            f"gamma:protection:{pos.instrument.tradingsymbol}:"
                            f"{int(pos.gtt_id)}:{float(pos.stop):.2f}:{int(pos.quantity)}"
                        ),
                        strategy="Gamma-Unwind",
                        event_type="TSL SYNCED",
                        title=pos.instrument.tradingsymbol,
                        detail=(
                            f"SL ₹{old_stop:.2f} → ₹{float(pos.stop):.2f} "
                            f"· Qty {int(pos.quantity)} · GTT #{int(pos.gtt_id)}"
                        ),
                    )
                    return True
                if getattr(client, "_is_paper", None) is False:
                    from app.services.notifications.telegram_lifecycle import enqueue_best_effort
                    enqueue_best_effort(
                        uid,
                        dedupe_key=(
                            f"gamma:protection-failed:{pos.instrument.tradingsymbol}:"
                            f"{int(pos.gtt_id)}:{float(pos.stop):.2f}:{int(pos.quantity)}"
                        ),
                        strategy="Gamma-Unwind",
                        event_type="PROTECTION FAILED",
                        title=pos.instrument.tradingsymbol,
                        detail=(
                            f"Broker TSL update not confirmed · Qty {int(pos.quantity)} "
                            f"· required SL ₹{float(pos.stop):.2f} · new entries halted"
                        ),
                        severity="CRITICAL",
                    )
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
                tick_size=float(pos.instrument.tick_size),
            )
        if gid:
            pos.gtt_id = int(gid)
            pos.gtt_at = float(pos.stop)
            pos.gtt_qty = int(pos.quantity)
            positions_store.put(uid, pos)
            from app.services.notifications.telegram_lifecycle import enqueue_best_effort
            enqueue_best_effort(
                uid,
                dedupe_key=(
                    f"gamma:protection:{pos.instrument.tradingsymbol}:"
                    f"{int(pos.gtt_id)}:{float(pos.stop):.2f}:{int(pos.quantity)}"
                ),
                strategy="Gamma-Unwind",
                event_type="SL ARMED",
                title=pos.instrument.tradingsymbol,
                detail=(
                    f"SL ₹{float(pos.stop):.2f} · Qty {int(pos.quantity)} "
                    f"· GTT #{int(pos.gtt_id)}"
                ),
            )
            return True
    except Exception as exc:
        log.warning("gamma_unwind: protection failed for %s: %s",
                    pos.instrument.tradingsymbol, exc)
    if getattr(client, "_is_paper", None) is False:
        from app.services.notifications.telegram_lifecycle import enqueue_best_effort
        enqueue_best_effort(
            uid,
            dedupe_key=(
                f"gamma:protection-failed:{pos.instrument.tradingsymbol}:"
                f"{old_gtt}:{float(pos.stop):.2f}:{int(pos.quantity)}"
            ),
            strategy="Gamma-Unwind",
            event_type="PROTECTION FAILED",
            title=pos.instrument.tradingsymbol,
            detail=(
                f"Broker stop not confirmed · Qty {int(pos.quantity)} "
                f"· required SL ₹{float(pos.stop):.2f} · new entries halted"
            ),
            severity="CRITICAL",
        )
    return False


STOP_RELEASED = "released"
STOP_FIRED = "fired"
STOP_UNKNOWN = "unknown"


async def _release_or_classify_stop(client, pos: PositionState) -> str:
    """Take the resting broker stop out of the way, or say what became of it.

    ``released``: provably cancelled or provably absent; nothing else at the
    broker will sell this position.  ``fired``: the broker trigger already fired,
    so a broker SELL exists.  ``unknown``: cannot be proved either way; treat the
    stop as still armed.
    """
    if not pos.gtt_id:
        return STOP_RELEASED
    if not _position_account_matches(client, pos.account_id):
        return STOP_UNKNOWN
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
        if outcome == pstop.CANCELLED:
            return STOP_RELEASED
        status = await pstop.stop_status(
            client, gid,
            tradingsymbol=pos.instrument.tradingsymbol,
            direction="long",
            exchange=pos.instrument.exchange,
            product=_product(pos),
            qty=int(pos.quantity),
        )
        if status == pstop.STOP_ABSENT:
            return STOP_RELEASED
        if status == pstop.STOP_TRIGGERED:
            return STOP_FIRED
        return STOP_UNKNOWN
    except Exception as exc:
        log.warning("gamma_unwind: protection release failed: %s", exc)
        return STOP_UNKNOWN


async def _cancel_protection(uid: str, client, pos: PositionState) -> bool:
    """True only when no broker stop remains that could sell this position."""
    if await _release_or_classify_stop(client, pos) != STOP_RELEASED:
        return False
    pos.gtt_id = 0
    pos.gtt_at = 0.0
    pos.gtt_qty = 0
    positions_store.put(uid, pos)
    return True


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


def _book_exit_pnl(uid: str, client, pos: PositionState, *, order_id: str,
                   quantity: int, price: float, pnl: float,
                   source: str = "exit") -> str:
    """Feed one confirmed exit into the account daily-loss accounting, once.

    The INR daily-loss breaker reads ``state.daily_realized_pnl_strict``: the
    signed execution ledger (live fills with a recorded cost basis) plus a legacy
    per-day accumulator (positions with no ledger basis, i.e. simulated fills).
    Gamma exits used to reach neither, so a Gamma loss never counted toward the
    cap. Mirrors the SuperTrend monitor's rule: ledger when it holds the cost
    basis, accumulator when it does not, nowhere when the ledger cannot answer.
    """
    oid = str(order_id or "")
    if oid and pos.exit_booked_order_id == oid:
        return BOOK_ALREADY
    symbol = pos.instrument.tradingsymbol
    account_id = str(pos.account_id or getattr(client, "_account_id", "") or "")
    if getattr(client, "_is_paper", None) is False and account_id and oid:
        try:
            from app.services.kite_engine import fill_ledger
            holding = fill_ledger.inventory(account_id, uid, symbol)
        except Exception as exc:
            log.error("gamma_unwind: ledger unreadable for %s: %s", symbol, exc)
            return BOOK_UNKNOWN
        if holding is not None:
            if holding.net_quantity == 0:
                return BOOK_ALREADY
            if holding.net_quantity < 0:
                return BOOK_UNKNOWN
            try:
                applied = fill_ledger.record(
                    account_id=account_id, uid=uid, symbol=symbol,
                    exchange=pos.instrument.exchange, side="SELL",
                    order_id=oid, cumulative_quantity=int(quantity),
                    cumulative_value=float(quantity) * float(price),
                    source=source,
                    lot_size=int(pos.instrument.lot_size or 0),
                )
            except Exception as exc:
                log.error("gamma_unwind: exit not booked to ledger for %s: %s",
                          symbol, exc)
                return BOOK_UNKNOWN
            if applied.reconciliation_required:
                return BOOK_UNKNOWN
            return BOOK_LEDGER if applied.accepted else BOOK_ALREADY
    try:
        from app.services.kite_engine import state as kite_state
        kite_state.record_realized_pnl(uid, float(pnl))
    except Exception as exc:
        log.error("gamma_unwind: realised P&L not booked for %s: %s", symbol, exc)
        return BOOK_UNKNOWN
    return BOOK_LEGACY


def _flag_unbooked_pnl(uid: str, session, pos: PositionState, oid: str) -> None:
    pos.pnl_reconciliation_required = True
    session.note(
        "error",
        f"{pos.instrument.tradingsymbol}: exit {oid} P&L could not be booked "
        "to the daily-loss accounting; reconcile manually",
    )
    from app.services.notifications.telegram_lifecycle import enqueue_best_effort
    enqueue_best_effort(
        uid,
        dedupe_key=f"gamma:pnl-unbooked:{pos.signal_id}:{oid}",
        strategy="Gamma-Unwind",
        event_type="PNL UNRECONCILED",
        title=pos.instrument.tradingsymbol,
        detail=(
            f"Exit {oid} realised P&L not booked to the daily-loss cap · "
            f"Qty {int(pos.quantity)} · reconcile with the broker"
        ),
        severity="CRITICAL",
    )


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
            _alert_unresolved(uid, pos, what="EXIT", oid=oid)
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
    booking = _book_exit_pnl(
        uid, client, pos, order_id=oid, quantity=sold, price=fill, pnl=pnl,
    )
    if booking == BOOK_UNKNOWN:
        _flag_unbooked_pnl(uid, session, pos, oid)
    else:
        pos.exit_booked_order_id = oid

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
        from app.services.notifications.telegram_lifecycle import enqueue_best_effort
        enqueue_best_effort(
            uid,
            dedupe_key=f"gamma:partial-exit:{oid}:{sold}:{fill:.2f}",
            strategy="Gamma-Unwind",
            event_type="PARTIAL EXIT",
            title=pos.instrument.tradingsymbol,
            detail=(
                f"Sold {sold}/{requested} @ ₹{fill:.2f} · "
                f"{int(pos.quantity)} remains · {reason}"
            ),
            severity="WARNING",
        )
        return False, "partial_exit"

    positions_store.close(
        uid,
        pos.instrument.tradingsymbol,
        reason,
        exit_price=fill,
        quantity_closed=sold,
        pnl_inr=float(pos.partial_realised_inr) + float(pnl),
    )
    session.record.record(
        pnl,
        _today_str(),
        outcome_pnl=float(pos.partial_realised_inr) + float(pnl),
    )
    positions_store.save_record(uid, session.record)
    session.note(
        "exit",
        f"{pos.instrument.tradingsymbol}: {reason} @ {fill:g}",
    )
    return True, "closed"


async def _execute_exit(uid: str, pos: PositionState, *,
                        market_ltp: float, reason: str,
                        grade_price: float | None = None) -> tuple[bool, str]:
    """Serialise exits per position and back off after a failed attempt.

    Tick batches, the 15:20 sweep and reconcile can all reach this at once. The
    broker awaits inside the body are interleaving points, so without a lock two
    callers both pass the ``pos.exiting`` check and send two SELLs (naked short).
    """
    key = (uid, pos.instrument.tradingsymbol)
    if not pos.is_open:
        return False, "position_not_open"
    if _exit_retry_at.get(key, 0.0) > time.monotonic():
        return False, "exit_retry_backoff"
    lock = _exit_locks.setdefault(key, asyncio.Lock())
    if lock.locked():
        return False, "exit_in_progress"
    async with lock:
        if not pos.is_open:
            return False, "position_not_open"
        ok, status = await _execute_exit_locked(
            uid, pos, market_ltp=market_ltp, reason=reason,
            grade_price=grade_price,
        )
        if ok or status in ("partial_exit", "closed"):
            _exit_retry_at.pop(key, None)
        else:
            _exit_retry_at[key] = time.monotonic() + _EXIT_RETRY_COOLDOWN_S
        return ok, status


async def _execute_exit_locked(uid: str, pos: PositionState, *,
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
    if not await _cancel_protection(uid, client, pos):
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
            # TSL only ratchets up: never loosen a tighter structural stop.
            pos.stop = max(float(pos.stop or 0.0), float(pos.effective_entry))
            pos.high_water = max(pos.high_water, ltp)
            positions_store.put(uid, pos)
            session_for(uid).note(
                "T1",
                f"{pos.instrument.tradingsymbol}: T1 hit; reference says "
                "book part (fraction unspecified), SL moved to entry",
            )
            from app.services.notifications.telegram_lifecycle import enqueue_best_effort
            enqueue_best_effort(
                uid,
                dedupe_key=(
                    f"gamma:t1:{pos.signal_id}:{float(pos.stop):.2f}"
                ),
                strategy="Gamma-Unwind",
                event_type="T1 / BREAKEVEN",
                title=pos.instrument.tradingsymbol,
                detail=(
                    f"T1 ₹{float(pos.t1):.2f} reached · TSL moved to "
                    f"entry ₹{float(pos.effective_entry):.2f} · T2 ₹{float(pos.t2):.2f}"
                ),
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

    if pos.trade_state == "T1_TRAIL":
        trail = max(float(pos.stop or 0.0), float(pos.effective_entry))
        if ltp <= trail + eps:
            ok, status = await _execute_exit(
                uid, pos, market_ltp=ltp,
                reason="TRAIL", grade_price=trail,
            )
            return "exited" if ok else status

    return "watching"


async def on_ticks(uid: str, ticks: list) -> str:
    from app.services import gamma_move_scanner as scanner
    signals: list[GammaSignal] = []
    if bool(get_config(uid).enabled):
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
        _position_ltp[(uid, pos.instrument.tradingsymbol)] = ltp
        _position_ltp_ms[(uid, pos.instrument.tradingsymbol)] = _now_ms()
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

    # Catch up intraday holdings from a prior trading day immediately. A paper
    # process can be offline at 15:20 and restart next morning; waiting for the
    # new day's 15:20 would incorrectly expose that stale same-day position to a
    # second session's ticks.
    for pos in list(positions_store.open_positions(uid)):
        if pos.hold != "intraday" or pos.exiting:
            continue
        try:
            entry_day = date.fromisoformat(pos.entry_day[:10])
        except (TypeError, ValueError):
            entry_day = today
        if entry_day >= today:
            continue
        ltp = await _quote_ltp(client, pos)
        if ltp <= 0:
            continue
        await _execute_exit(
            uid,
            pos,
            market_ltp=ltp,
            reason="EOD",
            grade_price=ltp,
        )

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
    """Return latest due slot; supersede older slots, but do not pre-claim it."""
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
    # Earlier missed slots are deliberately superseded by the latest due slot.
    # The latest itself remains retryable until the scheduled operation succeeds.
    for _target, raw in due[:-1]:
        _schedule_done.add((uid, day, f"{kind}:{raw}"))
    return due[-1][1]


def _complete_slot(uid: str, now: datetime, kind: str, raw: str | None) -> None:
    if raw:
        _schedule_done.add((uid, now.date().isoformat(), f"{kind}:{raw}"))


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

            # Broker truth is not a startup-only concern. Resolve acknowledged
            # entries/exits, manual broker closes and protection drift every cycle.
            rec = await reconcile(uid)
            if rec.get("error"):
                out.setdefault(uid, {})["reconcile_error"] = rec["error"]

            level_slot = (
                _claim_latest_due_slot(
                    uid, now, "levels", cfg.mark_levels_times,
                )
                if cfg.enabled else None
            )
            build_slot = (
                _claim_latest_due_slot(
                    uid,
                    now,
                    "watchlist",
                    cfg.build_watchlists_times,
                    not_after=dtime(15, 20),
                )
                if cfg.enabled else None
            )

            if level_slot is not None:
                out.setdefault(uid, {})["levels"] = await scanner.mark_levels(
                    uid, cfg, today=now.date()
                )
                _complete_slot(uid, now, "levels", level_slot)

            recover_missing = (
                cfg.enabled
                and not scanner.has_watchers(uid)
                and not scanner.completed_scan_for_day(uid, now.date())
                and dtime(9, 20) <= now.time() <= dtime(15, 20)
            )
            if build_slot is not None or recover_missing:
                built = await scan_once(uid, scheduled_slot=build_slot)
                if uid in out and isinstance(out[uid], dict):
                    out[uid]["watchlist"] = built
                else:
                    out[uid] = built
                if build_slot is not None:
                    _complete_slot(uid, now, "watchlist", build_slot)

            if (
                is_paper(uid) is True
                and (scanner.has_watchers(uid) or positions_store.open_positions(uid))
            ):
                ticks = await _paper_poll_ticks(uid)
                if ticks:
                    await on_ticks(uid, ticks)

            # Position maintenance stays alive even while the signal engine is OFF.
            await _reference_time_sweeps(uid, now)
            await _subscribe_watched(uid, session)
        except Exception as exc:
            log.warning("gamma_unwind cycle failed for %s: %s", uid, exc)
            out[uid] = {"error": str(exc)}
    return out


async def auto_scan_loop(interval: int = 300) -> None:
    del interval
    log.info(
        "gamma_unwind scheduler started: reference schedule + E1 polling"
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
            "ltp": _position_ltp.get((uid, pos.instrument.tradingsymbol)),
            "ltp_age_s": (
                None
                if (uid, pos.instrument.tradingsymbol) not in _position_ltp_ms
                else round(max(0.0, (_now_ms() - _position_ltp_ms[
                    (uid, pos.instrument.tradingsymbol)
                ]) / 1000.0), 1)
            ),
            "partial_realised_inr": round(float(pos.partial_realised_inr), 2),
            "exiting": bool(pos.exiting),
            "pnl_reconciliation_required": bool(pos.pnl_reconciliation_required),
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


def _order_ms(order: dict) -> int:
    """Order timestamp in epoch ms; naive Kite stamps are IST wall-clock."""
    for key in ("exchange_timestamp", "order_timestamp"):
        raw = order.get(key)
        if raw in (None, ""):
            continue
        try:
            when = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw))
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=IST)
        return int(when.timestamp() * 1000)
    return 0


async def _find_triggered_exit_fills(client, pos: PositionState) -> Optional[list[tuple[str, int, float]]]:
    """Locate the completed broker SELL(s) that closed ``pos`` after its stop fired.

    Returns ``[(order_id, quantity, average_price), ...]`` summing to exactly the
    durable quantity, or None when the fill cannot be attributed with certainty
    (unreadable order book, no match, or an ambiguous quantity). Never guesses.
    """
    try:
        orders = await client.get_orders()
    except Exception as exc:
        log.warning("gamma_unwind: order book unreadable for %s: %s",
                    pos.instrument.tradingsymbol, exc)
        return None
    if not isinstance(orders, list):
        return None
    symbol = pos.instrument.tradingsymbol.upper()
    exchange = pos.instrument.exchange.upper()
    product = _product(pos)
    own_exit = str(pos.exit_order_id or "")
    rows: list[tuple[int, str, int, float]] = []
    for order in orders:
        if not isinstance(order, dict):
            continue
        if str(order.get("tradingsymbol") or "").upper() != symbol:
            continue
        if str(order.get("exchange") or "").upper() != exchange:
            continue
        if str(order.get("product") or "").upper() != product:
            continue
        if str(order.get("transaction_type") or "").upper() != "SELL":
            continue
        if str(order.get("status") or "").upper() != "COMPLETE":
            continue
        try:
            filled = int(float(order.get("filled_quantity") or 0))
            average = float(order.get("average_price") or 0.0)
        except (TypeError, ValueError):
            continue
        oid = str(order.get("order_id") or "")
        if filled <= 0 or average <= 0 or not oid or oid == own_exit:
            continue
        stamp = _order_ms(order)
        if stamp and pos.entered_ms and stamp < int(pos.entered_ms) - 60_000:
            continue  # a SELL from before this position existed
        rows.append((stamp, oid, filled, average))
    rows.sort(reverse=True)  # newest first
    need = int(pos.quantity)
    taken: list[tuple[str, int, float]] = []
    got = 0
    for _stamp, oid, filled, average in rows:
        if got >= need:
            break
        if got + filled > need:
            return None  # ambiguous: would over-attribute
        taken.append((oid, filled, average))
        got += filled
    return taken if got == need and taken else None


async def _settle_broker_triggered_exit(uid: str, client, pos: PositionState) -> bool:
    """Close a position whose broker stop already fired and sold it.

    True when the position was closed (fill located and booked, or the bounded
    attempt budget ran out and it was closed flagged for manual reconciliation).
    False while the fill is still being located; the position stays managed but
    cannot send a second SELL because broker inventory is zero.
    """
    key = (uid, pos.instrument.tradingsymbol)
    session = session_for(uid)
    fills = await _find_triggered_exit_fills(client, pos)
    reason = "TRAIL" if pos.trade_state == "T1_TRAIL" else "SL"
    if fills is None:
        attempts = _triggered_fill_attempts.get(key, 0) + 1
        _triggered_fill_attempts[key] = attempts
        if attempts < _TRIGGERED_FILL_MAX_ATTEMPTS:
            if attempts == 1:
                session.note(
                    "warning",
                    f"{pos.instrument.tradingsymbol}: broker stop fired; "
                    "locating the exit fill",
                )
            return False
        _triggered_fill_attempts.pop(key, None)
        pos.pnl_reconciliation_required = True
        positions_store.close(
            uid, pos.instrument.tradingsymbol,
            f"{reason} (broker stop; fill unconfirmed)",
        )
        session.note(
            "error",
            f"{pos.instrument.tradingsymbol}: broker stop fired but the exit "
            "fill could not be attributed; P&L needs manual reconciliation",
        )
        from app.services.notifications.telegram_lifecycle import enqueue_best_effort
        enqueue_best_effort(
            uid,
            dedupe_key=f"gamma:pnl-unbooked:{pos.signal_id}:broker-stop",
            strategy="Gamma-Unwind",
            event_type="PNL UNRECONCILED",
            title=pos.instrument.tradingsymbol,
            detail=(
                f"Broker stop exit fill not found · Qty {int(pos.quantity)} · "
                "reconcile P&L with the broker"
            ),
            severity="CRITICAL",
        )
        return True

    _triggered_fill_attempts.pop(key, None)
    quantity = sum(q for _o, q, _p in fills)
    value = sum(q * p for _o, q, p in fills)
    average = value / quantity
    pnl_total = 0.0
    unbooked = False
    for oid, qty, price in fills:
        leg = realised_inr(pos, price, qty)
        pnl_total += leg
        if _book_exit_pnl(
            uid, client, pos, order_id=oid, quantity=qty, price=price,
            pnl=leg, source="gtt",
        ) == BOOK_UNKNOWN:
            unbooked = True
        else:
            pos.exit_booked_order_id = oid
    if unbooked:
        _flag_unbooked_pnl(uid, session, pos, fills[0][0])
    pos.exit_order_id = fills[0][0]
    outcome_pnl = float(pos.partial_realised_inr) + pnl_total
    positions_store.close(
        uid, pos.instrument.tradingsymbol, reason,
        exit_price=average, quantity_closed=quantity, pnl_inr=outcome_pnl,
    )
    session.record.record(pnl_total, _today_str(), outcome_pnl=outcome_pnl)
    positions_store.save_record(uid, session.record)
    session.note(
        "exit",
        f"{pos.instrument.tradingsymbol}: broker {reason} @ {average:g}",
    )
    return True


def _protection_due(uid: str, pos: PositionState) -> bool:
    """Whether reconcile should touch the broker stop for this position now.

    Always when it is missing, resized or moved; otherwise at most once per
    ``_PROTECTION_RECHECK_S`` (drift detection without a GTT write every pass).
    """
    key = (uid, pos.instrument.tradingsymbol)
    synced = bool(
        pos.gtt_id
        and int(pos.gtt_qty or 0) == int(pos.quantity)
        and abs(float(pos.gtt_at or 0.0) - float(pos.stop)) < 0.005
    )
    now = time.monotonic()
    if synced and now - _protection_checked_at.get(key, 0.0) < _PROTECTION_RECHECK_S:
        return False
    _protection_checked_at[key] = now
    return True


def _alert_unresolved(uid: str, pos: PositionState, *, what: str, oid: str) -> None:
    """CRITICAL lifecycle alert for an order whose outcome is not yet known."""
    from app.services.notifications.telegram_lifecycle import enqueue_best_effort
    enqueue_best_effort(
        uid,
        dedupe_key=f"gamma:{what.lower()}-unresolved:{pos.signal_id}:{oid}",
        strategy="Gamma-Unwind",
        event_type=f"{what} UNRESOLVED",
        title=pos.instrument.tradingsymbol,
        detail=(
            f"Order {oid} outcome unknown · Qty {int(pos.quantity)} · "
            "no retry sent · reconcile with the broker · new entries halted"
        ),
        severity="CRITICAL",
    )


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
                if pos.gtt_id:
                    # Broker inventory is zero while a stop is still on file.
                    # Either the stop FIRED (the normal live stop-out: book the
                    # fill) or the position was closed by hand and the stop must
                    # be removed before it can SELL into zero inventory.
                    stop = await _release_or_classify_stop(client, pos)
                    if stop == STOP_FIRED:
                        await _settle_broker_triggered_exit(uid, client, pos)
                        continue
                    if stop != STOP_RELEASED:
                        session.note(
                            "warning",
                            f"{pos.instrument.tradingsymbol}: broker inventory is zero "
                            "but protective GTT could not be cancelled/verified absent",
                        )
                        try:
                            from app.services import live_safety
                            live_safety.set_kill_switch(
                                True,
                                f"Gamma-Unwind orphan protection unresolved for "
                                f"{pos.instrument.tradingsymbol}",
                            )
                        except Exception:
                            pass
                        continue
                    pos.gtt_id = 0
                    pos.gtt_at = 0.0
                    pos.gtt_qty = 0
                    positions_store.put(uid, pos)
                vanished.append(pos.instrument.tradingsymbol)
                positions_store.close(
                    uid, pos.instrument.tradingsymbol,
                    "not at broker on reconcile",
                )
                continue

            if (
                broker_qty is not None
                and broker_qty > 0
                and pos.hold == "intraday"
                and pos.stop_mode == "monitor"
            ):
                session.note(
                    "warning",
                    f"{pos.instrument.tradingsymbol}: live intraday broker inventory "
                    "has no broker stop; new entries are halted until the position "
                    "is closed or protection is corrected",
                )
                try:
                    from app.services import live_safety
                    live_safety.set_kill_switch(
                        True,
                        f"Gamma-Unwind live intraday position has monitor-only protection: "
                        f"{pos.instrument.tradingsymbol}",
                    )
                except Exception:
                    pass

            if broker_qty is None or broker_qty < int(pos.quantity):
                session.note(
                    "warning",
                    f"{pos.instrument.tradingsymbol}: broker quantity "
                    "does not match durable Gamma-Unwind state",
                )
                continue

        if pos.stop > 0 and pos.stop_mode != "monitor" and _protection_due(uid, pos):
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

    if getattr(client, "_is_paper", None) is False:
        broker_qty = await _broker_long_quantity(client, pos)
        if broker_qty is None or broker_qty < int(pos.quantity):
            return {
                "ok": False,
                "code": "broker_inventory_unverified",
                "message": (
                    f"adoption refused: broker {_product(pos)} inventory for "
                    f"{pos.instrument.tradingsymbol} is "
                    f"{'unreadable' if broker_qty is None else broker_qty}; "
                    f"need at least {pos.quantity}"
                ),
            }

    if getattr(client, "_is_paper", None) is False and pos.hold == "swing":
        return {
            "ok": False,
            "code": "reference_swing_protection_unavailable",
            "message": (
                "Live swing adoption is blocked. The D68 pre-T1 stop cannot be "
                "represented as a broker premium stop."
            ),
        }

    if (
        getattr(client, "_is_paper", None) is False
        and pos.hold == "intraday"
        and pos.stop_mode == "monitor"
    ):
        return {
            "ok": False,
            "code": "live_intraday_broker_protection_required",
            "message": (
                "Live intraday adoption is blocked. Select broker or both stop mode "
                "before Sterling assumes management."
            ),
        }

    positions_store.put(uid, pos)
    if pos.hold == "intraday":
        protected = await _ensure_protection(uid, client, pos)
        if (getattr(client, "_is_paper", None) is False
                and pos.stop_mode != "monitor" and not protected):
            try:
                from app.services import live_safety
                live_safety.set_kill_switch(
                    True,
                    f"Gamma-Unwind adopted position unprotected for "
                    f"{pos.instrument.tradingsymbol}",
                )
            except Exception:
                pass
            return {
                "ok": False,
                "code": "protection_not_confirmed",
                "message": (
                    "broker inventory was adopted, but protective stop could not "
                    "be confirmed; position remains managed and live entries are halted"
                ),
            }
    await _subscribe_watched(uid, session)
    return {
        "ok": True,
        "symbol": symbol,
        "hold": pos.hold,
        "t1": pos.t1,
        "t2": pos.t2,
        "stop": pos.stop or None,
    }
