"""The Trade Journal: one durable row per trade, a timeline of what happened, and the signals
the strategies declined, for LIVE and SIMULATION trades alike.

Three SQLite tables in the application database (created on first use):

* ``trade_journal_trades``  one row per trade (``trade_id``), every number a person asks
  about (entry, stop, target, trail, exit, gross/net P&L, R-multiple, hold time) plus the
  signal context that made the strategy enter and a plain-English narrative;
* ``trade_journal_events``  the ordered timeline (signal, order, fill, protection, trail,
  target, exit...), unique per ``(trade_id, event_key)`` so a replayed event never doubles;
* ``trade_journal_skips``   signals the strategy declined or was blocked on, one row per
  ``(day, uid, strategy, signal, symbol, code)`` with a counter.

Contract with the trading code:

* **It can never break a trade.** Every public ``record_*`` function swallows and logs its own
  errors (see :func:`_guarded`) and returns ``None`` instead of raising. It never touches a
  broker, a position registry or a lock of the trading code.
* **It is idempotent.** Rows are keyed deterministically (:func:`trade_id_for`), events by a
  natural key, so a restart, a replayed postback or two code paths reporting the same fact
  produce one row and one event.
* **Mode follows the uid.** ``mode`` is ``identity.mode_for_uid(uid)``: the simulation tenant
  runs the very same hooks and its rows are labelled SIMULATION, never mixed into LIVE.

Strategy-specific translation (positions -> journal calls) lives in
``trade_journal_adapters``; the pure maths in ``trade_journal_stats`` and the wording in
``trade_journal_narrative``.
"""
from __future__ import annotations

import functools
import hashlib
import json
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from app.core.logging import get_logger
from app.services import db
from app.services.simulation import identity
from app.services.trade_journal_narrative import (
    CANONICAL_EXIT_REASONS, EXIT_REASON_LABELS, STRATEGY_LABELS, build_narrative,
    canonical_exit_reason, reason_for_code,
)

log = get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
MODE_LIVE, MODE_SIMULATION = identity.MODE_LIVE, identity.MODE_SIMULATION

STRATEGIES = ("supertrend", "snapback", "gamma")
_ID_PREFIX = {"supertrend": "ST", "snapback": "SB", "gamma": "GU"}

PENDING, OPEN, CLOSED, REJECTED = "PENDING", "OPEN", "CLOSED", "REJECTED"
_RANK = {PENDING: 0, OPEN: 1, CLOSED: 2, REJECTED: 2}

# Event types (the timeline vocabulary).
SIGNAL, ORDER_SUBMITTED, FILLED, PROTECTION_PLACED = "SIGNAL", "ORDER_SUBMITTED", "FILLED", "PROTECTION_PLACED"
TSL_MOVED, TARGET_HIT, EXIT_SUBMITTED, EXIT_FILLED = "TSL_MOVED", "TARGET_HIT", "EXIT_SUBMITTED", "EXIT_FILLED"
PARTIAL_EXIT, HEDGE_FILLED, FAILED, NOTE = "PARTIAL_EXIT", "HEDGE_FILLED", "FAILED", "NOTE"
EVENT_TYPES = (SIGNAL, ORDER_SUBMITTED, FILLED, PROTECTION_PLACED, TSL_MOVED, TARGET_HIT,
               EXIT_SUBMITTED, EXIT_FILLED, PARTIAL_EXIT, HEDGE_FILLED, REJECTED, FAILED, NOTE)

# --------------------------------------------------------------------------
# Never-raises plumbing
# --------------------------------------------------------------------------

_warned: Dict[str, float] = {}
_WARN_EVERY_S = 60.0


def _warn_once(name: str, exc: BaseException) -> None:
    key = f"{name}:{type(exc).__name__}:{exc}"
    now = time.monotonic()
    if now - _warned.get(key, -1e9) >= _WARN_EVERY_S:
        _warned[key] = now
        log.warning("trade journal: %s failed (trading is unaffected): %s: %s",
                    name, type(exc).__name__, exc)


def _guarded(default: Any = None) -> Callable:
    """Make a ``record_*`` function incapable of raising into the trading path."""
    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - the journal must never break a trade
                _warn_once(fn.__name__, exc)
                return default
        return wrapper
    return deco


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trade_journal_trades (
    trade_id        TEXT PRIMARY KEY,
    mode            TEXT NOT NULL,
    strategy        TEXT NOT NULL,
    uid             TEXT NOT NULL,
    account_id      TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    underlying      TEXT NOT NULL DEFAULT '',
    option_type     TEXT NOT NULL DEFAULT '',
    strike          REAL,
    expiry          TEXT NOT NULL DEFAULT '',
    side            TEXT NOT NULL DEFAULT '',
    lot_size        INTEGER,
    lots            INTEGER,
    qty             INTEGER,
    signal_id       TEXT NOT NULL DEFAULT '',
    entry_order_id  TEXT NOT NULL DEFAULT '',
    signal_ts_ms    INTEGER,
    entry_ts_ms     INTEGER,
    entry_price     REAL,
    initial_sl      REAL,
    target          REAL,
    current_sl      REAL,
    tsl_history     TEXT NOT NULL DEFAULT '[]',
    exit_ts_ms      INTEGER,
    exit_price      REAL,
    exit_qty        INTEGER,
    exit_reason     TEXT NOT NULL DEFAULT '',
    exit_reason_raw TEXT NOT NULL DEFAULT '',
    gross_pnl       REAL,
    charges         REAL,
    charges_source  TEXT NOT NULL DEFAULT '',
    net_pnl         REAL,
    r_multiple      REAL,
    hold_minutes    REAL,
    mae             REAL,
    mfe             REAL,
    capital_used    REAL,
    signal_context  TEXT NOT NULL DEFAULT '{}',
    extra           TEXT NOT NULL DEFAULT '{}',
    narrative       TEXT NOT NULL DEFAULT '',
    source          TEXT NOT NULL DEFAULT 'journal',
    entry_day       TEXT NOT NULL DEFAULT '',
    exit_day        TEXT NOT NULL DEFAULT '',
    trade_day       TEXT NOT NULL DEFAULT '',
    created_ms      INTEGER NOT NULL,
    updated_ms      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tj_trades_uid_status ON trade_journal_trades(uid, status);
CREATE INDEX IF NOT EXISTS ix_tj_trades_uid_day ON trade_journal_trades(uid, trade_day);
CREATE INDEX IF NOT EXISTS ix_tj_trades_strategy ON trade_journal_trades(strategy);
CREATE INDEX IF NOT EXISTS ix_tj_trades_symbol ON trade_journal_trades(symbol);

CREATE TABLE IF NOT EXISTS trade_journal_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id    TEXT NOT NULL,
    event_key   TEXT NOT NULL,
    ts_ms       INTEGER NOT NULL,
    event_type  TEXT NOT NULL,
    price       REAL,
    qty         INTEGER,
    pnl         REAL,
    detail      TEXT NOT NULL DEFAULT '',
    data        TEXT NOT NULL DEFAULT '{}',
    UNIQUE(trade_id, event_key)
);
CREATE INDEX IF NOT EXISTS ix_tj_events_trade ON trade_journal_events(trade_id, ts_ms);

