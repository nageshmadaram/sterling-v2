"""Open auto-exec option positions registry (workstreams E / C / D / F).

A small, DB-persisted store of the option positions auto-exec has opened, plus a
pure exit predicate. It is the shared source of truth for three consumers:

  * the WS order-update handler (E) — confirms fills, stamps the real fill price
    and COMPLETE/REJECTED status instead of assuming success;
  * the tick monitor (C/D) — reads each open position's current trail and exits
    intrabar when the premium breaches it;
  * risk sizing (F) — records the sized qty / premium-at-risk for the order.

Persisted (``kite_engine_positions_{uid}``) so a restart rehydrates open positions
and the monitor can keep guarding them. Pure functions where possible; the store
holds no broker handles and makes no network calls.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict, field, fields
from typing import Dict, List, Optional

from app.core.logging import get_logger
from app.services import db
from app.services import trade_journal_adapters as _journal

log = get_logger(__name__)


# Lifecycle of an auto-exec position.
PENDING = "pending"     # entry order placed, fill not yet confirmed
OPEN = "open"           # fill confirmed (or assumed), guarding active
CLOSED = "closed"       # exited (stop hit / manual / trail breach)
REJECTED = "rejected"   # broker rejected/cancelled the entry order


@dataclass
class OpenPosition:
    uid: str
    symbol: str                 # option tradingsymbol or futures tradingsymbol
    exchange: str               # NFO / BFO
    token: int = 0              # instrument token (for tick subscription)
    qty: int = 0                # total quantity (lots × lot_size)
    lot_size: int = 0
    entry_premium: float = 0.0  # intended entry (scan-time premium or futures price)
    stop_premium: float = 0.0   # current trail level (premium ST trail or futures stop)
    order_id: str = ""
    fill_price: float = 0.0     # actual avg fill (from WS order update)
    status: str = PENDING
    gtt_id: int = 0             # broker-side protective stop id (0 = none)
    stop_mode: str = "both"     # broker | monitor | both
    guard_key: str = ""         # the state.auto_open slot this position guards
    direction: str = "long"     # "long" | "short" — options are always long; futures can be either
    #: Direction of the SIGNAL that opened this position, which is NOT ``direction``.
    #: Buying a PE on a BEAR signal is a LONG position in premium space but a SHORT
    #: signal, and the red counter is defined against the signal — feeding it
    #: ``direction`` made every bear position read 3-of-3 red at entry and exit on the
    #: next tick. For a derivatives-source row the ST runs on the contract's own premium
    #: series, so the signal really is "long" even for a PE; that is why this is recorded
    #: at entry rather than guessed from the CE/PE suffix.
    #:
    #: Empty means UNKNOWN, not "long". Positions are persisted with ``asdict`` and read
    #: back with ``OpenPosition(**d)``, so every row written before this field existed
    #: reloads without it — and defaulting those to "long" would hand a surviving bear
    #: position the exact wrong-way count this field was added to prevent. Read it
    #: through ``signal_direction_of``.
    signal_direction: str = ""
    vehicle: str = "otm_options" # otm_options | deep_itm_options | futures
    underlying: str = ""        # display underlying ("NIFTY 50") — for correlation grouping
    opened_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    closed_ms: int = 0
    exit_reason: str = ""
    exit_mode: str = "one_red"  # exit counter chosen at entry time (one_red/two_red/three_red/three_red_signal)
    current_red_count: int = 0  # latest computed red ST lines against this position (updated on scans)
    counter_signal_fired: bool = False  # fresh counter-signal observed for this underlying (for three_red_signal exit)
    #: When ``current_red_count`` was last refreshed from a live scan row. The count is
    #: only as good as its age: when the signal that opened a position ends and no row of
    #: that direction is emitted again, there is nothing to refresh it from and it holds
    #: its last value forever. Leaving it alone is the safe direction — inventing a 0
    #: would disarm the exit — but a counter that silently stopped counting must not look
    #: like a working one.
    red_count_ms: int = 0
    # ── delta-translation context (workstream: spot-mode + deep-ITM premium stop) ──
    # For option vehicles the protective stop lives in PREMIUM space but the signal's
    # trail lives in UNDERLYING space. We store the entry underlying spot + the BS
    # delta so every trailing update can re-translate the fresh underlying ST level
    # into a premium stop (``greeks.premium_stop_from_move``) — both at entry and each
    # scan. 0.0 delta means "no translation context" (e.g. futures, which trail in
    # index points directly).
    entry_spot: float = 0.0     # underlying price at entry (index level)
    entry_delta: float = 0.0    # |BS delta| of the held option at entry
    strike: float = 0.0         # option strike (for re-pricing / display)
    expiry: str = ""            # option expiry "YYYY-MM-DD" (for the expiry square-off guard)
    initial_stop_premium: float = 0.0  # first stop set at entry — the step-out floor (never risk more than this)
    #: Premium at which this position books profit, or 0.0 when the signal has no
    #: target. Only Navigator originations carry one (from the AVWAP proposal); a
    #: SuperTrend row has no target by design — it exits on the trail or the red
    #: counter. Enforced BROKER-side as the second leg of an OCO GTT, so the
    #: exchange cancels the stop when the target fills and vice versa.
    target_premium: float = 0.0
    #: order_id → quantity, one entry per entry order that makes up this holding.
    #: A second hand-placed buy of a contract we already hold is a SCALE-IN: we end
    #: up holding the SUM. This registry is keyed on symbol, so without a per-order
    #: breakdown the row would carry only the newest order's qty while we hold more —
    #: and ``qty`` is what every exit SELL, GTT quantity and PnL figure is derived
    #: from, so the difference is a lot with no stop on it. Empty on legacy rows,
    #: which keep the old single-order behaviour.
    qty_by_order: Dict[str, int] = field(default_factory=dict)
    #: Whether this position's realized PnL has already been added to the day's total.
    #:
    #: Two independent paths close a position and book it: the monitor's own
    #: ``_exit_position`` and the ``on_order_update`` reconciliation of a fill that
    #: arrived from the broker. Each guards on ``status``, which is not enough — the
    #: monitor sets CLOSED only AFTER its order returns, so a fill postback landing
    #: inside that await sees a live position and books it, and the monitor then books
    #: it again. Ordering the two is fragile (either can legitimately be first), so the
    #: booking itself is made exactly-once instead. Persisted, so a restart mid-exit
    #: cannot re-book. Legacy rows load as False, which is correct: booking only ever
    #: happens at exit, and a position that already closed is never exited again.
    realized_booked: bool = False
    pnl_reconciliation_required: bool = False
    exit_tag: str = ""
    exit_requested_ms: int = 0
    exit_requested_qty: int = 0   # immutable quantity of the in-flight exit request
    exit_order_id: str = ""       # submitting/unknown/actual ID; never implies filled
    exit_fills: Dict[str, int] = field(default_factory=dict)  # cumulative broker fills
    account_id: str = ""
    # Broker inventory bucket, persisted with the position. NRML is only the
    # historical default for rows written before product was recorded; an
    # explicitly empty/unknown value must never become NRML during a live write.
    product: str = "NRML"
    entry_requested_qty: int = 0
    entry_pending: bool = False
    protection_pending: bool = False  # unknown GTT submission outcome: never retry blindly
    protection_pending_ms: int = 0

    # derived threshold from exit_mode for convenience in responses/UI


class RegistryUnreadable(RuntimeError):
    """The stored registry exists but could not be read.

    Distinct from "no positions": callers must fail closed on it rather than
    treating the account as flat.
    """

    def __init__(self, uid: str) -> None:
        super().__init__(f"position_registry_unreadable:{uid}")
        self.uid = uid


_positions: Dict[str, Dict[str, OpenPosition]] = {}   # uid → {symbol → OpenPosition}
_unreadable: Dict[str, str] = {}                      # uid → why the registry failed to load


def broker_product(value: object) -> str:
    """Return a known broker product, or empty for unverified identity."""
    value = str(value or "").strip().upper()
    return value if value in {"MIS", "NRML", "CNC"} else ""


# ── pure exit predicate ──────────────────────────────────────────────────────
def signal_direction_of(p: OpenPosition) -> str:
    """The direction the red counter must be evaluated against for ``p``.

    Prefers what was recorded at entry. Falls back to the CE/PE suffix only for a
    position that predates the field (persisted before it existed), because there the
    alternative — assuming "long" — reproduces the very defect the field prevents: a
    surviving bear position would match the BULL row and read every down-trend as a red
    against itself.

    The suffix is a fallback, never the source of truth: a derivatives-source row runs
    the SuperTrend on the contract's OWN premium series, so a PE bought there really is a
    long signal and this would call it short. That mistake costs a red exit that never
    fires (the price trail and the expiry square-off still run) rather than a position
    sold while the trend is still with it — the same asymmetry this engine takes
    everywhere.
    """
    recorded = str(getattr(p, "signal_direction", "") or "").strip().lower()
    if recorded:
        return recorded
    sym = str(getattr(p, "symbol", "") or "").upper()
    if sym.endswith("CE"):
        return "long"
    if sym.endswith("PE"):
        return "short"
    return str(getattr(p, "direction", "long") or "long")  # futures trail in index points


def should_exit(stop_premium: float, ltp: float, direction: str = "long") -> bool:
    """True when a position's live price has breached its trail.

    For long positions (options + long futures): exit when LTP ≤ stop (downside).
    For short futures: exit when LTP ≥ stop (upside, buy-to-cover).
    A non-positive stop means 'no stop set' → never auto-exit on it.
    A non-positive ltp is treated as a stale tick and ignored.
    A non-positive or NaN ltp is treated as a stale tick and ignored.
    """
    import math
    if not math.isfinite(stop_premium) or not math.isfinite(ltp) or stop_premium <= 0 or ltp <= 0:
        return False
    if direction == "short":
        return ltp >= stop_premium
    return ltp <= stop_premium


# ── persistence ──────────────────────────────────────────────────────────────
def _load(uid: str) -> Dict[str, OpenPosition]:
    if uid not in _positions:
        out: Dict[str, OpenPosition] = {}
        try:
            raw = db.get_config_strict(f"kite_engine_positions_{uid}")
            if raw:
                known = {f.name for f in fields(OpenPosition)}
                decoded = json.loads(raw)
                if not isinstance(decoded, list):
                    raise ValueError("position_registry_not_a_list")
                for index, d in enumerate(decoded):
                    # Unknown fields are rollback-compatible: an older build can
                    # ignore fields written by a newer one. Structural corruption is
                    # different. Silently skipping one malformed row asserts that the
                    # corresponding live position does not exist, frees its guard and
                    # can leave broker protection unmanaged. Fail the whole registry
                    # closed instead.
                    if not isinstance(d, dict):
                        raise ValueError(f"invalid_position_row:{index}:not_object")
                    try:
                        p = OpenPosition(**{k: v for k, v in d.items() if k in known})
                    except Exception as exc:  # noqa: BLE001
                        raise ValueError(
                            f"invalid_position_row:{index}:{type(exc).__name__}"
                        ) from exc
                    if p.uid != uid or not str(p.symbol or "").strip() or not str(p.exchange or "").strip():
                        raise ValueError(f"invalid_position_identity:{index}")
                    if isinstance(p.qty, bool) or not isinstance(p.qty, int) or p.qty < 0:
                        raise ValueError(f"invalid_position_quantity:{index}")
                    if p.symbol in out:
                        raise ValueError(f"duplicate_position_symbol:{p.symbol}")
                    out[p.symbol] = p
        except Exception as exc:  # noqa: BLE001
            # A registry we could not READ must never present itself as an empty
            # one. Empty is a positive claim — "you hold nothing" — and acting on
            # it leaves every live position unguarded and frees the auto-open
            # guards to re-enter slots that are already held. The per-row guard
            # above covers one bad row; this covers a truncated blob or a failed
            # read, where nothing at all is known.
            # Scoped to this account rather than tripping the global kill switch:
            # one corrupt blob must stop THIS account trading, not halt every engine
            # for every user.
            _unreadable[uid] = str(exc) or exc.__class__.__name__
            log.error("kite positions: registry for %s is UNREADABLE: %s", uid, exc)
            raise RegistryUnreadable(uid) from exc
        _unreadable.pop(uid, None)
        _positions[uid] = out
    return _positions[uid]


def unreadable_reason(uid: str) -> str:
    """Why this user's registry could not be read, or empty when it is fine."""
    return _unreadable.get(uid, "")


