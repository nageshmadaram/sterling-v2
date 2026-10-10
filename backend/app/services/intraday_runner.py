"""The live path for the intraday pack: entry, protection, trailing and exit.

Nothing here decides whether a setup is valid — that is
``app.engines.intraday`` and it runs identically in a replay. This module is
everything that only exists because real money is involved: sizing against
capital, a stop resting at the broker, a tick loop that can exit intrabar, and
a reconcile that assumes the broker is right and we are wrong.

Two rules shape the whole file:

* **The broker is the truth.** A position we think we hold and Zerodha does not
  is closed, not re-protected.
* **An exit claims the position first.** ``pos.exiting`` is taken before any
  order goes out, so the tick loop, the session-end sweep and a manual
  square-off cannot each sell the same lots.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.core.logging import get_logger
from app.engines.intraday import IntradayConfig, STRATEGY_ID
from app.engines.intraday.position import (ContractRef, IntradayPosition, OPEN,
                                           PENDING, align_to_tick, premium_stop_for,
                                           q2, should_exit, should_scale_out,
                                           spot_trail, update_trail)
from app.services import intraday_positions as store
from app.services.intraday import get_config, ist_today, status as scan_status

log = get_logger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))
_OWNER = STRATEGY_ID
_locks: dict[str, asyncio.Lock] = {}
_notes: dict[str, list[dict]] = {}
_subscribed: dict[str, set[int]] = {}
_last_auto_scan_monotonic: dict[str, float] = {}

__all__ = ["arm", "adopt", "on_ticks", "square_off_all", "exit_one", "reconcile",
           "scan_all_once", "auto_scan_loop", "notes", "realised_pnl_today",
           "positions_view", "is_paper", "auto_execute", "auto_blocker_for",
           "check_rules", "entry_blocker"]


def _lock_for(uid: str) -> asyncio.Lock:
    lock = _locks.get(uid)
    if lock is None:
        lock = _locks[uid] = asyncio.Lock()
    return lock


def _now_ms() -> int:
    return int(datetime.now(_IST).timestamp() * 1000)


def _today() -> str:
    return ist_today().isoformat()


def note(uid: str, kind: str, message: str) -> None:
    rows = _notes.setdefault(uid, [])
    rows.append({"kind": kind, "message": message, "at_ms": _now_ms()})
    del rows[:-40]


def notes(uid: str) -> list[dict]:
    return list(_notes.get(uid) or [])[-20:]


def _is_market_open(cfg: IntradayConfig) -> bool:
    now = datetime.now(_IST)
    if now.weekday() >= 5:
        return False
    return cfg.session_start <= now.strftime("%H:%M") <= cfg.session_end


def _entries_allowed(cfg: IntradayConfig) -> bool:
    """Entries stop before the session does. Exits never do."""
    now = datetime.now(_IST).strftime("%H:%M")
    return _is_market_open(cfg) and now < cfg.no_entry_after


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
        log.warning("intraday: account mode unavailable for %s: %s", uid, exc)
        return None


def auto_execute(uid: str) -> bool:
    """Auto entry needs BOTH the account-wide switch and this engine's own.

    ANDed, never ORed: the shared switch is the operator's master control and
    this engine's is a statement that these three strategies specifically have
    earned it. Neither alone is enough.
    """
    try:
        from app.services.kite_engine import state as engine_state
        shared = bool(getattr(engine_state.get_config(uid), "auto_execute", False))
    except Exception:
        shared = False
    return shared and bool(get_config(uid).auto_execute)


def _replay_owns_the_board() -> bool:
    try:
        from app.services.simulation import simulation_runner
        return bool(simulation_runner.has_session_view)
    except Exception:
        return False


def _safety(uid: str, idempotency_key: Optional[str]) -> tuple[bool, str]:
    """The account-wide gate: kill switch, daily loss, duplicate order.

    Fails CLOSED. A safety check that cannot run is not a pass — and ``uid`` is
    passed explicitly because the daily-loss breaker reads per-account P&L and
    silently returns zero without it, which is a breaker that never trips.
    """
    try:
        from app.services.live_safety import assert_safe_to_trade
        decision = assert_safe_to_trade([], idempotency_key,
                                        check_daily_loss=True, uid=uid)
        return bool(decision.allowed), str(decision.reason or "")
    except Exception as exc:
        log.error("intraday: safety check failed closed for %s: %s", uid, exc)
        return False, f"safety check unavailable: {exc}"


def realised_pnl_today(uid: str) -> float:
    return round(store.load_record(uid).roll(_today()).realised_inr, 2)


# ------------------------------------------------------------------- gating

def entry_blocker(uid: str, cfg: IntradayConfig, symbol: str) -> Optional[str]:
    """Every reason this engine will not open another position, in one place.

    Collected here rather than scattered through ``arm`` so the board can show
    the same answer BEFORE a click as the click itself would give.
    """
    if not cfg.enabled:
        return "the intraday engine is switched off"
    if _replay_owns_the_board():
        return "replay is driving this board — live entry is off"
    if not _entries_allowed(cfg):
        return f"outside the entry window ({cfg.session_start}–{cfg.no_entry_after})"
    open_now = store.open_positions(uid)
    if len(open_now) >= cfg.max_concurrent_positions:
        return (f"{len(open_now)} positions open, cap is "
                f"{cfg.max_concurrent_positions}")
    if any(p.contract.tradingsymbol == symbol for p in open_now):
        return f"already holding {symbol}"
    rec = store.load_record(uid).roll(_today())
    if rec.trades >= cfg.max_new_trades_per_day:
        return f"{rec.trades} trades today, cap is {cfg.max_new_trades_per_day}"
    if cfg.daily_loss_limit_inr > 0 and rec.realised_inr <= -abs(cfg.daily_loss_limit_inr):
        return (f"this engine is down Rs {abs(rec.realised_inr):,.0f} today, "
                f"limit Rs {cfg.daily_loss_limit_inr:,.0f}")
    return None


def _size(cfg: IntradayConfig, *, premium: float, stop: float,
          lot_size: int, uid: str) -> tuple[int, int, str]:
    """Lots and quantity for one option BUY. Returns ``(lots, qty, why)``.

    ``lots`` of 0 means do not trade, and ``why`` says which cap refused —
    never a silent floor to one lot, which turns ``risk_per_trade_pct`` into a
    suggestion.
    """
    rec = store.load_record(uid).roll(_today())
    cap = cfg.max_lots
    if cfg.descale_after_losses and rec.consecutive_losses >= cfg.descale_after_losses:
        cap = max(1, cap // 2)
    if cfg.sizing_mode == "LOTS":
        lots = min(cfg.lots, cap)
        return lots, lots * max(1, lot_size), (
            f"{lots} lot(s) fixed" + (" (de-scaled after losses)" if cap < cfg.max_lots else ""))
    from app.services.kite_engine.sizing import size_position
    res = size_position(entry_premium=premium, stop_premium=stop, lot_size=lot_size,
                        available_capital=cfg.capital_inr,
                        risk_pct=cfg.risk_per_trade_pct, max_lots=cap,
                        allow_min_lot_over_risk=cfg.allow_min_lot_over_risk)
    if getattr(res, "blocked", False):
        return 0, 0, str(res.reason)
    # ``lots`` is exchange lots; ``qty`` is units. Every value, risk and
    # P&L figure in this engine uses qty — confusing the two is a known
    # lot-size-multiple error in this repo.
    return int(res.lots), int(res.qty), str(res.reason)


# --------------------------------------------------------------- protection

async def _place_protection(uid: str, client, pos: IntradayPosition,
                            cfg: IntradayConfig) -> int:
    """Rest the stop (and target) at the broker.

    ``monitor`` mode deliberately places nothing: an operator who chose it has
    accepted that protection lives only while this process does, and the
    settings page says so. Everything else gets a GTT, and a GTT that fails to
    place is a warning on the board, not a silent monitor-only position.
    """
    if cfg.stop_mode == "monitor" or pos.stop <= 0 or pos.quantity <= 0:
        return 0
    if not _position_account_matches(client, pos.account_id):
        note(uid, "warning",
             f"{pos.contract.tradingsymbol}: protection refused — broker account ownership unverified")
        return 0
    try:
        from app.services.kite_engine.protective_stop import place_stop
        from app.services.snapback_family_mode import (
            INTENT_PROTECT,
            canonical_broker_capability,
        )
        with canonical_broker_capability(
            f"intraday-protect:{getattr(client, '_account_id', '') or 'paper'}:"
            f"{pos.contract.tradingsymbol}",
            intent=INTENT_PROTECT,
        ):
            gtt_id = await place_stop(
                client, tradingsymbol=pos.contract.tradingsymbol,
                exchange=pos.contract.exchange, qty=int(pos.quantity),
                trigger_premium=float(pos.stop), last_price=float(pos.effective_entry),
                # A BOUGHT option is long premium whichever way the thesis points.
                # Reading the thesis here is the bug that sold every PE at entry.
                direction="long", target_premium=float(pos.target or 0.0),
                product="MIS")
    except Exception as exc:                                       # noqa: BLE001
        gtt_id = None
        log.warning("intraday: protective GTT failed for %s: %s",
                    pos.contract.tradingsymbol, exc)
    if gtt_id:
        pos.gtt_id = int(gtt_id)
        pos.gtt_at = float(pos.stop)
        pos.gtt_qty = int(pos.quantity)
        store.put(uid, pos)
        return int(gtt_id)
    note(uid, "warning", f"{pos.contract.tradingsymbol}: no broker stop — "
                         "protection is this process only")
    return 0


async def _sync_protection(
    uid: str,
    pos: IntradayPosition,
    cfg: IntradayConfig,
    client=None,
) -> bool:
    """Move the resting stop to where the trail has got to.

    Skipped when it has not actually moved: a GTT modify per tick is both a rate
    limit and a way to lose the resting order to a rejected amend.
    """
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
            note(uid, "warning",
                 f"{pos.contract.tradingsymbol}: trail refused — broker account ownership unverified")
            return False
        with canonical_broker_capability(
            f"intraday-trail:{getattr(client, '_account_id', '') or 'paper'}:"
            f"{pos.contract.tradingsymbol}:{pos.gtt_id}",
            intent=INTENT_PROTECT,
        ):
            moved = await move_stop(
                client, trigger_id=int(pos.gtt_id),
                tradingsymbol=pos.contract.tradingsymbol,
                exchange=pos.contract.exchange, qty=int(pos.quantity),
                trigger_premium=float(pos.stop),
                last_price=float(pos.effective_entry), direction="long",
                target_premium=float(pos.target or 0.0), product="MIS")
        if moved:
            pos.gtt_at = float(pos.stop)
            pos.gtt_qty = int(pos.quantity)
            store.put(uid, pos)
            return True
        note(uid, "warning",
             f"{pos.contract.tradingsymbol}: broker stop/quantity update was not confirmed")
        return False
    except Exception as exc:                                       # noqa: BLE001
        log.warning("intraday: could not move the broker stop for %s: %s",
                    pos.contract.tradingsymbol, exc)
        return False


async def _ensure_protection_synced(
    uid: str,
    client,
    pos: IntradayPosition,
    cfg: IntradayConfig,
) -> bool:
    """Ensure broker protection covers the exact confirmed live quantity."""
    if cfg.stop_mode == "monitor":
        return True

    if not pos.gtt_id:
        ok = bool(await _place_protection(uid, client, pos, cfg))
    else:
        ok = await _sync_protection(uid, pos, cfg, client=client)

    if not ok and getattr(client, "_is_paper", None) is False:
        try:
            from app.services import live_safety
            live_safety.set_kill_switch(
                True,
                f"Intraday protection not synchronized for "
                f"{pos.contract.tradingsymbol} qty={pos.quantity}",
            )
        except Exception as exc:                                   # noqa: BLE001
            log.error("intraday: could not engage kill switch after protection failure: %s", exc)
    return ok


async def _broker_long_quantity(client, pos: IntradayPosition) -> Optional[int]:
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
        log.warning("intraday: broker quantity unavailable for %s: %s",
                    pos.contract.tradingsymbol, exc)
        return None

    rows = (book.get("net") or []) if isinstance(book, dict) else (book or [])
    want_symbol = str(pos.contract.tradingsymbol or "").strip().upper()
    want_exchange = str(pos.contract.exchange or "").strip().upper()
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


async def _release_protection_for_exit(
    uid: str, client, pos: IntradayPosition
) -> bool:
    """Prove there is no broker-side stop that can race our own SELL."""
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
            f"intraday-exit-disarm:{getattr(client, '_account_id', '') or 'paper'}:"
            f"{pos.contract.tradingsymbol}:{gid}",
            intent=INTENT_REDUCE,
        ):
            outcome = await pstop.cancel_stop_result(client, gid)
    except Exception as exc:                                       # noqa: BLE001
        log.warning("intraday: could not cancel broker stop %s for %s: %s",
                    gid, pos.contract.tradingsymbol, exc)
        outcome = pstop.UNKNOWN

    safe = outcome == pstop.CANCELLED
    if not safe:
        status = await pstop.stop_status(
            client,
            gid,
            tradingsymbol=pos.contract.tradingsymbol,
            direction="long",
            exchange=pos.contract.exchange,
            product="MIS",
            qty=int(pos.quantity),
        )
        safe = status == pstop.STOP_ABSENT
        if not safe and status == pstop.STOP_TRIGGERED_STALE:
            safe, why = await pstop.release_stale_fired_stop(
                client, gid, tradingsymbol=pos.contract.tradingsymbol,
                exchange=pos.contract.exchange, product="MIS", qty=int(pos.quantity),
            )
            if not safe:
                note(uid, "warning", f"{pos.contract.tradingsymbol}: exit held — {why}")
                return False
        if not safe:
            note(
                uid,
                "warning",
                f"{pos.contract.tradingsymbol}: exit held — broker GTT #{gid} "
                f"is {status}; refusing a second SELL",
            )
            return False

    pos.gtt_id = 0
    pos.gtt_at = 0.0
    pos.gtt_qty = 0
    store.put(uid, pos)
    return True


# ------------------------------------------------------------------ ticker

async def _subscribe(uid: str) -> None:
    """Hold ticks for everything this engine is watching or holding.

    The subscription set is shared per account and refcounted by owner, so
    releasing ours never takes another engine's ticks away.
    """
    st = scan_status(uid)
    tokens = {int((r.get("contract") or {}).get("token") or 0)
              for r in st.signals.values()}
    for p in store.open_positions(uid):
        tokens.add(int(p.contract.token or 0))
        # The UNDERLYING as well. Every one of these three states its stop in
        # the underlying's points, and without its ticks that stop can never
        # fire — the position would be protected only by its premium stop,
        # which is a different rule than the one on the board.
        tokens.add(int(p.underlying_token or 0))
    tokens.discard(0)
    held = _subscribed.setdefault(uid, set())
    new, stale = tokens - held, held - tokens
    if new:
        try:
            from app.services.exchanges.kite import constants as K
            from app.services.exchanges.kite import ticker_manager
            await ticker_manager.subscribe(uid, sorted(new), K.MODE_FULL, owner=_OWNER)
            held |= new
        except Exception as exc:                                   # noqa: BLE001
            log.warning("intraday subscribe failed for %s: %s", uid, exc)
    if stale:
        try:
            from app.services.exchanges.kite import ticker_manager
            await ticker_manager.release(uid, sorted(stale), owner=_OWNER)
        except Exception as exc:                                   # noqa: BLE001
            log.debug("intraday release failed for %s: %s", uid, exc)
        held -= stale


async def release_subscriptions(uid: str) -> None:
    held = _subscribed.get(uid) or set()
    if not held:
        return
    try:
        from app.services.exchanges.kite import ticker_manager
        await ticker_manager.release(uid, sorted(held), owner=_OWNER)
    except Exception as exc:                                       # noqa: BLE001
        log.debug("intraday release failed for %s: %s", uid, exc)
    held.clear()


# --------------------------------------------------------------------- entry

async def arm(uid: str, signal_id: str) -> dict:
    """Enter one armed signal by id."""
    cfg = get_config(uid)
    async with _lock_for(uid):
        st = scan_status(uid)
        row = st.signals.get(signal_id)
        if row is None:
            return {"ok": False, "message": f"no armed signal {signal_id} in this scan"}
        contract = row.get("contract") or {}
        sig = row.get("signal") or {}
        symbol = str(contract.get("symbol") or "")
        if not symbol:
            return {"ok": False, "message": "this signal has no resolved contract"}
        blocker = entry_blocker(uid, cfg, symbol)
        if blocker:
            note(uid, "blocked", f"entry refused for {symbol}: {blocker}")
            return {"ok": False, "message": blocker}

        client = await _client(uid)
        quote = await _fresh_quote(client, contract, cfg,
                                   spot=float(sig.get("entry") or 0.0))
        if quote.get("blockers"):
            why = quote["blockers"][0]
            note(uid, "blocked", f"entry refused for {symbol}: {why}")
            return {"ok": False, "message": why}
        premium = float(quote["premium"])

        stop = premium_stop_for(premium, float(sig.get("entry") or 0.0),
                                float(sig.get("stop") or 0.0), cfg,
                                delta=quote.get("delta"))
        if stop >= premium:
            return {"ok": False, "message": "the premium stop is not below the premium"}
        lot_size = int(contract.get("lot_size") or 0) or 1
        lots, qty, why = _size(cfg, premium=premium, stop=stop,
                               lot_size=lot_size, uid=uid)
        if lots <= 0 or qty <= 0:
            note(uid, "blocked", f"entry refused for {symbol}: {why}")
            return {"ok": False, "message": why}

        idem = f"{STRATEGY_ID}:{uid}:{signal_id}"
        ok, safety_why = _safety(uid, idem)
        if not ok:
            note(uid, "blocked", f"entry refused for {symbol}: {safety_why}")
            return {"ok": False, "message": safety_why}

        tick = float(contract.get("tick_size") or 0.05) or 0.05
        limit = align_to_tick(premium, tick, side="buy")
        target, target2 = _premium_targets(premium, stop, sig, cfg)
        pos = IntradayPosition(
            strategy=str(row.get("strategy") or ""), signal_id=signal_id,
            underlying=str(row.get("symbol") or ""),
            contract=ContractRef(
                tradingsymbol=symbol, exchange=str(contract.get("exchange") or "NFO"),
                token=int(contract.get("token") or 0),
                option_type=str(contract.get("option_type") or "CE"),
                strike=float(contract.get("strike") or 0.0),
                expiry=str(contract.get("expiry") or ""),
                lot_size=lot_size, tick_size=tick),
            thesis="BEARISH" if str(sig.get("direction")) == "BEARISH" else "BULLISH",
            spot_entry=float(sig.get("entry") or 0.0),
            spot_stop=float(sig.get("stop") or 0.0),
            spot_target=float(sig.get("target") or 0.0),
            spot_target2=sig.get("target2"),
            spot_risk=float(sig.get("risk") or 0.0),
            underlying_token=int(row.get("underlying_token") or 0),
            entry=limit, stop=stop, initial_stop=stop,
            target=target, target2=target2,
            quantity=qty, lots=lots, peak=limit,
            stop_mode=cfg.stop_mode, idempotency_key=idem,
            account_id=str(getattr(client, "_account_id", "") or ""),
            entered_ms=_now_ms(), entry_day=_today(), status=PENDING)

        from app.services.intraday_canonical_entry import submit_entry

        result = await submit_entry(
            client=client,
            uid=uid,
            signal_id=signal_id,
            sub_strategy=str(row.get("strategy") or ""),
            exchange=pos.contract.exchange,
            symbol=symbol,
            quantity=qty,
            limit_price=limit,
        )
        if not result.success:
            note(uid, "error", f"entry rejected for {symbol}: {result.error}")
            return {"ok": False, "message": result.error or result.status}
        order_id = str(result.order_id or "")
        if not order_id:
            note(uid, "error", f"{symbol}: canonical execution returned no order id")
            return {"ok": False, "message": "canonical execution returned no order id"}
        pos.order_id = order_id
        store.put(uid, pos)

        from app.services.canonical_fill_reconciliation import (
            mark_projected as mark_fill_projected,
            resolve_entry_fill,
        )
        resolution = await resolve_entry_fill(
            uid=uid,
            client=client,
            order_id=order_id,
            expected_quantity=qty,
            reference_price=limit,
        )

        if resolution.filled_quantity <= 0:
            # The local PENDING row is the strategy projection for this broker
            # observation. A non-terminal journal state still blocks further
            # exposure; acknowledging the projection merely says our registry is
            # caught up with the evidence we have.
            if resolution.projection_version:
                mark_fill_projected(resolution)
            if resolution.terminal:
                store.mark_rejected(uid, symbol, resolution.status)
                note(uid, "error", f"{symbol} {resolution.status.lower()} at the broker")
                return {
                    "ok": False,
                    "message": f"order {resolution.status.lower()}",
                    "status": resolution.status,
                }
            note(
                uid,
                "warning",
                f"{symbol}: entry fill unresolved; no protection placed and no retry will be sent",
            )
            return {
                "ok": False,
                "message": resolution.reason or "entry fill unresolved; reconciliation required",
                "status": "PENDING_RECONCILIATION",
                "order_id": order_id,
            }

        # Protect only quantity the broker positively confirms. If a remainder
        # could not be settled, the canonical journal remains unresolved and
        # blocks new exposure, but the quantity already held is still protected.
        pos.quantity = int(resolution.filled_quantity)
        pos.lots = max(1, pos.quantity // max(1, lot_size))
        filled = store.mark_filled(
            uid,
            symbol,
            resolution.average_price or limit,
        ) or pos
        if not resolution.settled or resolution.reconciliation_required:
            filled.status = PENDING
            store.put(uid, filled)
        gtt_id = await _place_protection(uid, client, filled, cfg)
        if resolution.projection_version:
            mark_fill_projected(resolution)
        st.signals.pop(signal_id, None)
        await _subscribe(uid)
        if not resolution.settled or resolution.reconciliation_required:
            note(
                uid,
                "warning",
                f"{symbol}: protected {filled.quantity} confirmed units; "
                "entry remainder requires reconciliation",
            )
        else:
            note(uid, "entry", f"bought {filled.quantity} {symbol} @ {filled.effective_entry} "
                               f"(stop {filled.stop}, target {filled.target}) — {why}")
        return {
            "ok": True,
            "order_id": order_id,
            "symbol": symbol,
            "quantity": filled.quantity,
            "lots": filled.lots,
            "entry": filled.effective_entry,
            "stop": filled.stop,
            "target": filled.target,
            "gtt_id": gtt_id,
            "paper": getattr(client, "_is_paper", None) is True,
            "sizing": why,
            "status": (
                "FILLED"
                if resolution.settled and not resolution.reconciliation_required
                else "PARTIAL_RECONCILIATION_REQUIRED"
            ),
        }


def _premium_targets(premium: float, stop: float, sig: dict,
                     cfg: IntradayConfig) -> tuple[float, float]:
    """The premium objectives that match the strategy's own reward-to-risk.

    Derived from R rather than from the spot target scaled by a delta: delta
    changes as the trade works, so a delta-scaled target drifts away from the
    ratio the strategy was tested at. Holding the RATIO fixed is the honest
    translation.

    The second value is the runner's, and is 0 for the two strategies that have
    one objective. A row is only allowed to advertise a runner when one exists.
    """
    rr = float(sig.get("rr") or 0.0) or 2.0
    risk = max(0.0, premium - stop)
    if risk <= 0:
        return 0.0, 0.0
    first = q2(premium + rr * risk)
    spot_entry = float(sig.get("entry") or 0.0)
    spot_risk = float(sig.get("risk") or 0.0)
    t2 = sig.get("target2")
    if t2 is None or spot_risk <= 0 or spot_entry <= 0:
        return first, 0.0
    rr2 = abs(float(t2) - spot_entry) / spot_risk
    return first, q2(premium + rr2 * risk)


async def _fresh_quote(client, contract: dict, cfg: IntradayConfig,
                       spot: float = 0.0) -> dict:
    from app.services.intraday import _quote_for
    q = await _quote_for(client, contract, cfg, spot=spot)
    if q is None:
        return {"premium": 0.0, "blockers": ["no quote for this contract"]}
    return q


async def _confirm_fill(client, order_id: str) -> tuple[str, float]:
    if str(order_id).startswith("PAPER-"):
        return "COMPLETE", 0.0
    try:
        history = await client.get_order_history(order_id)
    except Exception as exc:                                       # noqa: BLE001
        log.warning("intraday: order status unavailable for %s: %s", order_id, exc)
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


async def adopt(uid: str, symbol: str, quantity: int, entry_price: float) -> dict:
    """Take responsibility for a position this engine did not open.

    Protection is placed as part of adopting. A hand-placed position the engine
    is managing but has not protected is the worst of both worlds.
    """
    if _replay_owns_the_board():
        return {"ok": False, "message": "replay is driving this board — adopt is off"}
    if quantity <= 0 or entry_price <= 0:
        return {"ok": False, "message": "quantity and entry_price must be positive"}
    cfg = get_config(uid)
    async with _lock_for(uid):
        existing = store.get(uid, symbol)
        if existing and existing.is_open:
            return {"ok": False, "message": f"already managing {symbol}"}
        client = await _client(uid)
        meta = await _contract_meta(client, symbol)
        if meta is None:
            return {"ok": False, "message": f"{symbol} is not a listed NFO/BFO contract"}
        stop = q2(float(entry_price) * (1.0 - cfg.premium_stop_pct / 100.0))
        pos = IntradayPosition(
            strategy="adopted", signal_id=f"adopt:{symbol}:{_today()}",
            underlying=meta["underlying"], contract=ContractRef(**meta["contract"]),
            thesis="BULLISH" if meta["contract"]["option_type"] == "CE" else "BEARISH",
            entry=q2(entry_price), fill_price=q2(entry_price),
            stop=stop, initial_stop=stop, target=0.0,
            quantity=int(quantity),
            lots=max(1, int(quantity) // max(1, meta["contract"]["lot_size"])),
            peak=q2(entry_price), stop_mode=cfg.stop_mode,
            account_id=str(getattr(client, "_account_id", "") or ""),
            entered_ms=_now_ms(), entry_day=_today(), status=OPEN)
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
        store.put(uid, pos)
        gtt_id = await _place_protection(uid, client, pos, cfg)
        await _subscribe(uid)
        note(uid, "adopt", f"adopted {quantity} {symbol} @ {entry_price}, stop {stop}")
        return {"ok": True, "symbol": symbol, "stop": stop, "gtt_id": gtt_id}


async def _contract_meta(client, symbol: str) -> Optional[dict]:
    for exchange in ("NFO", "BFO"):
        try:
            rows = await client.search_instruments(symbol, exchange, limit=5) or []
        except Exception:
            rows = []
        for r in rows:
            if str(r.get("tradingsymbol") or "") != symbol:
                continue
            return {
                "underlying": str(r.get("name") or symbol),
                "contract": {
                    "tradingsymbol": symbol, "exchange": exchange,
                    "token": int(r.get("instrument_token") or 0),
                    "option_type": str(r.get("instrument_type") or "CE"),
                    "strike": float(r.get("strike") or 0.0),
                    "expiry": str(r.get("expiry") or "")[:10],
                    "lot_size": int(r.get("lot_size") or 1) or 1,
                    "tick_size": float(r.get("tick_size") or 0.05) or 0.05,
                },
            }
    return None


# ---------------------------------------------------------------------- exit

def _clear_pending_reduction(pos: IntradayPosition) -> None:
    pos.exit_order_id = ""
    pos.exit_requested_qty = 0
    pos.exit_requested_price = 0.0
    pos.exit_pending_kind = ""
    pos.exit_pending_reason = ""
    pos.exiting = False


async def _settle_pending_reduction(
    uid: str,
    client,
    pos: IntradayPosition,
    cfg: IntradayConfig,
) -> tuple[bool, str]:
    """Reconcile one already-submitted SELL without ever sending another."""
    if not pos.exit_order_id or pos.exit_requested_qty <= 0:
        return False, "no_pending_reduction"

    from app.services.canonical_fill_reconciliation import resolve_acknowledged_order

    requested = int(pos.exit_requested_qty)
    resolution = await resolve_acknowledged_order(
        client=client,
        order_id=pos.exit_order_id,
        expected_quantity=requested,
        reference_price=float(pos.exit_requested_price or pos.effective_entry),
        timeout_s=5.0,
    )

    if not resolution.settled:
        if getattr(client, "_is_paper", None) is False:
            try:
                from app.services import live_safety
                live_safety.set_kill_switch(
                    True,
                    f"Intraday reduction unresolved for {pos.contract.tradingsymbol} "
                    f"order {pos.exit_order_id}",
                )
            except Exception as exc:                               # noqa: BLE001
                log.error("intraday: could not engage kill switch after uncertain exit: %s", exc)
        store.put(uid, pos)
        note(
            uid,
            "warning",
            f"{pos.contract.tradingsymbol}: SELL #{pos.exit_order_id} unresolved; "
            "no second SELL or replacement stop will be sent until reconciliation",
        )
        return False, "reconciliation_required"

    sold = min(int(resolution.filled_quantity), int(pos.quantity))
    if sold <= 0:
        status = resolution.status or "unfilled"
        _clear_pending_reduction(pos)
        store.put(uid, pos)
        await _place_protection(uid, client, pos, cfg)
        note(
            uid,
            "warning",
            f"{pos.contract.tradingsymbol}: reduction {status.lower()} with no fill; "
            "position remains open and protection was restored",
        )
        return False, status

    fill_price = float(resolution.average_price or pos.exit_requested_price or 0.0)
    if fill_price <= 0:
        store.put(uid, pos)
        note(
            uid,
            "warning",
            f"{pos.contract.tradingsymbol}: {sold} units sold but fill price is unknown; "
            "reconciliation required",
        )
        return False, "reconciliation_required"

    kind = pos.exit_pending_kind or "exit"
    reason = pos.exit_pending_reason or "exit"
    original_qty = int(pos.quantity)
    realised = round((fill_price - pos.effective_entry) * sold, 2)

    if kind == "scale":
        old_scaled = int(pos.scaled_qty or 0)
        old_value = float(pos.scaled_price or 0.0) * old_scaled
        pos.scaled_qty = old_scaled + sold
        pos.scaled_price = (
            (old_value + fill_price * sold) / pos.scaled_qty
            if pos.scaled_qty > 0
            else 0.0
        )
        pos.quantity = max(0, original_qty - sold)
        pos.lots = (
            max(1, pos.quantity // max(1, pos.contract.lot_size))
            if pos.quantity > 0
            else 0
        )
        pos.target1_done = True
        pos.breakeven_done = True
        pos.stop = max(pos.stop, pos.effective_entry)
        _clear_pending_reduction(pos)
        if pos.quantity <= 0:
            store.close(
                uid,
                pos.contract.tradingsymbol,
                "scale filled entire position",
                exit_price=fill_price,
            )
        else:
            store.put(uid, pos)
            await _place_protection(uid, client, pos, cfg)
        rec = store.load_record(uid).roll(_today())
        store.save_record(uid, rec.add_realised(realised))
        note(
            uid,
            "scale",
            f"banked {sold} {pos.contract.tradingsymbol} @ {fill_price:.2f} "
            f"(Rs {realised:,.0f}); {pos.quantity} remains",
        )
        return sold >= requested, resolution.status

    remaining = max(0, original_qty - sold)
    if remaining == 0:
        _clear_pending_reduction(pos)
        store.put(uid, pos)
        closed = store.close(
            uid,
            pos.contract.tradingsymbol,
            reason,
            exit_price=fill_price,
        )
        pnl = closed.realised_inr if closed else realised
        banked = closed.banked_inr if closed else pos.banked_inr
        rec = store.load_record(uid).roll(_today())
        store.save_record(
            uid,
            rec.record(
                pnl,
                outcome_pnl=banked + pnl,
            ),
        )
        note(
            uid,
            "exit",
            f"sold {sold} {pos.contract.tradingsymbol} @ {fill_price:.2f} "
            f"({reason}), Rs {pnl:,.0f}",
        )
        return True, resolution.status

    pos.quantity = remaining
    pos.lots = max(1, remaining // max(1, pos.contract.lot_size))
    _clear_pending_reduction(pos)
    store.put(uid, pos)
    await _place_protection(uid, client, pos, cfg)
    rec = store.load_record(uid).roll(_today())
    store.save_record(uid, rec.add_realised(realised))
    note(
        uid,
        "warning",
        f"{pos.contract.tradingsymbol}: exit filled {sold}/{original_qty} @ "
        f"{fill_price:.2f}; {remaining} remains and was re-protected",
    )
    return False, "partial_exit"


async def _exit_position(uid: str, client, pos: IntradayPosition, reason: str,
                         premium: float, cfg: IntradayConfig) -> bool:
    """Sell one position. The claim is taken BEFORE anything is sent.

    Every exit path in this engine goes through here for that reason: the tick
    loop, the session-end sweep and a manual square-off can all decide at the
    same moment, and two sells for one position is a naked short.
    """
    if pos.status == PENDING and pos.order_id:
        note(
            uid,
            "warning",
            f"{pos.contract.tradingsymbol}: exit held — entry order {pos.order_id} "
            "still has an unresolved remainder; reconcile the BUY before any SELL",
        )
        return False
    if pos.exiting or not pos.is_open:
        return False
    pos.exiting = True
    store.put(uid, pos)

    broker_qty = await _broker_long_quantity(client, pos)
    if broker_qty is None or broker_qty < int(pos.quantity):
        pos.exiting = False
        store.put(uid, pos)
        note(uid, "warning",
             f"{pos.contract.tradingsymbol}: exit held — broker quantity "
             f"{broker_qty if broker_qty is not None else 'unknown'} is below "
             f"local quantity {pos.quantity}")
        return False

    if not await _release_protection_for_exit(uid, client, pos):
        pos.exiting = False
        store.put(uid, pos)
        return False

    tick = pos.contract.tick_size
    price = align_to_tick(max(0.05, float(premium or pos.stop)), tick, side="sell")
    try:
        from app.services.snapback_family_mode import (
            INTENT_REDUCE, ReduceBound,
            canonical_broker_capability,
        )
        with canonical_broker_capability(
            f"intraday-exit:{getattr(client, '_account_id', '') or 'paper'}:"
            f"{pos.contract.tradingsymbol}:{pos.signal_id}",
            intent=INTENT_REDUCE,
            bound=ReduceBound.closing(exchange=pos.contract.exchange, symbol=pos.contract.tradingsymbol, side="SELL", quantity=pos.quantity),
        ):
            res = await client.place_order(
                f"{pos.contract.exchange}:{pos.contract.tradingsymbol}", "sell",
                float(pos.quantity), order_type="limit_order", limit_price=float(price),
                product="MIS", tag=f"{STRATEGY_ID[:14]}-exit")
        oid = str((res or {}).get("order_id") or "")
    except Exception as exc:                                       # noqa: BLE001
        pos.exiting = False
        store.put(uid, pos)
        # The stop went away to make room for this order. Put it back.
        await _place_protection(uid, client, pos, cfg)
        note(uid, "error", f"exit rejected for {pos.contract.tradingsymbol}: {exc}")
        return False
    if not oid:
        pos.exiting = False
        store.put(uid, pos)
        await _place_protection(uid, client, pos, cfg)
        return False

    # Persist the acknowledged SELL before waiting for fill evidence. A restart
    # can now reconcile this exact order instead of submitting a second exit.
    pos.exit_order_id = oid
    pos.exit_requested_qty = int(pos.quantity)
    pos.exit_requested_price = float(price)
    pos.exit_pending_kind = "exit"
    pos.exit_pending_reason = reason
    store.put(uid, pos)
    completed, _status = await _settle_pending_reduction(uid, client, pos, cfg)
    return completed


async def _scale_out(uid: str, client, pos: IntradayPosition, premium: float,
                     cfg: IntradayConfig) -> bool:
    """Bank half at the first target and let the rest run behind a breakeven stop.

    This is the 1:2-then-1:3 the board advertises. Without it the first target
    closed the whole position and the runner never existed — a UI claiming
    backend behaviour the backend does not honour, which is the failure this
    codebase keeps having to fix.

    A single-lot position cannot be halved, so it keeps its runner target and
    simply rides the trail. Attempting to sell half a lot gets a rejection at
    the exact moment it was trying to bank a win.
    """
    qty = pos.scale_out_qty(pos.contract.lot_size)
    if qty <= 0 or qty >= pos.quantity:
        # Nothing to bank. Mark it done so the stop still goes to breakeven and
        # the runner target becomes the live one.
        pos.target1_done = True
        pos.breakeven_done = True
        pos.stop = max(pos.stop, pos.effective_entry)
        store.put(uid, pos)
        await _sync_protection(uid, pos, cfg)
        return False
    price = align_to_tick(max(0.05, float(premium)), pos.contract.tick_size,
                          side="sell")
    broker_qty = await _broker_long_quantity(client, pos)
    if broker_qty is None or broker_qty < int(pos.quantity):
        note(uid, "warning",
             f"{pos.contract.tradingsymbol}: scale-out held — broker quantity "
             f"{broker_qty if broker_qty is not None else 'unknown'} is below "
             f"local quantity {pos.quantity}")
        return False
    if pos.exiting:
        return False
    pos.exiting = True
    store.put(uid, pos)
    if not await _release_protection_for_exit(uid, client, pos):
        pos.exiting = False
        store.put(uid, pos)
        return False
    try:
        from app.services.snapback_family_mode import (
            INTENT_REDUCE, ReduceBound,
            canonical_broker_capability,
        )
        with canonical_broker_capability(
            f"intraday-scale:{getattr(client, '_account_id', '') or 'paper'}:"
            f"{pos.contract.tradingsymbol}:{pos.signal_id}",
            intent=INTENT_REDUCE,
            bound=ReduceBound.closing(exchange=pos.contract.exchange, symbol=pos.contract.tradingsymbol, side="SELL", quantity=qty),
        ):
            res = await client.place_order(
                f"{pos.contract.exchange}:{pos.contract.tradingsymbol}", "sell",
                float(qty), order_type="limit_order", limit_price=float(price),
                product="MIS", tag=f"{STRATEGY_ID[:12]}-scale")
        oid = str((res or {}).get("order_id") or "")
    except Exception as exc:                                       # noqa: BLE001
        pos.exiting = False
        store.put(uid, pos)
        await _place_protection(uid, client, pos, cfg)
        note(uid, "error", f"scale-out rejected for {pos.contract.tradingsymbol}: {exc}")
        return False
    if not oid:
        pos.exiting = False
        store.put(uid, pos)
        await _place_protection(uid, client, pos, cfg)
        return False

    pos.exit_order_id = oid
    pos.exit_requested_qty = int(qty)
    pos.exit_requested_price = float(price)
    pos.exit_pending_kind = "scale"
    pos.exit_pending_reason = "target1"
    store.put(uid, pos)
    completed, _status = await _settle_pending_reduction(uid, client, pos, cfg)
    return completed


async def on_ticks(uid: str, ticks: list) -> str:
    """Trail and exit on live prices.

    This is where the trailing stop actually happens. A trail that only runs
    when a UI is open is not a trail, so this is driven by the ticker manager's
    broadcast rather than by anything the browser does.
    """
    open_now = store.open_positions(uid)
    if not open_now:
        return "idle"
    cfg = get_config(uid)
    by_token = {int(t.get("instrument_token") or 0): t for t in ticks or []}
    session_over = cfg.close_at_session_end and not _is_market_open(cfg)
    client = None
    acted = "watching"
    for pos in open_now:
        if pos.status == PENDING:
            # The broker may still fill more of the original BUY. Protection for
            # confirmed quantity stays live, but trailing/scale/exit logic must
            # not race that unresolved remainder.
            continue
        tick = by_token.get(int(pos.contract.token or 0))
        spot_tick = by_token.get(int(pos.underlying_token or 0))
        spot = float((spot_tick or {}).get("last_price") or 0.0) or None
        if not tick and not spot_tick and not session_over:
            continue
        ltp = float((tick or {}).get("last_price") or 0.0)
        if ltp > 0:
            if ltp > pos.peak:
                pos.peak = ltp
                store.put(uid, pos)
            new_stop, why = update_trail(pos, ltp, cfg)
            if new_stop > pos.stop:
                pos.stop = new_stop
                pos.breakeven_done = pos.breakeven_done or new_stop >= pos.effective_entry
                store.put(uid, pos)
                note(uid, "trail",
                     f"{pos.contract.tradingsymbol} stop to {new_stop} ({why})")
                await _sync_protection(uid, pos, cfg)
        if should_scale_out(pos, ltp):
            client = client or await _client(uid)
            if await _scale_out(uid, client, pos, ltp, cfg):
                acted = "scaled"
            continue
        done, reason = should_exit(pos, ltp, spot=spot, session_over=session_over)
        if not done:
            continue
        client = client or await _client(uid)
        # A session-end sweep can arrive with no tick for this contract. Pricing
        # the exit at the stop would send a limit nowhere near the market, so
        # the price is fetched rather than assumed.
        price = ltp or await _last_price(client, pos) or pos.stop
        if await _exit_position(uid, client, pos, reason, price, cfg):
            acted = "exited"
    if not store.open_positions(uid):
        await _subscribe(uid)
    return acted


async def check_rules(uid: str) -> int:
    """Close positions whose REASON for being open has gone.

    A stop answers "how much am I willing to lose"; this answers "is the idea
    still true". For two of these three the stated exit IS the idea failing —
    the ribbon strategy is held until the opposite full cross, and nothing but
    this evaluates that — so without this the trail was the only exit and a
    strategy was being traded that nobody wrote.

    Bar-close work, so it runs on the scan cadence rather than on ticks.
    """
    open_now = store.open_positions(uid)
    if not open_now:
        return 0
    cfg = get_config(uid)
    from app.engines.intraday import thesis_broken, to_bars
    from app.services.intraday import (LOOKBACK_BARS, _drop_forming, _inst,
                                       resample_for)
    client = None
    closed = 0
    for pos in open_now:
        if pos.strategy == "adopted":
            continue          # no rule to test — it was not this engine's setup
        try:
            client = client or await _client(uid)
            token = int(pos.underlying_token or 0)
            if token <= 0:
                continue       # no series to test it on; the price stop still holds
            raw = _drop_forming(
                await client.get_candles(_inst(pos.underlying, token),
                                         cfg.timeframe, LOOKBACK_BARS), cfg)
            if not raw:
                continue
            bars = to_bars(resample_for(raw, cfg))
            done, why = thesis_broken(bars, cfg, pos.strategy, pos.thesis)
        except Exception as exc:                                   # noqa: BLE001
            log.debug("intraday: rule check failed for %s: %s",
                      pos.contract.tradingsymbol, exc)
            continue
        if not done:
            # The thesis holds. Ratchet the SPOT stop, which is the trail these
            # strategies actually state — the premium trail protects the money,
            # this protects the idea, and neither replaces the other.
            moved, trail_why = spot_trail(pos, cfg, spot=float(bars.close[-1]),
                                          **_trail_inputs(bars, cfg))
            if moved != pos.spot_stop:
                pos.spot_stop = moved
                store.put(uid, pos)
                note(uid, "trail",
                     f"{pos.contract.tradingsymbol} spot stop to {moved} ({trail_why})")
            continue
        premium = await _last_price(client, pos)
        if await _exit_position(uid, client, pos, why, premium, cfg):
            closed += 1
    return closed


def _trail_inputs(bars, cfg: IntradayConfig) -> dict:
    """ATR, the last swing, and VWAP — whichever the trail mode needs.

    Computed once per position per bar rather than inside the trail, so the
    trail itself stays a pure function of numbers and can be tested without a
    bar series.
    """
    import numpy as np
    from app.engines.indicators import compute_atr
    from app.engines.intraday.indicators import session_vwap
    out: dict = {}
    try:
        atr = compute_atr(bars.high, bars.low, bars.close, cfg.pb_atr_length)
        out["atr"] = float(atr[-1]) if np.isfinite(atr[-1]) else 0.0
    except Exception:                                              # noqa: BLE001
        out["atr"] = 0.0
    try:
        look = max(3, int(cfg.pb_atr_length))
        out["swing"] = float(np.min(bars.low[-look:]))
    except Exception:                                              # noqa: BLE001
        out["swing"] = None
    try:
        vwap = session_vwap(bars.high, bars.low, bars.close, bars.volume,
                            bars.session_starts)
        out["vwap"] = float(vwap[-1])
    except Exception:                                              # noqa: BLE001
        out["vwap"] = None
    return out


async def exit_one(uid: str, symbol: str, reason: str = "manual") -> dict:
    """Close one position now. Available whatever the manual/auto setting says.

    Auto gates OPENING. An operator must always be able to close.
    """
    async with _lock_for(uid):
        pos = store.get(uid, symbol)
        if pos is None or not pos.is_open:
            return {"ok": False, "message": f"not holding {symbol}"}
        if pos.status == PENDING and pos.order_id:
            return {
                "ok": False,
                "message": (
                    f"entry order {pos.order_id} still has an unresolved remainder; "
                    "reconcile the BUY before exiting"
                ),
            }
        cfg = get_config(uid)
        client = await _client(uid)
        premium = await _last_price(client, pos)
        ok = await _exit_position(uid, client, pos, reason, premium, cfg)
        await _subscribe(uid)
        return {"ok": ok, "symbol": symbol}


async def square_off_all(uid: str, reason: str = "square off") -> dict:
    """Flatten only after reconciling all acknowledged broker orders.

    A session-end retry must poll a persisted SELL again instead of getting
    stuck behind exiting=True. A partial BUY with an unresolved remainder must
    also be cancelled/reconciled before any SELL can be submitted.
    """
    before = {p.contract.tradingsymbol for p in store.open_positions(uid)}
    reconcile_result = await reconcile(uid)
    after_reconcile = {p.contract.tradingsymbol for p in store.open_positions(uid)}
    async with _lock_for(uid):
        cfg = get_config(uid)
        client = await _client(uid)
        # A persisted SELL may have completed inside reconcile(). Count that as
        # closed by this square-off attempt even though no new SELL was sent.
        closed: list[str] = sorted(before - after_reconcile)
        blocked: list[dict[str, str]] = []
        for pos in store.open_positions(uid):
            symbol = pos.contract.tradingsymbol
            if pos.status == PENDING and pos.order_id:
                blocked.append({
                    "symbol": symbol,
                    "reason": "entry_reconciliation_pending",
                })
                continue
            if pos.exiting:
                # reconcile() above already polled the persisted reduction.
                # Still exiting means the exact SELL remains unresolved.
                blocked.append({
                    "symbol": symbol,
                    "reason": "exit_reconciliation_pending",
                })
                continue
            premium = await _last_price(client, pos)
            if await _exit_position(uid, client, pos, reason, premium, cfg):
                closed.append(symbol)
            else:
                current = store.get(uid, symbol)
                if current is not None and current.is_open:
                    blocked.append({
                        "symbol": symbol,
                        "reason": (
                            "exit_reconciliation_pending"
                            if current.exiting
                            else "exit_not_confirmed"
                        ),
                    })
        await _subscribe(uid)
        return {
            "ok": not blocked and not reconcile_result.get("error"),
            "closed": closed,
            "blocked": blocked,
            "reconcile_error": reconcile_result.get("error"),
        }

async def _last_price(client, pos: IntradayPosition) -> float:
    key = f"{pos.contract.exchange}:{pos.contract.tradingsymbol}"
    try:
        quotes = await client.get_quote([key]) or {}
        q = quotes.get(key) or quotes.get(pos.contract.tradingsymbol) or {}
        return float(q.get("last_price") or 0.0)
    except Exception:
        return 0.0


# ----------------------------------------------------------------- reconcile

async def reconcile(uid: str) -> dict:
    """Re-sync against the broker. The broker is right; we are wrong.

    A position we think we hold and Zerodha does not is CLOSED, not
    re-protected — re-protecting it would rest a sell order against lots that
    are not there, which is a naked short waiting for a trigger.
    """
    cfg = get_config(uid)
    restored = vanished = reprotected = 0
    gone: list[str] = []
    ownership_errors: list[str] = []
    try:
        client = await _client(uid)
    except Exception as exc:                                       # noqa: BLE001
        return {"error": str(exc), "restored": 0, "vanished": 0, "reprotected": 0}
    live: dict[tuple[str, str], int] = {}
    try:
        book = await client.get_positions()
        rows = book.get("net") or [] if isinstance(book, dict) else (book or [])
        for r in rows:
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
                if not exchange:
                    underlying = str(getattr(r, "underlying", "") or "")
                    exchange = underlying.split(":", 1)[0].strip().upper() if ":" in underlying else ""
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
    except Exception as exc:                                       # noqa: BLE001
        log.warning("intraday: reconcile could not read the broker for %s: %s", uid, exc)
        return {"error": str(exc), "restored": 0, "vanished": 0, "reprotected": 0}

    for pos in store.open_positions(uid):
        sym = pos.contract.tradingsymbol

        # Resume an acknowledged reduction before doing any ordinary position
        # reconciliation. While this is unresolved the old GTT is intentionally
        # not re-armed: the working SELL and a replacement SELL trigger could
        # otherwise both fill and create a naked short.
        if pos.exiting and pos.exit_order_id:
            try:
                await _settle_pending_reduction(uid, client, pos, cfg)
            except Exception as exc:                               # noqa: BLE001
                note(uid, "warning", f"{sym}: pending reduction reconcile failed — {exc}")
                continue
            current = store.get(uid, sym)
            if current is None or not current.is_open:
                vanished += 1
                gone.append(sym)
                continue
            pos = current
            if pos.exiting:
                restored += 1
                continue

        # A canonical ACK can survive a restart before its fill was settled.
        # Reconcile that order first; treating a PENDING row as an ordinary
        # holding would either close it because it is not in the position book
        # yet, or protect the requested quantity instead of the filled quantity.
        if pos.status == PENDING and pos.order_id:
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
                # A legacy pre-canonical pending row has no shared journal
                # identity. Let the older broker-position reconciliation below
                # handle it rather than inventing canonical ownership.
                if "canonical_intent_not_found" not in str(exc):
                    note(uid, "warning", f"{sym}: pending entry reconcile failed — {exc}")
                    continue
            except Exception as exc:                               # noqa: BLE001
                note(uid, "warning", f"{sym}: pending entry reconcile failed — {exc}")
                continue
            else:
                if resolution.filled_quantity > 0:
                    pos.quantity = int(resolution.filled_quantity)
                    pos.lots = max(
                        1,
                        pos.quantity // max(1, int(pos.contract.lot_size or 1)),
                    )
                    filled_pos = store.mark_filled(
                        uid,
                        sym,
                        resolution.average_price or pos.entry,
                    ) or pos
                    if not resolution.settled or resolution.reconciliation_required:
                        filled_pos.status = PENDING
                        store.put(uid, filled_pos)
                    if cfg.stop_mode != "monitor":
                        if await _ensure_protection_synced(uid, client, filled_pos, cfg):
                            reprotected += 1
                    if resolution.projection_version:
                        mark_fill_projected(resolution)
                    restored += 1
                    if not resolution.settled or resolution.reconciliation_required:
                        note(
                            uid,
                            "warning",
                            f"{sym}: protected confirmed partial fill; "
                            "canonical entry remains under reconciliation",
                        )
                    continue

                if resolution.projection_version:
                    mark_fill_projected(resolution)
                if resolution.terminal:
                    store.mark_rejected(uid, sym, resolution.status)
                    gone.append(sym)
                    vanished += 1
                else:
                    note(
                        uid,
                        "warning",
                        f"{sym}: entry still pending broker reconciliation; no stop sent",
                    )
                    restored += 1
                continue

        if not _position_account_matches(client, pos.account_id):
            ownership_errors.append(sym)
            note(uid, "warning",
                 f"{sym}: reconcile skipped — broker account ownership unverified")
            continue
        key = (str(pos.contract.exchange or "").strip().upper(), sym.strip().upper())
        if getattr(client, "_is_paper", None) is False and live.get(key, 0) <= 0:
            store.close(uid, sym, "not at the broker on reconcile")
            gone.append(sym)
            vanished += 1
            continue
        restored += 1
        if cfg.stop_mode != "monitor" and (
            not pos.gtt_id
            or int(pos.gtt_qty or 0) != int(pos.quantity)
            or abs(float(pos.stop) - float(pos.gtt_at or 0.0)) >= 0.05
        ):
            if await _ensure_protection_synced(uid, client, pos, cfg):
                reprotected += 1
    await _subscribe(uid)
    if restored or vanished or reprotected:
        note(uid, "reconcile",
             f"restored {restored}, {vanished} gone at the broker, "
             f"{reprotected} re-protected")
    return {"restored": restored, "vanished": vanished, "gone": gone,
            "reprotected": reprotected, "ownership_errors": ownership_errors}


def positions_view(uid: str) -> list[dict]:
    rows = []
    for pos in store.load(uid).values():
        d = pos.as_dict()
        d["broker_stop"] = bool(
            pos.gtt_id
            and int(pos.gtt_qty or 0) == int(pos.quantity)
            and abs(float(pos.gtt_at or 0.0) - float(pos.stop or 0.0)) < 0.05
        )
        d["broker_stop_qty"] = int(pos.gtt_qty or 0)
        rows.append(d)
    rows.sort(key=lambda r: (not r["is_open"], -int(r.get("entered_ms") or 0)))
    return rows


# ------------------------------------------------------------- the auto loop

def _kite_user_ids() -> list[str]:
    try:
        from app.services.exchanges.kite import accounts
        accounts.bootstrap()
        return sorted({a.user_id for a in accounts.all_accounts() if a.is_active})
    except Exception:
        return []


def auto_blocker_for(strategy: str) -> Optional[str]:
    """Whether this strategy may trade UNATTENDED, and why not if not.

    Manual arming is deliberately not gated on this. An operator taking an
    unproven setup with their eyes open is their call; refusing it would be
    paternalism rather than safety. Trading it while nobody is watching is a
    different thing, and that is what the harness has to earn.
    """
    try:
        from app.services.intraday_validation import auto_execution_blocker
        return auto_execution_blocker(strategy)
    except Exception as exc:                                       # noqa: BLE001
        # A validation record we cannot read means nothing is proven, which is
        # the safe reading.
        return f"validation record unreadable ({exc})"


async def _auto_enter(uid: str) -> int:
    """Take the armed rows, best reward-to-risk first, until a cap refuses.

    Stopping on the first cap rather than trying every row: the caps are about
    the account, not the row, so the next one would be refused for the same
    reason and each attempt costs a quote.
    """
    st = scan_status(uid)
    cfg = get_config(uid)
    armed = sorted(st.signals.values(),
                   key=lambda r: -float((r.get("signal") or {}).get("rr") or 0.0))
    taken = 0
    for row in armed:
        sid = row.get("signal_id")
        if not sid:
            continue
        # Ask the account-level gates BEFORE the row's own. They are about the
        # account, not the row, so once one refuses, the next row is refused for
        # the same reason and each attempt costs a quote. Matching on words in
        # an error message was the earlier version of this, and a reworded
        # blocker would have silently turned the cap off.
        if entry_blocker(uid, cfg, ""):
            break
        # Unattended execution is what the walk-forward harness gates. A
        # strategy that has not cleared it is skipped here and stays armable by
        # hand, which is the whole difference between the two.
        why = auto_blocker_for(str(row.get("strategy") or ""))
        if why:
            note(uid, "blocked", f"auto entry skipped: {why}")
            continue
        if (await arm(uid, sid)).get("ok"):
            taken += 1
    return taken


async def scan_all_once(*, respect_interval: bool = False, manage_only: bool = False) -> dict[str, str]:
    if _replay_owns_the_board():
        return {"*": "replay is driving this board — live scan is off"}
    uids = _kite_user_ids()
    if not uids:
        return {"*": "no active accounts"}
    from app.services.intraday import scan_once
    out: dict[str, str] = {}
    now_mono = time.monotonic()
    for uid in uids:
        cfg = get_config(uid)
        has_positions = bool(store.open_positions(uid))

        # Closing/maintaining owned risk is independent of whether NEW scans are
        # enabled. Disabling an intraday engine must never turn off its session
        # boundary while positions remain open.
        if not _is_market_open(cfg):
            out[uid] = "outside session"
            if cfg.close_at_session_end and has_positions:
                try:
                    result = await square_off_all(uid, "session end")
                    out[uid] = (
                        f"outside session; closed {len(result.get('closed') or [])}"
                    )
                except Exception as exc:                           # noqa: BLE001
                    out[uid] = f"outside session; square-off error: {exc}"
                    log.warning("intraday: session-end square-off failed for %s: %s",
                                uid, exc)
            continue

        if respect_interval:
            last = _last_auto_scan_monotonic.get(uid)
            due_after = max(1, int(cfg.scan_interval_seconds))
            if last is not None and now_mono - last < due_after:
                out[uid] = "waiting"
                continue
            _last_auto_scan_monotonic[uid] = now_mono

        try:
            maintenance_notes: list[str] = []
            if has_positions:
                rec = await reconcile(uid)
                if rec.get("error"):
                    maintenance_notes.append(f"reconcile: {rec['error']}")
                rule_closed = await check_rules(uid)
                if rule_closed:
                    maintenance_notes.append(f"{rule_closed} closed on rule")
                await _subscribe(uid)

            from app.services.archived_strategies import enabled as archived_enabled
            if manage_only or not cfg.enabled or not archived_enabled(uid, "intraday"):
                suffix = f"; {', '.join(maintenance_notes)}" if maintenance_notes else ""
                mode = "maintenance only" if manage_only else "disabled"
                out[uid] = f"{mode}; managing existing positions{suffix}"
                continue

            res = await scan_once(uid)
            note_text = f"{res.get('armed', 0)} armed of {res.get('scanned', 0)}"
            if maintenance_notes:
                note_text += ", " + ", ".join(maintenance_notes)
            if res.get("armed") and auto_execute(uid):
                note_text += f", auto-entered {await _auto_enter(uid)}"
            out[uid] = note_text
        except Exception as exc:                                   # noqa: BLE001
            out[uid] = f"error: {exc}"
            log.warning("intraday scan failed for %s: %s", uid, exc)
    return out


async def reconcile_all() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for uid in _kite_user_ids():
        try:
            out[uid] = await reconcile(uid)
        except Exception as exc:                                   # noqa: BLE001
            log.warning("intraday reconcile failed for %s: %s", uid, exc)
    return out


async def auto_scan_loop(interval: int = 60, *, manage_only: bool = False) -> None:
    """Run the strategy cadence or maintenance-only retirement mode.

    Reconciles against the broker BEFORE the first cycle. In manage-only mode
    existing positions keep exits, reconciliation and session-end safety, but
    no new Intraday scan or entry can start.
    """
    log.info(
        "intraday %s loop started (every %ss)",
        "maintenance" if manage_only else "auto-scan",
        interval,
    )
    try:
        await reconcile_all()
    except Exception as exc:                                       # noqa: BLE001
        log.warning("intraday startup reconcile failed: %s", exc)
    while True:
        try:
            # Per-user cadence is enforced inside scan_all_once. Polling at a
            # short fixed scheduler tick avoids one tenant's config deciding
            # every other tenant's scan interval.
            await scan_all_once(respect_interval=True, manage_only=manage_only)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                                   # noqa: BLE001
            log.warning("intraday auto-scan cycle failed: %s", exc)
        await asyncio.sleep(max(5, min(30, int(interval))))