CREATE TABLE IF NOT EXISTS trade_journal_skips (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    day         TEXT NOT NULL,
    mode        TEXT NOT NULL,
    strategy    TEXT NOT NULL,
    uid         TEXT NOT NULL,
    symbol      TEXT NOT NULL DEFAULT '',
    underlying  TEXT NOT NULL DEFAULT '',
    signal_id   TEXT NOT NULL DEFAULT '',
    code        TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    context     TEXT NOT NULL DEFAULT '{}',
    count       INTEGER NOT NULL DEFAULT 1,
    first_ms    INTEGER NOT NULL,
    last_ms     INTEGER NOT NULL,
    UNIQUE(day, uid, strategy, signal_id, symbol, code)
);
CREATE INDEX IF NOT EXISTS ix_tj_skips_uid_day ON trade_journal_skips(uid, day);
"""

_ready_paths: set = set()


def _ensure_schema(conn: Any) -> None:
    path = str(getattr(db, "_DB_PATH", ""))
    if path in _ready_paths:
        return
    for statement in [s.strip() for s in _SCHEMA.split(";") if s.strip()]:
        conn.execute(statement)
    conn.commit()
    _ready_paths.add(path)


class JournalUnavailable(RuntimeError):
    pass


@contextmanager
def _write():
    """One write transaction (``BEGIN IMMEDIATE``), so read-modify-write is atomic."""
    if not db.is_available():
        raise JournalUnavailable("database_unavailable")
    with db.connection() as conn:
        _ensure_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        yield conn


@contextmanager
def _read():
    if not db.is_available():
        raise JournalUnavailable("database_unavailable")
    with db.connection() as conn:
        _ensure_schema(conn)
        yield conn


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def _now_ms() -> int:
    return int(time.time() * 1000)


def _day(ts_ms: Optional[float]) -> str:
    if not ts_ms:
        return ""
    return datetime.fromtimestamp(float(ts_ms) / 1000.0, tz=IST).date().isoformat()


def _num(value: object) -> Optional[float]:
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return out if out == out and abs(out) != float("inf") else None


def _int(value: object) -> Optional[int]:
    n = _num(value)
    return None if n is None else int(round(n))


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str, allow_nan=False)) if value is not None else None


def _dumps(value: Any) -> str:
    def clean(v: Any) -> Any:
        if isinstance(v, float) and (v != v or abs(v) == float("inf")):
            return None
        if isinstance(v, dict):
            return {str(k): clean(x) for k, x in v.items()}
        if isinstance(v, (list, tuple, set)):
            return [clean(x) for x in v]
        return v
    return json.dumps(clean(value), default=str, separators=(",", ":"), sort_keys=True)


def _loads(raw: Any, default: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    try:
        out = json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default
    return out if isinstance(out, type(default)) else default


def trade_id_for(strategy: str, uid: str, symbol: str, key: object) -> str:
    """The deterministic id of one trade: the same inputs from any code path, any restart,
    give the same id. ``key`` is whatever is unique to the trade for that strategy (the entry
    order id for Supertrend, the signal id for Gamma, the plan id for Snapback)."""
    digest = hashlib.sha1(f"{uid}|{strategy}|{symbol}|{key}".encode("utf-8")).hexdigest()[:10]
    return f"{_ID_PREFIX.get(strategy, 'TJ')}-{digest}"


_ENTRY_FIELDS = (
    "account_id", "underlying", "option_type", "strike", "expiry", "side", "lot_size", "lots", "qty",
    "signal_id", "entry_order_id", "signal_ts_ms", "entry_ts_ms", "entry_price", "initial_sl",
    "target", "capital_used",
)
_INT_FIELDS = {"lot_size", "lots", "qty", "signal_ts_ms", "entry_ts_ms", "exit_ts_ms", "exit_qty"}
_REAL_FIELDS = {"strike", "entry_price", "initial_sl", "target", "current_sl", "exit_price", "gross_pnl",
                "charges", "net_pnl", "r_multiple", "hold_minutes", "mae", "mfe", "capital_used"}

_TRADE_COLUMNS = (
    "trade_id", "mode", "strategy", "uid", "account_id", "status", "symbol", "underlying", "option_type",
    "strike", "expiry", "side", "lot_size", "lots", "qty", "signal_id", "entry_order_id", "signal_ts_ms",
    "entry_ts_ms", "entry_price", "initial_sl", "target", "current_sl", "tsl_history", "exit_ts_ms",
    "exit_price", "exit_qty", "exit_reason", "exit_reason_raw", "gross_pnl", "charges", "charges_source",
    "net_pnl", "r_multiple", "hold_minutes", "mae", "mfe", "capital_used", "signal_context", "extra",
    "narrative", "source", "entry_day", "exit_day", "trade_day", "created_ms", "updated_ms",
)
_JSON_COLUMNS = {"tsl_history": [], "signal_context": {}, "extra": {}}


def _decode_trade(row: Any) -> Dict[str, Any]:
    t = {k: row[k] for k in _TRADE_COLUMNS}
    for col, default in _JSON_COLUMNS.items():
        t[col] = _loads(t[col], default)
    return t


def _encode_trade(t: Dict[str, Any]) -> List[Any]:
    out = []
    for col in _TRADE_COLUMNS:
        value = t.get(col)
        if col in _JSON_COLUMNS:
            value = _dumps(value if value is not None else _JSON_COLUMNS[col])
        out.append(value)
    return out


def _load_trade(conn: Any, trade_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(f"SELECT {','.join(_TRADE_COLUMNS)} FROM trade_journal_trades WHERE trade_id=?",
                       (trade_id,)).fetchone()
    return _decode_trade(row) if row else None


def _save_trade(conn: Any, t: Dict[str, Any]) -> None:
    cols = ",".join(_TRADE_COLUMNS)
    marks = ",".join("?" * len(_TRADE_COLUMNS))
    conn.execute(f"INSERT OR REPLACE INTO trade_journal_trades ({cols}) VALUES ({marks})", _encode_trade(t))


def _blank_trade(trade_id: str, strategy: str, uid: str, symbol: str, now: int) -> Dict[str, Any]:
    t: Dict[str, Any] = {c: None for c in _TRADE_COLUMNS}
    t.update(trade_id=trade_id, mode=identity.mode_for_uid(uid), strategy=strategy, uid=uid,
             account_id="", status=PENDING, symbol=symbol, underlying="", option_type="", expiry="",
             side="", signal_id="", entry_order_id="", tsl_history=[], exit_reason="",
             exit_reason_raw="", charges_source="", signal_context={}, extra={}, narrative="",
             source="journal", entry_day="", exit_day="", trade_day="", created_ms=now, updated_ms=now)
    return t


def _coerce(field: str, value: Any) -> Any:
    if field in _INT_FIELDS:
        return _int(value)
    if field in _REAL_FIELDS:
        return _num(value)
    return value


def _present(value: Any) -> bool:
    return value is not None and value != ""


#: The only entry facts that legitimately change after the first report (a partial fill grows the
#: holding and moves the average). Everything else is a fact about the trade's birth: once known it
#: is never overwritten (a stop moved later is a TSL event, not a new initial stop).
_UPDATABLE = frozenset({"entry_price", "qty", "lots", "capital_used"})


def _merge_entry(t: Dict[str, Any], fields: Dict[str, Any]) -> None:
    """Fold entry facts into ``t``: a missing value never overwrites a known one, birth facts are
    sticky, and a closed trade's history is not rewritten by a late replay."""
    closed = t.get("status") == CLOSED
    for name in _ENTRY_FIELDS:
        if name not in fields:
            continue
        value = _coerce(name, fields[name])
        if not _present(value):
            continue
        if _present(t.get(name)) and (closed or name not in _UPDATABLE):
            continue
        t[name] = value