def _persist(uid: str) -> None:
    if uid in _unreadable:
        # Writing what we have in memory over a blob we failed to parse would
        # destroy the only copy of the positions we cannot currently see.
        log.error("kite positions: refusing to overwrite the unreadable registry for %s", uid)
        return
    try:
        rows = [asdict(p) for p in _positions.get(uid, {}).values()]
        db.set_config(f"kite_engine_positions_{uid}", json.dumps(rows))
    except Exception as exc:  # noqa: BLE001 - legacy best-effort callers exist
        # Best-effort callers are deliberately not made fatal here, but a failed
        # live-state write must never disappear silently. Critical broker-derived
        # mutations immediately follow this with persist_strict().
        log.error("kite positions: best-effort persistence failed for %s: %s", uid, exc)


def persist_strict(uid: str) -> None:
    """Durably project live execution state; failure must block follow-on effects."""
    if not db.is_available():
        raise RuntimeError("position_store_unavailable")
    rows = [asdict(p) for p in _load(uid).values()]
    if uid in _unreadable:
        raise RegistryUnreadable(uid)
    with db._conn() as conn:
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("INSERT OR REPLACE INTO system_config (key, value) VALUES (?, ?)",
                     (f"kite_engine_positions_{uid}", json.dumps(rows, allow_nan=False)))


