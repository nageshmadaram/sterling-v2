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
import uuid
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

# An exit SELL is persisted with this placeholder id (and a unique tag) before it
# leaves the process. The placeholder means "may exist at the broker, find it by
# its tag"; it is never sent anywhere and never booked.
EXIT_SUBMITTING = "submitting"
# The one tag every exit carried before per-exit tags: resolves a legacy row.
LEGACY_EXIT_TAG = "gamma-unwind-exit"
# A SELL whose reply was lost can still be accepted by the broker for a moment.
_EXIT_IN_FLIGHT_GRACE_MS = 30_000

# Outcomes of feeding an exit into the account daily-loss accounting.
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

    try:
        unreconciled = positions_store.pnl_unreconciled(uid, _today_str())
    except Exception as exc:
        return {
            "ok": False,
            "code": "gamma_registry_unreadable",
            "message": f"Gamma position registry could not be read: {exc}",
        }
    if unreconciled:
        return {
            "ok": False,
            "code": "pnl_reconciliation_required",
            "message": (
                "realised P&L of "
                + ", ".join(unreconciled)
                + " is not reconciled with the broker; the daily-loss figure may "
                "be understated, so new Gamma entries are held until it is"
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
    if pos.gtt_pending and not int(pos.gtt_id or 0):
        # An earlier placement may have landed at the broker without our hearing
        # back. Never place again until the broker's trigger book says otherwise.
        resolved = await _resolve_pending_gtt(uid, client, pos)
        if resolved == GTT_PENDING:
            return False
        if resolved == GTT_ADOPTED and _adopted_trigger_is_current(pos):
            return True
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
        from app.services.kite_engine.protective_stop import (
            place_stop_outcome, move_stop,
        )
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

            outcome = await place_stop_outcome(
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
                before_submit=lambda: _latch_gtt_pending(uid, pos),
                on_refused=lambda: _lift_gtt_pending(uid, pos),
            )
        gid = outcome.trigger_id
        if outcome.ambiguous:
            _alert_protection_uncertain(uid, pos, outcome.reason)
        if gid:
            pos.gtt_id = int(gid)
            pos.gtt_at = float(pos.stop)
            pos.gtt_qty = int(pos.quantity)
            _clear_gtt_pending_fields(pos)
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


# ---------------------------------------------------------------------------
# GTT placement fence. A placement whose reply was lost (a timeout, a 5xx, a
# crash before the registry write) may have created a trigger. The fence is
# durable before the POST and is resolved only against the broker's trigger book.
# ---------------------------------------------------------------------------
GTT_ADOPTED = "adopted"
GTT_CLEARED = "cleared"
GTT_PENDING = "pending"
# A POST that timed out can still be created by the broker a few seconds later,
# so an empty trigger book is not proof until this long after the send.
_GTT_IN_FLIGHT_GRACE_MS = 30_000
# The broker's clock against ours: a trigger stamped this far before the latch (or less)
# can still be the one this placement created.
_GTT_CLOCK_SKEW_MS = 60_000


def _clear_gtt_pending_fields(pos: PositionState) -> None:
    pos.gtt_pending = False
    pos.gtt_pending_ms = 0
    pos.gtt_pending_qty = 0
    pos.gtt_pending_trigger = 0.0


def _latch_gtt_pending(uid: str, pos: PositionState) -> None:
    """Make "a trigger may exist" durable BEFORE the GTT leaves this process."""
    pos.gtt_pending = True
    pos.gtt_pending_ms = _now_ms()
    pos.gtt_pending_qty = int(pos.quantity)
    pos.gtt_pending_trigger = float(pos.stop)
    try:
        positions_store.put(uid, pos)
    except Exception:
        _clear_gtt_pending_fields(pos)
        raise


def _lift_gtt_pending(uid: str, pos: PositionState) -> None:
    """The broker provably created nothing: the fence must not outlive that."""
    _clear_gtt_pending_fields(pos)
    positions_store.put(uid, pos)


def _alert_protection_uncertain(uid: str, pos: PositionState, reason: str) -> None:
    if getattr(pos, "gtt_pending", False) is not True:
        return
    from app.services.notifications.telegram_lifecycle import enqueue_best_effort
    enqueue_best_effort(
        uid,
        dedupe_key=(
            f"gamma:protection-uncertain:{pos.instrument.tradingsymbol}:"
            f"{int(pos.gtt_pending_ms or 0)}"
        ),
        strategy="Gamma-Unwind",
        event_type="PROTECTION UNCERTAIN",
        title=pos.instrument.tradingsymbol,
        detail=(
            f"Broker stop placement not confirmed · Qty {int(pos.quantity)} · "
            f"SL ₹{float(pos.stop):.2f} · no second trigger sent until the broker "
            "book is read · new entries halted"
        ),
        severity="CRITICAL",
    )


def _adopted_trigger_is_current(pos: PositionState) -> bool:
    """An adopted trigger that already matches the plan needs no modify."""
    return (
        int(pos.gtt_qty or 0) == int(pos.quantity)
        and float(pos.gtt_at or 0.0) >= float(pos.stop) - 0.005
    )


class _GttBookSnapshot:
    """One read of the broker's trigger book, served back to the shared matcher."""

    def __init__(self, rows: list) -> None:
        self._rows = rows

    async def get_gtts(self) -> list:
        return self._rows


def _trigger_value(row: dict) -> float:
    cond = row.get("condition") if isinstance(row.get("condition"), dict) else {}
    values = cond.get("trigger_values")
    try:
        return float(values[0]) if isinstance(values, (list, tuple)) and values else 0.0
    except (TypeError, ValueError):
        return 0.0


async def _resolve_pending_gtt(uid: str, client, pos: PositionState) -> str:
    """Settle ``gtt_pending`` against the broker's trigger book.

    ``adopted``: a trigger of exactly this exit (exchange, symbol, product, SELL,
    quantity, trigger) that belongs to THIS placement's window exists and is now this
    position's. ``cleared``: the book was read, holds nothing of the kind, and the
    in-flight window has passed, so a fresh placement is safe. ``pending``: anything
    else; the fence stays.

    Gamma's stop is the structural base low, so a same-day re-entry into the same
    contract repeats the same quantity and trigger value: an earlier stop-out's fired
    row matches every field and is still listed. A row is therefore only ours when it
    does not predate the latch (``created_at``, else ``updated_at``, against
    ``gtt_pending_ms`` less clock skew), and a FIRED row is ours only when it is shown to
    postdate it - no timestamp means it cannot be attributed, so the fence holds. When
    several rows qualify an ACTIVE one is kept over a fired one, the newest id over an
    older one, and an ACTIVE row is never cancelled in favour of a fired one.
    """
    from app.services.kite_engine import protective_stop as pstop

    try:
        rows = await client.get_gtts()
    except Exception as exc:
        log.warning("gamma_unwind: GTT book unreadable for %s: %s",
                    pos.instrument.tradingsymbol, exc)
        return GTT_PENDING
    if not isinstance(rows, list):
        return GTT_PENDING
    found = await pstop.resting_sell_triggers(
        _GttBookSnapshot(rows),
        tradingsymbol=pos.instrument.tradingsymbol,
        exchange=pos.instrument.exchange,
        product=_product(pos),
        direction="long",
    )
    if found is None:
        return GTT_PENDING   # a row for this contract cannot be attributed
    from app.services.external_positions import owned_gtt_ids

    claimed_ids = owned_gtt_ids(str(pos.account_id or ""))
    if claimed_ids is None:
        # Local ownership cannot be established. A field-identical trigger may
        # protect SuperTrend/Snapback or another Gamma position; never guess.
        return GTT_PENDING

    want_qty = int(pos.gtt_pending_qty or pos.quantity)
    want_trigger = float(pos.gtt_pending_trigger or pos.stop)
    tolerance = float(pos.instrument.tick_size or 0.05) / 2 + 1e-6
    by_id = {
        int(r.get("id") or r.get("trigger_id") or 0): r
        for r in rows if isinstance(r, dict)
    }
    latch_ms = int(pos.gtt_pending_ms or 0)
    matching = [
        f for f in found
        if str(int(f["id"])) not in claimed_ids
        and f["verdict"] in (pstop.STOP_ACTIVE, pstop.STOP_TRIGGERED)
        and int(f["qty"]) == want_qty
        and abs(_trigger_value(by_id.get(int(f["id"]), {})) - want_trigger) <= tolerance
    ]
    ours: list = []
    unattributable: list = []
    for f in matching:
        row = by_id.get(int(f["id"]), {})
        stamp = (pstop.broker_ts_ms(row.get("created_at"))
                 or pstop.broker_ts_ms(row.get("updated_at")))
        if latch_ms and stamp and stamp < latch_ms - _GTT_CLOCK_SKEW_MS:
            continue  # an earlier round trip's trigger: it predates this placement
        if f["verdict"] == pstop.STOP_TRIGGERED and not (latch_ms and stamp):
            unattributable.append(f)  # fired, and nothing shows it is this placement's
            continue
        ours.append(f)
    unclear = [f for f in found if f["verdict"] == pstop.STOP_UNVERIFIED
               and int(f["qty"]) == want_qty]
    if unclear:
        return GTT_PENDING
    if not ours and unattributable:
        return GTT_PENDING
    if ours:
        ours.sort(key=lambda f: (0 if f["verdict"] == pstop.STOP_ACTIVE else 1, -int(f["id"])))
        keep = ours[0]
        # Only duplicates of a live stop are cancelled; a fired row is not a stop to remove.
        extras = ([f for f in ours[1:] if f["verdict"] == pstop.STOP_ACTIVE]
                  if keep["verdict"] == pstop.STOP_ACTIVE else [])
        pos.gtt_id = int(keep["id"])
        pos.gtt_at = _trigger_value(by_id.get(int(keep["id"]), {})) or want_trigger
        pos.gtt_qty = int(keep["qty"])
        _clear_gtt_pending_fields(pos)
        positions_store.put(uid, pos)
        session_for(uid).note(
            "warning",
            f"{pos.instrument.tradingsymbol}: unconfirmed broker stop found "
            f"(GTT #{pos.gtt_id}); adopted, no second trigger placed",
        )
        await _cancel_duplicate_triggers(uid, client, pos, [int(f["id"]) for f in extras])
        return GTT_ADOPTED
    age_ms = _now_ms() - int(pos.gtt_pending_ms or 0)
    if int(pos.gtt_pending_ms or 0) and age_ms < _GTT_IN_FLIGHT_GRACE_MS:
        return GTT_PENDING
    _clear_gtt_pending_fields(pos)
    positions_store.put(uid, pos)
    return GTT_CLEARED


async def _cancel_duplicate_triggers(uid: str, client, pos: PositionState,
                                     ids: list[int]) -> None:
    """Two identical SELL triggers sell twice: keep one, remove the rest."""
    if not ids:
        return
    from app.services.kite_engine import protective_stop as pstop
    from app.services.snapback_family_mode import (
        INTENT_REDUCE, canonical_broker_capability,
    )
    failed: list[int] = []
    for gid in ids:
        with canonical_broker_capability(
            f"gamma-unwind-dedupe:{getattr(client, '_account_id', '') or 'paper'}:"
            f"{pos.instrument.tradingsymbol}:{gid}",
            intent=INTENT_REDUCE,
        ):
            outcome = await pstop.cancel_stop_result(client, gid)
        if outcome != pstop.CANCELLED:
            failed.append(gid)
    if failed:
        from app.services.notifications.telegram_lifecycle import enqueue_best_effort
        enqueue_best_effort(
            uid,
            dedupe_key=f"gamma:duplicate-stop:{pos.instrument.tradingsymbol}:{','.join(map(str, failed))}",
            strategy="Gamma-Unwind",
            event_type="PROTECTION FAILED",
            title=pos.instrument.tradingsymbol,
            detail=(
                f"Duplicate broker stop(s) {', '.join('#' + str(g) for g in failed)} "
                "could not be cancelled · delete them in Kite before the stop fires"
            ),
            severity="CRITICAL",
        )


STOP_RELEASED = "released"
STOP_FIRED = "fired"
STOP_UNKNOWN = "unknown"


async def _release_or_classify_stop(client, pos: PositionState, uid: str = "", *,
                                    forced: bool = False) -> str:
    """Take the resting broker stop out of the way, or say what became of it.

    ``released``: provably cancelled or provably absent; nothing else at the
    broker will sell this position.  ``fired``: the broker trigger already fired,
    so a broker SELL exists.  ``unknown``: cannot be proved either way; treat the
    stop as still armed.

    A trigger that fired but whose LIMIT child rests unfilled (a gap) is consumed:
    nothing at the broker will sell the position. Once the child is stale
    (``forced`` skips the grace window, for an operator flatten) it is cancelled
    and confirmed terminal, any part-fill is booked, and the stop reads
    ``released`` so the caller sells only what the broker still holds.
    """
    if not pos.gtt_id and pos.gtt_pending:
        # A placement whose outcome is unknown: the trigger may rest at the broker
        # with no id on file. Settle that against the trigger book before any SELL.
        if not uid or not _position_account_matches(client, pos.account_id):
            return STOP_UNKNOWN
        try:
            resolved = await _resolve_pending_gtt(uid, client, pos)
        except Exception as exc:
            log.warning("gamma_unwind: pending protection unresolved: %s", exc)
            return STOP_UNKNOWN
        if resolved == GTT_PENDING:
            return STOP_UNKNOWN
        if resolved == GTT_CLEARED:
            return STOP_RELEASED
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
        if status == pstop.STOP_TRIGGERED_STALE or (
                forced and status == pstop.STOP_TRIGGERED_RESTING):
            return await _clear_fired_stop_child(uid, client, pos, forced=forced)
        return STOP_UNKNOWN
    except Exception as exc:
        log.warning("gamma_unwind: protection release failed: %s", exc)
        return STOP_UNKNOWN


async def _book_stop_child_fill(uid: str, client, pos: PositionState, done) -> bool:
    """Book what a cancelled stop-child filled, and shrink the position to match.

    The fill is real inventory that left the account: selling the registry's figure
    afterwards would sell more than the broker holds. False when it cannot be
    consumed safely (the caller then holds the exit).
    """
    filled = int(done.filled_quantity)
    oid = str(done.order_id or "")
    if filled <= 0 or (oid and pos.exit_booked_order_id == oid):
        return True
    session = session_for(uid)
    if filled >= int(pos.quantity):
        _flag_pnl(pos)
        positions_store.put(uid, pos)
        session.note(
            "error",
            f"{pos.instrument.tradingsymbol}: stop order {oid} reports {filled} filled against "
            f"{int(pos.quantity)} held; reconcile with the broker before any exit",
        )
        return False
    price = float(done.average_price or 0.0)
    pnl = realised_inr(pos, price, filled) if price > 0 else 0.0
    booking = (_book_exit_pnl(uid, client, pos, order_id=oid, quantity=filled, price=price,
                              pnl=pnl, source="gtt")
               if price > 0 else BOOK_UNKNOWN)
    if booking == BOOK_UNKNOWN:
        _flag_unbooked_pnl(uid, session, pos, oid)
    else:
        pos.exit_booked_order_id = oid
    pos.quantity = int(pos.quantity) - filled
    lot = max(1, int(pos.instrument.lot_size or 1))
    pos.lots = max(1, (pos.quantity + lot - 1) // lot)
    pos.partial_realised_inr += pnl
    try:
        positions_store.put(uid, pos)
        if booking != BOOK_UNKNOWN:
            session.record.add_realised(pnl, _today_str())
            positions_store.save_record(uid, session.record)
    except Exception as exc:
        log.error("gamma_unwind: stop-child part-fill not persisted for %s: %s",
                  pos.instrument.tradingsymbol, exc)
        return False
    session.note(
        "warning",
        f"{pos.instrument.tradingsymbol}: stop order {oid} filled {filled} before it was "
        f"cancelled; {int(pos.quantity)} remains",
    )
    return True


async def _clear_fired_stop_child(uid: str, client, pos: PositionState, *,
                                  forced: bool) -> str:
    """A fired stop's LIMIT child rests unfilled: cancel it, confirm, book its part-fill.

    ``released`` once the child is provably dead (sell what the broker still holds),
    ``fired`` when it filled first (its SELL is the exit), otherwise ``unknown``.
    """
    from app.services.kite_engine import protective_stop as pstop

    fired = await pstop.stop_state(
        client, int(pos.gtt_id),
        tradingsymbol=pos.instrument.tradingsymbol,
        direction="long",
        exchange=pos.instrument.exchange,
        product=_product(pos),
        qty=int(pos.quantity),
    )
    verdict = fired.verdict
    if (verdict == pstop.STOP_TRIGGERED_RESTING and forced
            and fired.child_state == pstop.CHILD_RESTING and fired.child_order_id):
        verdict = pstop.STOP_TRIGGERED_STALE
    if verdict == pstop.STOP_ABSENT:
        return STOP_RELEASED
    if verdict == pstop.STOP_TRIGGERED:
        return STOP_FIRED
    if verdict != pstop.STOP_TRIGGERED_STALE:
        return STOP_UNKNOWN
    done = await pstop.cancel_fired_child(
        client, fired, tradingsymbol=pos.instrument.tradingsymbol)
    if done.outcome == pstop.CANCEL_UNCONFIRMED:
        from app.services.notifications.telegram_lifecycle import enqueue_best_effort
        enqueue_best_effort(
            uid,
            dedupe_key=f"gamma:stop-child-uncancelled:{pos.signal_id}:{int(pos.gtt_id)}",
            strategy="Gamma-Unwind",
            event_type="STOP ORDER NOT CANCELLED",
            title=pos.instrument.tradingsymbol,
            detail=(
                f"Broker stop #{int(pos.gtt_id)} fired and its sell order "
                f"#{fired.child_order_id or '?'} rests unfilled; it could not be cancelled "
                "and confirmed, so no exit was sent over it · cancel it in Kite"
            ),
            severity="CRITICAL",
        )
        return STOP_UNKNOWN
    if done.outcome == pstop.CANCEL_COMPLETED:
        return STOP_FIRED
    if not await _book_stop_child_fill(uid, client, pos, done):
        return STOP_UNKNOWN
    session_for(uid).note(
        "warning",
        f"{pos.instrument.tradingsymbol}: broker stop fired but its sell order "
        f"{done.order_id} did not fill; cancelled ({done.status.lower()}), selling the "
        f"{int(pos.quantity)} still held",
    )
    return STOP_RELEASED


async def _cancel_protection(uid: str, client, pos: PositionState, *,
                             forced: bool = False) -> bool:
    """True only when no broker stop remains that could sell this position."""
    if await _release_or_classify_stop(client, pos, uid, forced=forced) != STOP_RELEASED:
        return False
    pos.gtt_id = 0
    pos.gtt_at = 0.0
    pos.gtt_qty = 0
    positions_store.put(uid, pos)
    return True


async def _broker_long_quantity(
    uid: str, client, pos: PositionState, *, current_registered: bool = True
) -> Optional[int]:
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
    from app.services import snapback_reconciliation as recon

    return recon.attributable_long_quantity(
        uid,
        pos.account_id,
        exchange=pos.instrument.exchange,
        product=_product(pos),
        symbol=pos.instrument.tradingsymbol,
        current_quantity=int(pos.quantity),
        broker_quantity=max(0, total) if matched else 0,
        current_registered=current_registered,
    )


def _book_exit_pnl(uid: str, client, pos: PositionState, *, order_id: str,
                   quantity: int, price: float, pnl: float,
                   source: str = "exit") -> str:
    """Feed one confirmed exit into the account daily-loss accounting, once.

    The INR daily-loss breaker reads ``state.daily_realized_pnl_strict``: the
    signed execution ledger plus a per-day accumulator. Gamma books to the
    accumulator ONLY. Its entries are never recorded in the ledger, and the
    ledger holds ONE cost basis per account and contract that every strategy
    shares: a Gamma SELL booked there would either be dropped as "already flat"
    (another strategy closed the same weekly strike earlier, so the loss never
    reached the breaker) or be matched against that other strategy's cost basis.
    ``source`` is kept for the callers' sake and no longer selects a ledger.
    """
    del client, quantity, price, source
    oid = str(order_id or "")
    if oid and pos.exit_booked_order_id == oid:
        return BOOK_ALREADY
    try:
        from app.services.kite_engine import state as kite_state
        kite_state.record_realized_pnl(uid, float(pnl))
    except Exception as exc:
        log.error("gamma_unwind: realised P&L not booked for %s: %s",
                  pos.instrument.tradingsymbol, exc)
        return BOOK_UNKNOWN
    return BOOK_LEGACY


def _flag_pnl(pos: PositionState) -> None:
    """Mark realised P&L as possibly missing from the daily-loss figure."""
    pos.pnl_reconciliation_required = True
    pos.pnl_flagged_day = _today_str()


def _flag_unbooked_pnl(uid: str, session, pos: PositionState, oid: str) -> None:
    _flag_pnl(pos)
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


def _new_exit_tag() -> str:
    # Kite tags are alphanumeric and at most 20 characters.
    return "gx" + uuid.uuid4().hex[:16]


def _clear_exit_fields(pos: PositionState) -> None:
    pos.exiting = False
    pos.exit_order_id = ""
    pos.exit_tag = ""
    pos.exit_submitted_ms = 0
    pos.exit_requested_qty = 0
    pos.exit_requested_price = 0.0
    pos.exit_pending_reason = ""


def _exit_lock(uid: str, symbol: str) -> asyncio.Lock:
    return _exit_locks.setdefault((uid, symbol), asyncio.Lock())


def _halt_unresolved_exit(uid: str, client, pos: PositionState, oid: str) -> None:
    """An exit SELL whose outcome is unknown stops new entries and pages the operator."""
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


async def _resolve_submitted_exit(uid: str, client, pos: PositionState) -> tuple[str, str]:
    """Find the broker order of an exit whose id was never recorded.

    The exit's unique tag was persisted before the SELL left, so a lost reply, a
    cancelled task or a restart is settled by reading the order book, never by
    sending again. ``("", reason)`` means no order id yet: ``exit_submit_unconfirmed``
    while the in-flight window is open, ``exit_not_submitted`` once the book proves
    the SELL never arrived (the exit is released and the stop re-armed), or an
    unresolved/ambiguous reason that keeps the fence and alerts.
    """
    symbol = pos.instrument.tradingsymbol
    try:
        orders = await client.get_orders()
    except Exception as exc:
        log.warning("gamma_unwind: order book unreadable for %s: %s", symbol, exc)
        orders = None
    if not isinstance(orders, list):
        _halt_unresolved_exit(uid, client, pos, EXIT_SUBMITTING)
        return "", "exit_identity_unresolved"

    tag = str(pos.exit_tag or LEGACY_EXIT_TAG)
    since = int(pos.exit_submitted_ms or pos.entered_ms or 0)
    wanted_qty = int(pos.exit_requested_qty or pos.quantity)
    exchange = pos.instrument.exchange.upper()
    product = _product(pos)
    matches: list[dict] = []
    for order in orders:
        if not isinstance(order, dict):
            continue
        if str(order.get("tradingsymbol") or "").upper() != symbol.upper():
            continue
        if str(order.get("exchange") or "").upper() != exchange:
            continue
        if str(order.get("product") or "").upper() != product:
            continue
        if str(order.get("transaction_type") or "").upper() != "SELL":
            continue
        tags = {str(order.get("tag") or "")}
        tags.update(str(t) for t in (order.get("tags") or []) if t)
        if tag not in tags:
            continue
        oid = str(order.get("order_id") or "")
        if not oid or oid == str(pos.exit_booked_order_id or ""):
            continue
        stamp = _order_ms(order)
        if stamp and since and stamp < since - 60_000:
            continue  # a SELL from before this exit was requested
        try:
            qty = int(float(order.get("quantity") or 0))
        except (TypeError, ValueError):
            qty = 0
        if wanted_qty and qty and qty != wanted_qty:
            continue
        matches.append(order)

    if len(matches) == 1:
        oid = str(matches[0].get("order_id") or "")
        pos.exit_order_id = oid
        positions_store.put(uid, pos)
        session_for(uid).note(
            "warning",
            f"{symbol}: exit SELL {oid} recovered from the order book; not resent",
        )
        return oid, "found"
    if len(matches) > 1:
        _halt_unresolved_exit(uid, client, pos, EXIT_SUBMITTING)
        return "", "exit_identity_ambiguous"

    submitted = int(pos.exit_submitted_ms or 0)
    if submitted and _now_ms() - submitted < _EXIT_IN_FLIGHT_GRACE_MS:
        return "", "exit_submit_unconfirmed"
    # The book was read and holds no such SELL after the in-flight window: the exit
    # never reached the broker. Release it and put the broker stop back.
    _clear_exit_fields(pos)
    positions_store.put(uid, pos)
    session_for(uid).note(
        "warning",
        f"{symbol}: exit SELL never reached the broker; stop re-armed",
    )
    await _ensure_protection(uid, client, pos)
    return "", "exit_not_submitted"


async def _settle_exit_serialised(uid: str, client, pos: PositionState) -> tuple[bool, str]:
    """``_settle_known_exit`` under the position's exit lock.

    Reconcile and the time sweeps reach an acknowledged exit while the exit that
    placed it is still polling. Two settlements of the same evidence subtract the
    fill twice, so a caller that finds the lock held simply skips this pass.
    """
    lock = _exit_lock(uid, pos.instrument.tradingsymbol)
    if lock.locked():
        return False, "exit_in_progress"
    async with lock:
        if not pos.exiting:
            return False, "exit_not_pending"
        return await _settle_known_exit(uid, client, pos)


async def _settle_known_exit(uid: str, client, pos: PositionState) -> tuple[bool, str]:
    """Resolve one acknowledged Gamma-Unwind SELL without ever sending another."""
    oid = str(pos.exit_order_id or "")
    if not oid or oid == EXIT_SUBMITTING:
        oid, why = await _resolve_submitted_exit(uid, client, pos)
        if not oid:
            return False, why

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
    if not pos.exiting or str(pos.exit_order_id or "") != oid:
        # The poll above awaits for seconds; another path settled this very order
        # meanwhile. Applying the same evidence again would subtract it twice.
        return False, "exit_already_settled"

    if not resolution.settled:
        # Keep the exact SELL identity durable. No replacement GTT is armed
        # while that SELL can still fill, otherwise the exit and stop could both
        # execute and create a naked short.
        positions_store.put(uid, pos)
        _halt_unresolved_exit(uid, client, pos, oid)
        return False, "exit_reconciliation_required"

    sold = min(int(resolution.filled_quantity), int(pos.quantity))
    reason = str(pos.exit_pending_reason or "exit")
    fill = float(
        resolution.average_price
        or pos.exit_requested_price
        or pos.effective_entry
    )

    if sold <= 0:
        _clear_exit_fields(pos)
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
        _clear_exit_fields(pos)
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
                        grade_price: float | None = None,
                        client=None, forced: bool = False) -> tuple[bool, str]:
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
    lock = _exit_lock(*key)
    if lock.locked():
        return False, "exit_in_progress"
    async with lock:
        if not pos.is_open:
            return False, "position_not_open"
        ok, status = await _execute_exit_locked(
            uid, pos, market_ltp=market_ltp, reason=reason,
            grade_price=grade_price, client=client, forced=forced,
        )
        if ok or status in ("partial_exit", "closed"):
            _exit_retry_at.pop(key, None)
        else:
            _exit_retry_at[key] = time.monotonic() + _EXIT_RETRY_COOLDOWN_S
        return ok, status


def _resize_to_broker(uid: str, pos: PositionState, broker_qty: int) -> None:
    """The broker holds less than the registry: trust the broker, loudly.

    A hand square-off in Kite or a part-filled stop child leaves fewer contracts
    than Sterling recorded. Selling or protecting the recorded quantity would
    either be refused or oversell, so the registry follows the broker, the P&L of
    the missing contracts is flagged for reconciliation and the operator is paged.
    """
    held = int(pos.quantity)
    pos.quantity = int(broker_qty)
    lot = max(1, int(pos.instrument.lot_size or 1))
    pos.lots = max(1, (pos.quantity + lot - 1) // lot)
    _flag_pnl(pos)
    try:
        positions_store.put(uid, pos)
    except Exception as exc:
        log.error("gamma_unwind: resized position not persisted for %s: %s",
                  pos.instrument.tradingsymbol, exc)
    session_for(uid).note(
        "error",
        f"{pos.instrument.tradingsymbol}: broker holds {int(broker_qty)} of the "
        f"{held} recorded; position resized, reconcile P&L with the broker",
    )
    from app.services.notifications.telegram_lifecycle import enqueue_best_effort
    enqueue_best_effort(
        uid,
        dedupe_key=(
            f"gamma:resized:{pos.signal_id}:{held}:{int(broker_qty)}"
        ),
        strategy="Gamma-Unwind",
        event_type="POSITION RESIZED",
        title=pos.instrument.tradingsymbol,
        detail=(
            f"Broker holds {int(broker_qty)} of {held} recorded · registry resized · "
            "P&L of the missing contracts needs reconciling with the broker"
        ),
        severity="CRITICAL",
    )


async def _execute_exit_locked(uid: str, pos: PositionState, *,
                               market_ltp: float, reason: str,
                               grade_price: float | None = None,
                               client=None, forced: bool = False) -> tuple[bool, str]:
    session = session_for(uid)
    if client is None:
        client = await _client(uid)

    if pos.exiting:
        return await _settle_known_exit(uid, client, pos)
    if pos.status == positions_store.PENDING:
        return False, "entry_reconciliation_pending"

    broker_qty = await _broker_long_quantity(uid, client, pos)
    if broker_qty is None or broker_qty <= 0:
        return False, "broker_quantity_unverified"
    held_before = int(pos.quantity)
    # ``forced`` (an operator flatten) is passed only when set: it does not wait out the
    # grace window for a fired stop's resting child.
    released = (await _cancel_protection(uid, client, pos, forced=True) if forced
                else await _cancel_protection(uid, client, pos))
    if not released:
        return False, "protection_not_released"
    if int(pos.quantity) != held_before:
        # The fired stop's child part-filled before it was cancelled and that fill is
        # booked: the holding read above predates it. Sell only what is still held.
        broker_qty = await _broker_long_quantity(uid, client, pos)
        if broker_qty is None or broker_qty <= 0:
            return False, "broker_quantity_unverified"
    if broker_qty < int(pos.quantity):
        # Sell what the broker holds, not what the registry remembers.
        _resize_to_broker(uid, pos, broker_qty)

    # The SELL's identity is durable BEFORE it leaves: a unique tag and a
    # "submitting" id. A cancelled task, a lost reply or a crash then leaves
    # something reconcile can find in the order book instead of a stranded exit.
    tag = _new_exit_tag()
    pos.exiting = True
    pos.exit_pending_reason = str(reason)
    # This value is only a grading/reference fallback; live P&L uses broker fill.
    pos.exit_requested_price = float(grade_price or market_ltp)
    pos.exit_requested_qty = int(pos.quantity)
    pos.exit_tag = tag
    pos.exit_order_id = EXIT_SUBMITTING
    pos.exit_submitted_ms = _now_ms()
    try:
        positions_store.put(uid, pos)
    except Exception as exc:
        _clear_exit_fields(pos)
        log.error("gamma_unwind: exit intent not persisted for %s: %s",
                  pos.instrument.tradingsymbol, exc)
        await _ensure_protection(uid, client, pos)
        return False, "exit_not_persisted"

    try:
        from app.services.snapback_family_mode import (
            INTENT_REDUCE, ReduceBound, canonical_broker_capability,
        )
        with canonical_broker_capability(
            f"gamma-unwind-exit:"
            f"{getattr(client, '_account_id', '') or 'paper'}:"
            f"{pos.instrument.tradingsymbol}:{pos.signal_id}",
            intent=INTENT_REDUCE,
            # One contract, the closing side, never more than the position: the
            # transport refuses an exit that would open or flip exposure.
            bound=ReduceBound.closing(
                exchange=pos.instrument.exchange,
                symbol=pos.instrument.tradingsymbol,
                side="SELL", quantity=int(pos.quantity),
            ),
        ):
            res = await client.place_order(
                f"{pos.instrument.exchange}:{pos.instrument.tradingsymbol}",
                "sell",
                float(pos.quantity),
                order_type="market_order",
                product=_product(pos),
                tag=tag,
                allow_amo=False,
            )
        oid = str((res or {}).get("order_id") or "")
    except asyncio.CancelledError:
        # The SELL may or may not have reached the broker. The durable
        # 'submitting' identity above is exactly what reconcile resolves; rolling
        # it back here would re-arm a stop over a SELL that may be live.
        raise
    except Exception as exc:
        from app.services.kite_engine.protective_stop import is_definitive_refusal
        if is_definitive_refusal(exc):
            # Nothing was created at the broker: release the exit, restore the stop.
            _clear_exit_fields(pos)
            positions_store.put(uid, pos)
            await _ensure_protection(uid, client, pos)
            session.note(
                "error",
                f"exit rejected for {pos.instrument.tradingsymbol}: {exc}",
            )
            return False, "submit_failed"
        session.note(
            "error",
            f"exit for {pos.instrument.tradingsymbol} not acknowledged ({exc}); "
            "resolving against the order book, no resend",
        )
        return await _settle_known_exit(uid, client, pos)

    if not oid:
        session.note(
            "error",
            f"exit for {pos.instrument.tradingsymbol} returned no order id; "
            "resolving against the order book, no resend",
        )
        return await _settle_known_exit(uid, client, pos)

    pos.exit_order_id = oid
    positions_store.put(uid, pos)
    return await _settle_known_exit(uid, client, pos)


async def flatten_all(uid: str, client, *, reason: str = "operator") -> dict:
    """Exit every open Gamma-Unwind position now (Square-Off All / Kill Switch).

    Each position leaves through :func:`_execute_exit`, the same canonical path a
    stop or the 15:20 sweep uses: its resting broker stop is cancelled first, the
    SELL is tagged and journaled before it is sent, and the exit is serialised
    with every other exit of that position. A second call finds nothing left to
    do. Returns ``{"closed": int, "failed": [..], "skipped": [..]}``: ``failed``
    names a position that is still open (its stop is restored), ``skipped`` one
    another exit is already handling or that was already closed.
    """
    label = str(reason or "operator").strip().upper() or "OPERATOR"
    closed = 0
    failed: list[str] = []
    skipped: list[str] = []
    try:
        open_now = list(positions_store.open_positions(uid))
        if any(p.status == positions_store.PENDING for p in open_now):
            # An entry whose fill is not settled cannot be sold by quantity yet.
            await reconcile(uid)
            open_now = list(positions_store.open_positions(uid))
    except Exception as exc:
        log.error("gamma_unwind: flatten could not read the registry for %s: %s", uid, exc)
        return {"closed": 0, "failed": [f"gamma registry unreadable: {exc}"], "skipped": []}

    for pos in open_now:
        symbol = pos.instrument.tradingsymbol
        if not _position_account_matches(client, pos.account_id):
            failed.append(f"{symbol}: belongs to a different broker account")
            continue
        key = (uid, symbol)
        outcome = "open"
        detail = ""
        for _attempt in range(3):          # a part-filled exit leaves a remainder to sell
            _exit_retry_at.pop(key, None)  # an operator action is never backed off
            try:
                price = await _quote_ltp(client, pos)
            except Exception:
                price = 0.0
            price = price if price > 0 else float(pos.effective_entry)
            ok, status = await _execute_exit(
                uid, pos, market_ltp=price, reason=label, grade_price=price,
                client=client, forced=True,
            )
            detail = status
            if ok:
                outcome = "closed"
                break
            if status == "position_not_open":
                outcome = "skipped"
                break
            if status == "exit_in_progress":
                outcome = "in_progress"
                break
            if status != "partial_exit":
                break
        if outcome == "closed":
            closed += 1
        elif outcome == "skipped":
            skipped.append(f"{symbol}: already closed")
        elif outcome == "in_progress":
            skipped.append(f"{symbol}: an exit is already in progress")
        else:
            failed.append(f"{symbol}: {detail or 'exit incomplete'}")
    return {"closed": closed, "failed": failed, "skipped": skipped}


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
        await _settle_exit_serialised(uid, client, pos)

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
        _flag_pnl(pos)
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


async def _broker_day_sells(client, pos: PositionState) -> Optional[tuple[int, float]]:
    """(quantity, average price) of today's SELLs on this contract, per the broker."""
    try:
        book = await client.get_positions()
    except Exception:
        return None
    rows = (book.get("net") or []) if isinstance(book, dict) else (book or [])
    qty = 0
    value = 0.0
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("tradingsymbol") or "").upper() != pos.instrument.tradingsymbol.upper():
            continue
        if str(row.get("exchange") or "").upper() != pos.instrument.exchange.upper():
            continue
        if str(row.get("product") or "").upper() != _product(pos):
            continue
        try:
            sold = int(float(row.get("day_sell_quantity") or 0))
            price = float(row.get("day_sell_price") or 0.0)
        except (TypeError, ValueError):
            continue
        if sold > 0 and price > 0:
            qty += sold
            value += sold * price
    return (qty, value / qty) if qty > 0 else None


async def _close_vanished_position(uid: str, client, pos: PositionState) -> None:
    """The broker holds nothing and no Gamma exit accounts for it.

    A hand square-off or a lost exit acknowledgement followed by a fill closes the
    position without Gamma seeing the fill. Dropping the row silently would leave
    the loss out of the daily-loss breaker, so the P&L is estimated from the
    broker's own average sell price where it is there, the position is flagged
    (which also holds new Gamma entries for the day) and the operator is paged.
    """
    symbol = pos.instrument.tradingsymbol
    session = session_for(uid)
    quantity = int(pos.quantity)
    sells = await _broker_day_sells(client, pos)
    exit_price = 0.0
    pnl: float | None = None
    if sells is not None:
        exit_price = float(sells[1])
        pnl = realised_inr(pos, exit_price, min(quantity, int(sells[0])))
        booking = _book_exit_pnl(
            uid, client, pos, order_id=f"vanished:{pos.signal_id}",
            quantity=quantity, price=exit_price, pnl=pnl, source="reconcile",
        )
        if booking == BOOK_UNKNOWN:
            pnl = None
        else:
            pos.exit_booked_order_id = f"vanished:{pos.signal_id}"
    _flag_pnl(pos)
    total = None if pnl is None else float(pos.partial_realised_inr) + pnl
    positions_store.close(
        uid, symbol, "not at broker on reconcile",
        exit_price=exit_price, quantity_closed=quantity, pnl_inr=total,
    )
    if pnl is not None:
        session.record.record(pnl, _today_str(), outcome_pnl=total)
        positions_store.save_record(uid, session.record)
    session.note(
        "error",
        f"{symbol}: not at the broker and no Gamma exit accounts for it; "
        + ("P&L estimated from the broker's average sell price"
           if pnl is not None else "P&L unknown")
        + ", reconcile with the broker",
    )
    from app.services.notifications.telegram_lifecycle import enqueue_best_effort
    enqueue_best_effort(
        uid,
        dedupe_key=f"gamma:pnl-unbooked:{pos.signal_id}:vanished",
        strategy="Gamma-Unwind",
        event_type="PNL UNRECONCILED",
        title=symbol,
        detail=(
            f"Position closed outside Gamma · Qty {quantity} · "
            + (f"estimated P&L ₹{pnl:,.2f} from the broker's sell price"
               if pnl is not None else "P&L not booked to the daily-loss cap")
            + " · reconcile with the broker · new entries held"
        ),
        severity="CRITICAL",
    )


async def _sell_after_stale_stop(uid: str, client, pos: PositionState) -> None:
    """Exit a position whose fired stop is no longer selling. Never raises into reconcile."""
    try:
        from app.services.kite_engine.market_hours import is_management_session

        if not is_management_session():
            return
        ltp = await _quote_ltp(client, pos)
        if ltp <= 0:
            return
        ok, why = await _execute_exit(
            uid, pos, market_ltp=ltp, reason="STOP_UNFILLED", grade_price=ltp, client=client,
        )
        session_for(uid).note(
            "warning" if not ok else "info",
            f"{pos.instrument.tradingsymbol}: stop unfilled; exit {'sent' if ok else 'held: ' + str(why)}",
        )
    except Exception as exc:  # noqa: BLE001 - reconcile must keep going
        log.warning("gamma stale-stop exit failed for %s: %s", pos.instrument.tradingsymbol, exc)


async def _reconcile_short_inventory(uid: str, client, pos: PositionState,
                                     broker_qty: int) -> bool:
    """The broker holds fewer contracts than the registry. True once resized.

    A stop that FIRED is the exception: its child order is still selling the
    remainder, the registry must keep describing the whole position until that
    fill is attributed, and resizing now would make the fill unattributable.
    """
    if pos.gtt_id:
        from app.services.kite_engine import protective_stop as pstop
        status = await pstop.stop_status(
            client, int(pos.gtt_id),
            tradingsymbol=pos.instrument.tradingsymbol,
            direction="long",
            exchange=pos.instrument.exchange,
            product=_product(pos),
            qty=int(pos.quantity),
        )
        if status in pstop.FIRED_VERDICTS or status == pstop.STOP_UNVERIFIED:
            session_for(uid).note(
                "warning",
                f"{pos.instrument.tradingsymbol}: broker holds {broker_qty} of "
                f"{int(pos.quantity)} while its stop is {status}; waiting for the "
                "stop's exit to settle",
            )
            if status == pstop.STOP_TRIGGERED_STALE:
                # The child will not fill any more, so nothing at the broker will sell the
                # rest. Say so, then sell it now through the normal exit path (it cancels the
                # child, books its part-fill and sells only what the broker still holds)
                # instead of waiting for the next tick on this contract or a Square-Off.
                from app.services.notifications.telegram_lifecycle import enqueue_best_effort
                enqueue_best_effort(
                    uid,
                    dedupe_key=(f"gamma:stop-child-stale:{pos.signal_id}:{int(pos.gtt_id)}"),
                    strategy="Gamma-Unwind",
                    event_type="STOP TRIGGERED, SELL NOT FILLING",
                    title=pos.instrument.tradingsymbol,
                    detail=(
                        f"Broker stop #{int(pos.gtt_id)} fired but its sell order rests "
                        f"unfilled · broker holds {broker_qty} of {int(pos.quantity)} · "
                        "cancelling it and selling at market now"
                    ),
                    severity="CRITICAL",
                )
                await _sell_after_stale_stop(uid, client, pos)
            return False
    _resize_to_broker(uid, pos, broker_qty)
    return True


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
            done, _ = await _settle_exit_serialised(uid, client, pos)
            if done:
                continue
            current = positions_store.get(uid, pos.instrument.tradingsymbol)
            if current is not None and current.exiting:
                continue
            pos = current or pos
            if not pos.is_open:
                continue

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
            broker_qty = await _broker_long_quantity(uid, client, pos)
            if broker_qty == 0:
                if pos.gtt_id or pos.gtt_pending:
                    # Broker inventory is zero while a stop is still on file.
                    # Either the stop FIRED (the normal live stop-out: book the
                    # fill) or the position was closed by hand and the stop must
                    # be removed before it can SELL into zero inventory.
                    stop = await _release_or_classify_stop(client, pos, uid)
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
                await _close_vanished_position(uid, client, pos)
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

            if broker_qty is None:
                session.note(
                    "warning",
                    f"{pos.instrument.tradingsymbol}: broker quantity could "
                    "not be read; durable Gamma-Unwind state left as is",
                )
                continue
            if broker_qty < int(pos.quantity):
                if not await _reconcile_short_inventory(uid, client, pos, broker_qty):
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
        broker_qty = await _broker_long_quantity(
            uid, client, pos, current_registered=False
        )
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