def _merge_dict(t: Dict[str, Any], column: str, extra: Optional[Dict[str, Any]]) -> None:
    if extra:
        merged = dict(t.get(column) or {})
        merged.update(_jsonable(extra))
        t[column] = merged


def _advance_status(t: Dict[str, Any], status: Optional[str]) -> None:
    if not status:
        return
    status = status.upper()
    current = t.get("status") or PENDING
    if status == current:
        return
    if current == REJECTED:
        return
    if _RANK.get(status, 0) >= _RANK.get(current, 0):
        t["status"] = status


# --------------------------------------------------------------------------
# Charges (estimate when the broker / virtual ledger has not said)
# --------------------------------------------------------------------------

def estimate_charges(legs: Iterable[Dict[str, Any]]) -> float:
    """Zerodha-style cost of a list of executed legs, using the one charge schedule the virtual
    exchange also uses (``simulation.charges``), so a live trade's estimate and a simulated
    trade's charges agree.

    Each leg is ``{"kind": "option"|"future", "side": "BUY"|"SELL", "price": p, "qty": q,
    "exchange": "NFO"}``. An estimate is only used until the real charges are recorded.
    """
    from app.services.simulation.charges import compute_charges

    total = 0.0
    for leg in legs:
        price, qty = _num(leg.get("price")), _int(leg.get("qty"))
        if not price or not qty or price <= 0 or qty <= 0:
            continue
        kind = "FUT" if str(leg.get("kind") or "option") == "future" else "CE"
        total += compute_charges(
            exchange=str(leg.get("exchange") or "NFO"), tradingsymbol="", side=str(leg.get("side") or "BUY"),
            quantity=qty, price=price, instrument_type=kind).total
    return round(total, 2)


# --------------------------------------------------------------------------
# Derived fields
# --------------------------------------------------------------------------

def _sign(side: Any) -> float:
    return -1.0 if str(side or "BUY").upper() == "SELL" else 1.0