# ── registry API ─────────────────────────────────────────────────────────────
def register(p: OpenPosition) -> OpenPosition:
    """Record a newly-placed entry order (status pending until a fill confirms)."""
    _load(p.uid)[p.symbol] = p
    _persist(p.uid)
    return p


def get(uid: str, symbol: str) -> Optional[OpenPosition]:
    return _load(uid).get(symbol)


def open_positions(uid: str) -> List[OpenPosition]:
    """Positions still being guarded (pending or open)."""
    return [p for p in _load(uid).values() if p.status in (PENDING, OPEN)]


def known_uids() -> List[str]:
    """Every operator whose registry has been persisted, read from the store.

    The in-memory registry belongs to one process; a shutdown check that read it
    from a command-line process would find nothing and report "no exposure" for a
    machine with open positions. This reads the durable projection instead, so
    the answer is about the deployment rather than about the caller.
    """
    prefix = "kite_engine_positions_"
    try:
        with db._conn() as conn:
            rows = conn.execute(
                "SELECT key FROM system_config WHERE key LIKE ?", (prefix + "%",)
            ).fetchall()
    except Exception:  # noqa: BLE001
        # An unreadable store is not an empty one. The caller must treat this as
        # unknown, and a raise is the only answer that cannot be mistaken for "none".
        raise
    return sorted({str(r[0])[len(prefix):] for r in rows if str(r[0]).startswith(prefix)})