def _exit_legs(conn: Any, trade_id: str) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT event_type, ts_ms, price, qty, pnl FROM trade_journal_events "
        "WHERE trade_id=? AND event_type IN (?,?) ORDER BY ts_ms, id",
        (trade_id, PARTIAL_EXIT, EXIT_FILLED)).fetchall()
    return [{"type": r["event_type"], "ts_ms": r["ts_ms"], "price": r["price"], "qty": r["qty"],
             "pnl": r["pnl"]} for r in rows]


def _derive(t: Dict[str, Any], legs: Sequence[Dict[str, Any]]) -> None:
    """Recompute every field that follows from the others, in place."""
    extra = t.get("extra") or {}
    entry_ts = t.get("entry_ts_ms")
    t["entry_day"] = _day(entry_ts) or t.get("entry_day") or _day(t.get("created_ms"))
    t["exit_day"] = _day(t.get("exit_ts_ms")) if t.get("exit_ts_ms") else ""
    t["trade_day"] = t["exit_day"] or t["entry_day"]
    entry, qty, sign = _num(t.get("entry_price")), _num(t.get("qty")), _sign(t.get("side"))

    if t.get("capital_used") is None and entry and qty:
        t["capital_used"] = round(entry * qty, 2)

    closed = t.get("status") == CLOSED
    if closed:
        priced = [x for x in legs if _num(x.get("price")) and _num(x.get("qty"))]
        if priced and not extra.get("gross_override"):
            sold = sum(float(x["qty"]) for x in priced)
            t["exit_qty"] = int(sold)
            t["exit_price"] = round(sum(float(x["qty"]) * float(x["price"]) for x in priced) / sold, 4)
            pnls = [x["pnl"] for x in priced if x.get("pnl") is not None]
            if len(pnls) == len(priced):
                t["gross_pnl"] = round(sum(float(p) for p in pnls), 2)
            elif entry:
                t["gross_pnl"] = round((t["exit_price"] - entry) * sold * sign, 2)
        # Charges: real ones (broker / virtual ledger) win. Per-order figures only count as the
        # real total once both the entry and an exit order are in, else a half-known figure would
        # understate the cost; until then (and when nothing real arrives) it is an estimate.
        by_order = extra.get("charges_by_order") or {}
        has_entry = any(str(k).startswith("entry") for k in by_order)
        has_exit = any(str(k).startswith("exit") for k in by_order)
        if by_order and has_entry and has_exit:
            t["charges"] = round(sum(float(v) for v in by_order.values()), 2)
            t["charges_source"] = str(extra.get("charges_source_real") or "broker")
        elif t.get("charges_source") in ("", None, "estimate") and entry and priced:
            kind = "future" if t.get("option_type") == "FUT" else "option"
            exch = str(extra.get("exchange") or "NFO")
            opposite = "SELL" if str(t.get("side") or "BUY").upper() == "BUY" else "BUY"
            round_trip = [{"kind": kind, "side": str(t.get("side") or "BUY").upper(), "price": entry,
                           "qty": qty or t.get("exit_qty"), "exchange": exch}]
            for x in priced:
                round_trip.append({"kind": kind, "side": opposite, "price": x["price"], "qty": x["qty"],
                                   "exchange": exch})
            hedge = extra.get("hedge") or {}
            if _num(hedge.get("entry_price")) and _num(hedge.get("qty")):
                hedge_open = str(hedge.get("side") or "BUY").upper()
                round_trip.append({"kind": "future", "side": hedge_open, "price": hedge["entry_price"],
                                   "qty": hedge["qty"], "exchange": exch})
                if _num(hedge.get("exit_price")):
                    round_trip.append({"kind": "future", "side": "SELL" if hedge_open == "BUY" else "BUY",
                                       "price": hedge["exit_price"], "qty": hedge["qty"], "exchange": exch})
            t["charges"] = estimate_charges(round_trip)
            t["charges_source"] = "estimate"
        if t.get("gross_pnl") is not None:
            t["net_pnl"] = round(float(t["gross_pnl"]) - float(t.get("charges") or 0.0), 2)
        else:
            t["net_pnl"] = None
        sl = _num(t.get("initial_sl"))
        risk = ((entry - sl) * sign * (qty or 0)) if (entry and sl and qty) else 0.0
        t["r_multiple"] = (round(float(t["net_pnl"]) / risk, 2)
                           if t.get("net_pnl") is not None and risk > 0 else None)
        if t.get("exit_ts_ms") and entry_ts:
            t["hold_minutes"] = round(max(0.0, (float(t["exit_ts_ms"]) - float(entry_ts)) / 60000.0), 1)
        if entry and qty:
            hw, lw = _num(extra.get("high_water")), _num(extra.get("low_water"))
            if sign > 0:
                t["mfe"] = round(max(0.0, (hw or entry) - entry) * qty, 2) if hw else t.get("mfe")
                t["mae"] = round(max(0.0, entry - (lw or entry)) * qty, 2) if lw else t.get("mae")
            else:
                t["mfe"] = round(max(0.0, entry - (lw or entry)) * qty, 2) if lw else t.get("mfe")
                t["mae"] = round(max(0.0, (hw or entry) - entry) * qty, 2) if hw else t.get("mae")
    t["narrative"] = build_narrative(t)


def _after_event(t: Dict[str, Any], event_type: str, ts_ms: int, price: Optional[float],
                 data: Dict[str, Any]) -> None:
    """Fold a NEW event into the trade row (never called for a replayed event)."""
    if event_type == TSL_MOVED:
        to = _num(data.get("to", price))
        if to:
            history = list(t.get("tsl_history") or [])
            history.append({"ts_ms": ts_ms, "from": _num(data.get("from")), "to": to})
            t["tsl_history"] = history
            t["current_sl"] = to
    elif event_type == PROTECTION_PLACED:
        stop = _num(price)
        if stop and not t.get("initial_sl"):
            t["initial_sl"] = stop
        if stop and not t.get("current_sl"):
            t["current_sl"] = stop
    elif event_type == REJECTED:
        _advance_status(t, REJECTED)
        reason = str(data.get("reason") or "").strip()
        if reason:
            _merge_dict(t, "extra", {"reject_reason": reason})


def _insert_event(conn: Any, trade_id: str, event_type: str, ts_ms: int, key: str, *,
                  price: Optional[float] = None, qty: Optional[int] = None, pnl: Optional[float] = None,
                  detail: str = "", data: Optional[Dict[str, Any]] = None,
                  cumulative: bool = False) -> bool:
    """Insert one event; ``True`` only when something new was stored.

    ``cumulative`` is for a fill keyed by its ORDER, where the broker reports a growing cumulative
    quantity: the stored event is updated when a larger quantity (or a better price) arrives and a
    replay of the same figures, or an older smaller one, changes nothing.
    """
    values = (trade_id, key, int(ts_ms), event_type, _num(price), _int(qty), _num(pnl), str(detail or ""),
              _dumps(data or {}))
    sql = ("INSERT {ignore}INTO trade_journal_events "
           "(trade_id, event_key, ts_ms, event_type, price, qty, pnl, detail, data) VALUES (?,?,?,?,?,?,?,?,?)")
    if not cumulative:
        return conn.execute(sql.format(ignore="OR IGNORE "), values).rowcount > 0
    cur = conn.execute(
        sql.format(ignore="") +
        " ON CONFLICT(trade_id, event_key) DO UPDATE SET event_type=excluded.event_type, price=excluded.price, "
        "qty=excluded.qty, pnl=excluded.pnl, detail=excluded.detail, data=excluded.data "
        "WHERE COALESCE(excluded.qty,0) >= COALESCE(trade_journal_events.qty,0) "
        "AND NOT (trade_journal_events.event_type='EXIT_FILLED' AND excluded.event_type='PARTIAL_EXIT') "
        "AND (COALESCE(excluded.qty,0) > COALESCE(trade_journal_events.qty,0) "
        "OR excluded.price IS NOT trade_journal_events.price "
        "OR excluded.event_type != trade_journal_events.event_type)", values)
    return cur.rowcount > 0


def _default_event_key(event_type: str, ts_ms: int, detail: str, price: Optional[float]) -> str:
    return f"{event_type}:{hashlib.sha1(f'{ts_ms}|{detail}|{price}'.encode()).hexdigest()[:10]}"


# --------------------------------------------------------------------------
# Recording API
# --------------------------------------------------------------------------

@_guarded()
def record_entry(*, uid: str, strategy: str, symbol: str, key: object, status: Optional[str] = None,
                 ts_ms: Optional[int] = None, signal_context: Optional[Dict[str, Any]] = None,
                 extra: Optional[Dict[str, Any]] = None, source: Optional[str] = None,
                 create_only: bool = False, **fields: Any) -> Optional[str]:
    """Create the trade (or fold new facts into it) and return its ``trade_id``.

    ``status`` is ``PENDING`` (order submitted) or ``OPEN`` (filled). ``fields`` are the entry
    facts: ``account_id underlying option_type strike expiry side lot_size lots qty signal_id
    entry_order_id signal_ts_ms entry_ts_ms entry_price initial_sl target capital_used``. Calling
    again with the same ``(uid, strategy, symbol, key)`` merges: a value never overwrites a known
    one with nothing, so any order of calls from any code path converges on one complete row.
    ``create_only`` makes the call a no-op when the trade already exists (used by hooks that only
    need the row to be there, such as a trail move reported before the fill was).
    """
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}")
    now = int(ts_ms or _now_ms())
    trade_id = trade_id_for(strategy, uid, symbol, key)
    with _write() as conn:
        t = _load_trade(conn, trade_id)
        if t is not None and create_only:
            return trade_id
        t = t or _blank_trade(trade_id, strategy, uid, symbol, now)
        if status and status.upper() == OPEN and not fields.get("entry_ts_ms") and not t.get("entry_ts_ms"):
            fields["entry_ts_ms"] = now
        _merge_entry(t, fields)
        _advance_status(t, status or (OPEN if _num(t.get("entry_price")) else PENDING))
        _merge_dict(t, "signal_context", signal_context)
        _merge_dict(t, "extra", extra)
        if source and (t.get("source") in (None, "", "journal")):
            t["source"] = source
        t["updated_ms"] = now
        _derive(t, _exit_legs(conn, trade_id) if t.get("status") == CLOSED else [])
        _save_trade(conn, t)
    return trade_id


@_guarded()
def trade_status(trade_id: str) -> Optional[str]:
    """The journal's status for one trade (``PENDING``/``OPEN``/``CLOSED``/``REJECTED``), or ``None``."""
    with _read() as conn:
        row = conn.execute("SELECT status FROM trade_journal_trades WHERE trade_id=?", (trade_id,)).fetchone()
    return row[0] if row else None