def all_open_tokens(uid: str) -> List[int]:
    return [p.token for p in open_positions(uid) if p.token]


def mark_filled(uid: str, symbol: str, fill_price: float,
                filled_qty: int = 0, order_id: str = "") -> Optional[OpenPosition]:
    """Confirm a fill. ``filled_qty``, when supplied by the broker postback, becomes
    the quantity of the order it belongs to.

    The quantity matters as much as the price: every downstream number is derived
    from ``qty`` — the exit SELL, the GTT's order quantity, and the realized PnL. If
    2 of 3 intended lots fill and we keep believing we hold 3, both the broker stop
    and the monitor try to sell 3, and the extra lot is a naked short.

    ``order_id`` is what makes that correction safe on a scaled-in holding: the
    postback reports only ITS order's filled quantity, so writing it straight into
    ``qty`` would silently forget every other lot. With the id we correct that lot
    and re-total. Absent an id (legacy postbacks) the old whole-position behaviour
    stands.
    """
    p = _load(uid).get(symbol)
    if p is None:
        return None
    was_status = p.status
    was_qty = int(p.qty or 0)
    was_fill = float(p.fill_price or 0.0)
    p.status = OPEN
    if fill_price > 0:
        p.fill_price = float(fill_price)
    if filled_qty > 0:
        oid = str(order_id or "")
        if oid and p.qty_by_order:
            p.qty_by_order[oid] = int(filled_qty)   # never drops the other lots
            p.qty = sum(int(v) for v in p.qty_by_order.values())
        elif oid:
            p.qty_by_order = {oid: int(filled_qty)}
            p.qty = int(filled_qty)
        else:
            p.qty = int(filled_qty)
    _persist(uid)
    # Only an entry that actually progressed is an "entry fill". Reconciling DOWN to
    # what the broker still holds (a partial exit elsewhere) also lands here with a
    # smaller quantity; announcing that as an entry would tell the operator a fresh
    # position had filled and re-stamp nothing but confusion.
    now_qty = int(p.qty or 0)
    price_moved = abs(was_fill - float(p.fill_price or 0.0)) >= 0.01
    entry_progressed = (
        was_status != OPEN
        or now_qty > was_qty
        or (now_qty == was_qty and price_moved)
    )
    if entry_progressed:
        from app.services.kite_engine import lifecycle_alerts as _alerts
        identity = str(order_id or p.order_id or p.opened_ms)
        _journal.st_filled(uid, p, order_id)
        fill = float(p.fill_price or p.entry_premium or 0.0)
        _alerts.emit(
            uid,
            dedupe_key=f"supertrend:entry:{p.symbol}:{identity}:{now_qty}:{fill:.2f}",
            event_type="ENTRY FILLED",
            title=p.symbol,
            detail=_alerts.build_detail(entry=fill, sl=p.stop_premium, qty=now_qty),
            created_at_ms=int(p.opened_ms or 0) or None,
        )
    return p


def mark_rejected(uid: str, symbol: str, reason: str = "") -> Optional[OpenPosition]:
    p = _load(uid).get(symbol)
    if p is None:
        return None
    p.status = REJECTED
    p.exit_reason = reason or "broker rejected/cancelled"
    p.closed_ms = int(time.time() * 1000)
    _persist(uid)
    _journal.st_rejected(uid, p, p.exit_reason)
    return p


def update_stop(uid: str, symbol: str, stop_premium: float, gtt_id: Optional[int] = None,
                *, notify: bool = True) -> Optional[OpenPosition]:
    """Tighten (trail) or set the stop for a held position. The stop only ratchets
    in the protective direction so the monitor never relaxes protection mid-trade:
    UP for a long (option or long future), DOWN for a short future. A looser new
    trail is ignored.

    ``notify=False`` leaves the Telegram alert to the caller. The trail updater uses
    it: the alert must reflect whether the BROKER stop actually moved, which is only
    known after the GTT modify returns, and queueing "TSL UPDATED ... GTT #n" first
    told the operator a stop had moved that Zerodha still held at the old level.
    """
    p = _load(uid).get(symbol)
    if p is None:
        return None
    old_stop = float(p.stop_premium or 0.0)
    old_gtt = int(p.gtt_id or 0)
    if p.direction == "short":
        # short future: the protective stop sits ABOVE price → ratchets DOWN.
        if stop_premium > 0 and (p.stop_premium <= 0 or stop_premium < p.stop_premium):
            p.stop_premium = float(stop_premium)
    elif stop_premium > p.stop_premium:
        # long (option or long future): the stop sits BELOW price → ratchets UP.
        p.stop_premium = float(stop_premium)
    if gtt_id is not None:
        p.gtt_id = int(gtt_id)
    _persist(uid)
    new_stop = float(p.stop_premium or 0.0)
    _journal.st_stop(uid, p, old_stop, old_gtt)
    if notify and new_stop > 0 and abs(new_stop - old_stop) >= 0.01:
        from app.services.kite_engine import lifecycle_alerts as _alerts
        _alerts.emit(
            uid,
            dedupe_key=stop_alert_key(p, new_stop),
            event_type="TSL UPDATED" if old_stop > 0 else "SL ARMED",
            title=p.symbol,
            detail=_alerts.build_detail(
                tsl=new_stop if old_stop > 0 else None, tsl_from=old_stop or None,
                sl=None if old_stop > 0 else new_stop, qty=p.qty,
                notes=[f"GTT #{int(p.gtt_id)}"] if p.gtt_id else ()),
        )
    return p