@_guarded()
def record_event(*, event_type: str, trade_id: Optional[str] = None, uid: str = "", strategy: str = "",
                 symbol: str = "", key: object = "", ts_ms: Optional[int] = None, detail: str = "",
                 price: Optional[float] = None, qty: Optional[int] = None, pnl: Optional[float] = None,
                 data: Optional[Dict[str, Any]] = None, event_key: Optional[str] = None) -> Optional[str]:
    """Append one timeline event; a repeat of the same ``event_key`` is ignored.

    The trade is named by ``trade_id`` or by ``(uid, strategy, symbol, key)``. An event for a trade
    that has no row yet is dropped (the strategy adapters create the row first). Returns the
    ``trade_id``.
    """
    now = int(ts_ms or _now_ms())
    tid = trade_id or trade_id_for(strategy, uid, symbol, key)
    data = dict(data or {})
    with _write() as conn:
        t = _load_trade(conn, tid)
        if t is None:
            log.debug("trade journal: %s for unknown trade %s dropped", event_type, tid)
            return None
        ekey = event_key or _default_event_key(event_type, now, detail, price)
        fresh = _insert_event(conn, tid, event_type, now, ekey, price=price, qty=qty, pnl=pnl,
                              detail=detail, data=data)
        if fresh:
            _after_event(t, event_type, now, _num(price), data)
            t["updated_ms"] = now
            _derive(t, _exit_legs(conn, tid) if t.get("status") == CLOSED else [])
            _save_trade(conn, t)
    return tid


@_guarded()
def record_exit(*, trade_id: Optional[str] = None, uid: str = "", strategy: str = "", symbol: str = "",
                key: object = "", exit_price: Optional[float] = None, qty: Optional[int] = None,
                ts_ms: Optional[int] = None, reason: str = "", trailed: Optional[bool] = None,
                gross_pnl: Optional[float] = None, charges: Optional[float] = None,
                charges_source: str = "", partial: bool = False, fill_key: Optional[str] = None,
                cumulative: bool = False, extra: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Book an exit fill. ``partial=True`` records a part-exit and leaves the trade open.

    On the final exit the trade closes: ``exit_price`` becomes the quantity-weighted average of
    every exit fill, gross P&L the sum of their P&L (or ``gross_pnl`` when the strategy knows the
    whole-trade figure and passes it), charges the real ones if recorded else an estimate, net
    P&L gross minus charges, plus R-multiple and hold time. ``reason`` is the strategy's own
    string; it is mapped onto the canonical set (``trailed`` separates SL from TSL).
    Replaying the same fill (same ``fill_key``, or same price and quantity) changes nothing.
    ``cumulative=True`` says ``fill_key`` names an ORDER whose reported quantity grows as it fills:
    the order's one event is updated rather than a second one added.
    """
    now = int(ts_ms or _now_ms())
    tid = trade_id or trade_id_for(strategy, uid, symbol, key)
    price, quantity = _num(exit_price), _int(qty)
    if price is not None and price <= 0:
        price = None
    with _write() as conn:
        t = _load_trade(conn, tid)
        if t is None:
            log.debug("trade journal: exit for unknown trade %s dropped", tid)
            return None
        entry = _num(t.get("entry_price"))
        leg_pnl = (round((price - entry) * quantity * _sign(t.get("side")), 2)
                   if price and entry and quantity else None)
        etype = PARTIAL_EXIT if partial else EXIT_FILLED
        ekey = fill_key or f"{etype}:{price if price else 'na'}:{quantity if quantity else 'na'}"
        canonical = canonical_exit_reason(t["strategy"], reason, trailed=trailed) if reason else ""
        raw = str(reason or "")
        fresh = _insert_event(
            conn, tid, etype, now, ekey, price=price, qty=quantity, pnl=leg_pnl,
            detail=(f"Sold {quantity} at {price:.2f}" if price and quantity else "Exit recorded") +
                   (f" ({EXIT_REASON_LABELS.get(canonical, canonical)})" if canonical and not partial else ""),
            data={"reason": raw, "canonical": canonical}, cumulative=cumulative)
        if partial:
            if fresh:
                t["updated_ms"] = now
                _derive(t, [])
                _save_trade(conn, t)
            return tid
        t["status"] = CLOSED
        if fresh or not t.get("exit_ts_ms"):
            t["exit_ts_ms"] = now
        if canonical:
            t["exit_reason"], t["exit_reason_raw"] = canonical, raw
        elif not t.get("exit_reason"):
            t["exit_reason"] = "OTHER"
        _merge_dict(t, "extra", extra)
        gross = _num(gross_pnl)
        if gross is not None:
            t["gross_pnl"] = round(gross, 2)
            _merge_dict(t, "extra", {"gross_override": True})
            if price:
                t["exit_price"] = price
            if quantity:
                t["exit_qty"] = quantity
        real_charges = _num(charges)
        if real_charges is not None:
            t["charges"] = round(real_charges, 2)
            t["charges_source"] = charges_source or "broker"
        t["updated_ms"] = now
        _derive(t, _exit_legs(conn, tid))
        _save_trade(conn, t)
    return tid


@_guarded()
def record_charges(*, trade_id: Optional[str] = None, uid: str = "", strategy: str = "", symbol: str = "",
                   key: object = "", charges: float = 0.0, source: str = "broker",
                   order_key: Optional[str] = None) -> Optional[str]:
    """Attach real charges (broker contract note, virtual-ledger charges) to a trade.

    With ``order_key`` (``"entry:<order id>"`` / ``"exit:<order id>"``) the amount is the cost of that
    one order: replaying an order overwrites, never doubles, and the total only replaces the estimate
    once an entry and an exit order are both known. Without it ``charges`` is the whole-trade total
    and always replaces an estimate.
    """
    amount = _num(charges)
    if amount is None or amount < 0:
        return None
    tid = trade_id or trade_id_for(strategy, uid, symbol, key)
    with _write() as conn:
        t = _load_trade(conn, tid)
        if t is None:
            return None
        if order_key:
            by_order = dict((t.get("extra") or {}).get("charges_by_order") or {})
            by_order[str(order_key)] = round(amount, 2)
            _merge_dict(t, "extra", {"charges_by_order": by_order, "charges_source_real": source or "broker"})
        else:
            t["charges"] = round(amount, 2)
            t["charges_source"] = source or "broker"
        t["updated_ms"] = _now_ms()
        _derive(t, _exit_legs(conn, tid) if t.get("status") == CLOSED else [])
        _save_trade(conn, t)
    return tid


# --------------------------------------------------------------------------
# Skips
# --------------------------------------------------------------------------

_skip_seen: Dict[tuple, float] = {}
_skip_seen_day = ""
#: A repeat of the same refusal inside this window is not even written: the strategies re-check
#: every few seconds and a counter that ticks that fast would only churn the database.
SKIP_REWRITE_EVERY_S = 300.0


@_guarded(default=False)
def record_skip(*, uid: str, strategy: str, code: str, reason: str = "", symbol: str = "",
                signal_id: str = "", underlying: str = "", context: Optional[Dict[str, Any]] = None,
                ts_ms: Optional[int] = None) -> bool:
    """Record that a signal was declined or blocked, once per signal + code per day.

    Repeats bump ``count`` / ``last_ms`` (at most every :data:`SKIP_REWRITE_EVERY_S` seconds) so the
    table cannot flood. Returns whether this call wrote to the database.
    """
    global _skip_seen_day
    now = int(ts_ms or _now_ms())
    day = _day(now)
    if day != _skip_seen_day:
        _skip_seen.clear()
        _skip_seen_day = day
    code = str(code or "blocked")
    sig, sym = str(signal_id or ""), str(symbol or "")
    memo = (str(getattr(db, "_DB_PATH", "")), day, uid, strategy, sig, sym, code)
    last = _skip_seen.get(memo)
    if last is not None and now / 1000.0 - last < SKIP_REWRITE_EVERY_S:
        return False
    with _write() as conn:
        conn.execute(
            "INSERT INTO trade_journal_skips (day, mode, strategy, uid, symbol, underlying, signal_id, code, "
            "reason, context, count, first_ms, last_ms) VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?) "
            "ON CONFLICT(day, uid, strategy, signal_id, symbol, code) DO UPDATE SET "
            "count=count+1, last_ms=excluded.last_ms, reason=excluded.reason",
            (day, identity.mode_for_uid(uid), strategy, uid, sym, str(underlying or ""), sig, code,
             str(reason or reason_for_code(code)), _dumps(context or {}), now, now))
    _skip_seen[memo] = now / 1000.0
    return True


@_guarded(default=0)
def purge_simulation(uid: str) -> int:
    """Delete every journal row of a SIMULATION tenant (the simulation reset).

    Refuses anything that is not a simulation uid: a live trade's record is never erased from here.
    """
    if not identity.is_sim_uid(uid):
        raise ValueError("purge_simulation only accepts a simulation uid")
    with _write() as conn:
        ids = [r[0] for r in conn.execute("SELECT trade_id FROM trade_journal_trades WHERE uid=?", (uid,))]
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            conn.execute(f"DELETE FROM trade_journal_events WHERE trade_id IN ({','.join('?' * len(chunk))})", chunk)
        conn.execute("DELETE FROM trade_journal_trades WHERE uid=?", (uid,))
        conn.execute("DELETE FROM trade_journal_skips WHERE uid=?", (uid,))
    for k in [k for k in _skip_seen if k[2] == uid]:
        _skip_seen.pop(k, None)
    return len(ids)


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

_PNL_SQL = "COALESCE(net_pnl, gross_pnl)"
_SORTS = {
    "newest": "COALESCE(exit_ts_ms, entry_ts_ms, created_ms) DESC, trade_id",
    "oldest": "COALESCE(exit_ts_ms, entry_ts_ms, created_ms) ASC, trade_id",
    "pnl_desc": f"{_PNL_SQL} IS NULL, {_PNL_SQL} DESC, trade_id",
    "pnl_asc": f"{_PNL_SQL} IS NULL, {_PNL_SQL} ASC, trade_id",
    "hold_desc": "hold_minutes IS NULL, hold_minutes DESC, trade_id",
    "symbol": "symbol ASC, entry_ts_ms DESC",
    "strategy": "strategy ASC, entry_ts_ms DESC",
}
OUTCOMES = ("win", "loss", "open")


def _filters(uids: Sequence[str], *, strategy: Optional[str] = None, outcome: Optional[str] = None,
             date_from: Optional[str] = None, date_to: Optional[str] = None,
             symbol: Optional[str] = None, exit_reason: Optional[str] = None,
             status: Optional[str] = None) -> tuple:
    if not uids:
        return "1=0", []
    where = [f"uid IN ({','.join('?' * len(uids))})"]
    args: List[Any] = list(uids)
    if strategy:
        where.append("strategy=?")
        args.append(strategy.lower())
    if outcome:
        o = outcome.lower()
        if o == "win":
            where.append(f"status='CLOSED' AND {_PNL_SQL} > 0")
        elif o == "loss":
            where.append(f"status='CLOSED' AND {_PNL_SQL} < 0")
        elif o == "open":
            where.append("status IN ('OPEN','PENDING')")
        else:
            raise ValueError("outcome must be win, loss or open")
    if status:
        where.append("status=?")
        args.append(status.upper())
    if date_from:
        where.append("trade_day >= ?")
        args.append(date_from)
    if date_to:
        where.append("trade_day <= ?")
        args.append(date_to)
    if symbol:
        where.append("(UPPER(symbol) LIKE ? OR UPPER(underlying) LIKE ?)")
        like = f"%{symbol.upper()}%"
        args.extend([like, like])
    if exit_reason:
        where.append("exit_reason=?")
        args.append(exit_reason.upper())
    return " AND ".join(where), args


def _present_trade(t: Dict[str, Any]) -> Dict[str, Any]:
    t = dict(t)
    t["side"] = t.get("side") or "BUY"
    t["strategy_label"] = STRATEGY_LABELS.get(t["strategy"], t["strategy"])
    t["exit_reason_label"] = EXIT_REASON_LABELS.get(t.get("exit_reason") or "", t.get("exit_reason") or "")
    t["pnl"] = t["net_pnl"] if t.get("net_pnl") is not None else t.get("gross_pnl")
    return t


def list_trades(uids: Sequence[str], *, sort: str = "newest", limit: int = 50, offset: int = 0,
                **filters: Any) -> Dict[str, Any]:
    """A page of trades for ``uids`` (already-authorised tenants) and the total that match."""
    if sort not in _SORTS:
        raise ValueError(f"sort must be one of {sorted(_SORTS)}")
    where, args = _filters(uids, **filters)
    limit = max(1, min(int(limit), 500))
    with _read() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM trade_journal_trades WHERE {where}", args).fetchone()[0]
        rows = conn.execute(
            f"SELECT {','.join(_TRADE_COLUMNS)} FROM trade_journal_trades WHERE {where} "
            f"ORDER BY {_SORTS[sort]} LIMIT ? OFFSET ?", [*args, limit, max(0, int(offset))]).fetchall()
    return {"total": total, "limit": limit, "offset": max(0, int(offset)),
            "items": [_present_trade(_decode_trade(r)) for r in rows]}


def rows_for_stats(uids: Sequence[str], **filters: Any) -> List[Dict[str, Any]]:
    """Every matching trade, as the plain rows the statistics functions take."""
    where, args = _filters(uids, **filters)
    with _read() as conn:
        rows = conn.execute(
            f"SELECT {','.join(_TRADE_COLUMNS)} FROM trade_journal_trades WHERE {where} "
            f"ORDER BY COALESCE(exit_ts_ms, entry_ts_ms, created_ms), trade_id", args).fetchall()
    return [_decode_trade(r) for r in rows]


def get_trade(trade_id: str, uids: Sequence[str]) -> Optional[Dict[str, Any]]:
    """One trade with its ordered timeline, or ``None`` (also when it belongs to another tenant)."""
    if not uids:
        return None
    with _read() as conn:
        row = conn.execute(
            f"SELECT {','.join(_TRADE_COLUMNS)} FROM trade_journal_trades WHERE trade_id=? AND "
            f"uid IN ({','.join('?' * len(uids))})", [trade_id, *uids]).fetchone()
        if row is None:
            return None
        events = conn.execute(
            "SELECT event_type, ts_ms, price, qty, pnl, detail, data FROM trade_journal_events "
            "WHERE trade_id=? ORDER BY ts_ms, id", (trade_id,)).fetchall()
    out = _present_trade(_decode_trade(row))
    out["timeline"] = [{"type": e["event_type"], "ts_ms": e["ts_ms"], "price": e["price"], "qty": e["qty"],
                        "pnl": e["pnl"], "detail": e["detail"], "data": _loads(e["data"], {})}
                       for e in events]
    return out


def list_skips(uids: Sequence[str], *, strategy: Optional[str] = None, code: Optional[str] = None,
               date_from: Optional[str] = None, date_to: Optional[str] = None,
               limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    """A page of declined signals (newest first) plus how many times each blocker code fired."""
    if not uids:
        return {"total": 0, "limit": limit, "offset": offset, "items": [], "by_code": []}
    where = [f"uid IN ({','.join('?' * len(uids))})"]
    args: List[Any] = list(uids)
    for clause, value in (("strategy=?", strategy.lower() if strategy else None), ("code=?", code),
                          ("day>=?", date_from), ("day<=?", date_to)):
        if value:
            where.append(clause)
            args.append(value)
    cond = " AND ".join(where)
    limit = max(1, min(int(limit), 500))
    with _read() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM trade_journal_skips WHERE {cond}", args).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM trade_journal_skips WHERE {cond} ORDER BY last_ms DESC, id DESC LIMIT ? OFFSET ?",
            [*args, limit, max(0, int(offset))]).fetchall()
        codes = conn.execute(
            f"SELECT code, COUNT(*) AS signals, SUM(count) AS times FROM trade_journal_skips WHERE {cond} "
            f"GROUP BY code ORDER BY signals DESC, code", args).fetchall()
    items = [{"id": r["id"], "day": r["day"], "mode": r["mode"], "strategy": r["strategy"],
              "strategy_label": STRATEGY_LABELS.get(r["strategy"], r["strategy"]), "symbol": r["symbol"],
              "underlying": r["underlying"], "signal_id": r["signal_id"], "code": r["code"],
              "reason": r["reason"], "context": _loads(r["context"], {}), "count": r["count"],
              "first_ms": r["first_ms"], "last_ms": r["last_ms"]} for r in rows]
    return {"total": total, "limit": limit, "offset": max(0, int(offset)), "items": items,
            "by_code": [{"code": c["code"], "signals": c["signals"], "times": c["times"],
                         "reason": reason_for_code(c["code"])} for c in codes]}


__all__ = [
    "MODE_LIVE", "MODE_SIMULATION", "STRATEGIES", "PENDING", "OPEN", "CLOSED", "REJECTED", "EVENT_TYPES",
    "SIGNAL", "ORDER_SUBMITTED", "FILLED", "PROTECTION_PLACED", "TSL_MOVED", "TARGET_HIT", "EXIT_SUBMITTED",
    "EXIT_FILLED", "PARTIAL_EXIT", "HEDGE_FILLED", "FAILED", "NOTE", "CANONICAL_EXIT_REASONS", "OUTCOMES",
    "trade_id_for", "estimate_charges", "record_entry", "record_event", "record_exit", "record_charges",
    "record_skip", "purge_simulation", "trade_status", "list_trades", "rows_for_stats", "get_trade", "list_skips",
]