def stop_alert_key(p: "OpenPosition", stop: float) -> str:
    """Dedupe identity of a stop alert: this POSITION at this level.

    Scoped by ``opened_ms``: the outbox keeps completed rows for days, so a re-entry
    into the same contract at the same stop level would otherwise share a key with the
    earlier trade and its alert would be dropped as a duplicate.
    """
    return f"supertrend:stop:{p.symbol}:{int(p.opened_ms or 0)}:{float(stop):.2f}:{int(p.gtt_id or 0)}"


def update_health(
    uid: str, symbol: str, red_count: int,
    exit_mode: Optional[str] = None, counter_signal_fired: Optional[bool] = None,
) -> Optional[OpenPosition]:
    """Update live red count health for a position (from scan regime). Persisted for UI."""
    p = _load(uid).get(symbol)
    if p is None:
        return None
    p.current_red_count = max(0, int(red_count))
    p.red_count_ms = int(time.time() * 1000)
    if exit_mode:
        p.exit_mode = exit_mode
    if counter_signal_fired is not None:
        p.counter_signal_fired = bool(counter_signal_fired)
    _persist(uid)
    return p


def claim_realized(uid: str, symbol: str) -> bool:
    """Claim the right to book this position's realized PnL. True for the first
    caller only.

    Check-and-set is atomic here because the event loop is single-threaded and there
    is no await between the read and the write — the same reason ``monitor._exiting``
    can be a plain set. An unknown symbol returns False: there is no position to
    attribute a booking to.
    """
    p = _load(uid).get(symbol)
    if p is None or getattr(p, "realized_booked", False):
        return False
    p.realized_booked = True
    _persist(uid)
    return True


def close(uid: str, symbol: str, reason: str = "", *, exit_price: float = 0.0) -> Optional[OpenPosition]:
    """Mark a position closed. ``exit_price`` (average premium per unit, when the broker
    reported one) lets the alert state the exit and the realized P&L; without it the
    alert says so rather than guessing a figure."""
    p = _load(uid).get(symbol)
    if p is None:
        return None
    was_closed = p.status == CLOSED
    p.status = CLOSED
    p.exit_reason = reason or "closed"
    if not p.closed_ms:
        p.closed_ms = int(time.time() * 1000)
    _persist(uid)
    if not was_closed:
        _journal.st_closed_fallback(uid, p, exit_price, p.exit_reason)
        from app.services.kite_engine import lifecycle_alerts as _alerts
        entry = float(p.fill_price or p.entry_premium or 0.0)
        pnl = _alerts.realized_pnl(entry, exit_price, p.qty, p.direction)
        text = str(p.exit_reason or "")
        stop_out = any(w in text.lower() for w in ("stop", "breach", "trail", "gtt"))
        # What ended the trade, in the board's vocabulary: SL until the stop has
        # trailed, TSL after. The free-text reason names the mechanism (GTT, tick).
        label = f"{_alerts.stop_kind(p)} hit" if stop_out else ""
        _alerts.emit(
            uid,
            dedupe_key=f"supertrend:exit:{p.symbol}:{int(p.opened_ms or 0)}:{int(p.closed_ms)}",
            event_type="POSITION CLOSED",
            title=p.symbol,
            detail=_alerts.build_detail(
                entry=entry, exit=exit_price or None, pnl=pnl, qty=p.qty,
                reason=(f"{label} · {text}" if label and text else (label or text))),
            severity="WARNING" if (stop_out or (pnl is not None and pnl < 0)) and not (
                pnl is not None and pnl > 0) else "INFO",
            created_at_ms=int(p.closed_ms),
        )
    return p


def reset(uid: str = "") -> None:
    """Test helper."""
    if uid:
        _positions.pop(uid, None)
        _unreadable.pop(uid, None)
    else:
        _positions.clear()
        _unreadable.clear()
