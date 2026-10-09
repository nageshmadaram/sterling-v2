"""The virtual account book: cash, orders, fills, positions, GTTs, charges, equity.

Everything a Simulation owner "has at the broker" lives here, in SQLite tables prefixed
``sim_`` and keyed by the OWNER uid, so two owners (or the live tenant) never mix. The
accounting model is deliberately plain:

* ``cash`` is capital plus realised P&L minus charges. Opening a position moves no cash;
  closing one books its realised P&L. Open positions are carried at the last real price.
* ``blocked`` is the margin the open positions hold: a long option its premium, a short
  option or a future ``futures_margin_pct`` of notional.
* ``available`` is ``cash - blocked`` less any unrealised loss, i.e. what a new order may use.
* ``equity`` is ``cash`` plus unrealised P&L.

Orders, trades and GTTs are stored as the Kite-shaped documents the engines read, next to the
few columns needed to query them. Non-Kite bookkeeping travels under the ``_sim`` key and is
stripped by :func:`public`.

Connections follow the app convention (``app.services.db.connection``): one commit on a clean
exit, a rollback on error. :meth:`SimLedger.transaction` holds ONE connection for a whole
fill so cash, position, trade and charges can never disagree.
"""
from __future__ import annotations

import itertools
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta, timezone
from typing import Any, Callable, Dict, Iterator, List, Optional

from app.services.simulation.charges import Charges, KIND_FUTURE, KIND_OPTION, instrument_kind
from app.services.simulation.identity import owner_uid

IST = timezone(timedelta(hours=5, minutes=30))
DEFAULT_CAPITAL = 1_000_000.0
OPEN_STATUSES = ("OPEN", "TRIGGER PENDING")
ACTIVE_GTT = "active"

_DDL = (
    """CREATE TABLE IF NOT EXISTS sim_accounts (
        owner TEXT PRIMARY KEY, capital REAL NOT NULL, cash REAL NOT NULL,
        created_ms INTEGER NOT NULL, updated_ms INTEGER NOT NULL, reset_ms INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS sim_settings (
        owner TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY (owner, key))""",
    """CREATE TABLE IF NOT EXISTS sim_sequences (name TEXT PRIMARY KEY, value INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS sim_orders (
        owner TEXT NOT NULL, order_id TEXT NOT NULL, day TEXT NOT NULL, status TEXT NOT NULL,
        exchange TEXT NOT NULL, tradingsymbol TEXT NOT NULL, placed_ms INTEGER NOT NULL,
        updated_ms INTEGER NOT NULL, doc TEXT NOT NULL, PRIMARY KEY (owner, order_id))""",
    "CREATE INDEX IF NOT EXISTS ix_sim_orders_status ON sim_orders(owner, status)",
    "CREATE INDEX IF NOT EXISTS ix_sim_orders_day ON sim_orders(owner, day)",
    """CREATE TABLE IF NOT EXISTS sim_order_history (
        owner TEXT NOT NULL, order_id TEXT NOT NULL, seq INTEGER NOT NULL, ts_ms INTEGER NOT NULL,
        status TEXT NOT NULL, doc TEXT NOT NULL, PRIMARY KEY (owner, order_id, seq))""",
    """CREATE TABLE IF NOT EXISTS sim_trades (
        owner TEXT NOT NULL, trade_id TEXT NOT NULL, order_id TEXT NOT NULL, day TEXT NOT NULL,
        fill_ms INTEGER NOT NULL, exchange TEXT NOT NULL, tradingsymbol TEXT NOT NULL,
        product TEXT NOT NULL, side TEXT NOT NULL, quantity INTEGER NOT NULL, price REAL NOT NULL,
        realised REAL NOT NULL, charges REAL NOT NULL, doc TEXT NOT NULL,
        PRIMARY KEY (owner, trade_id))""",
    "CREATE INDEX IF NOT EXISTS ix_sim_trades_day ON sim_trades(owner, day)",
    "CREATE INDEX IF NOT EXISTS ix_sim_trades_order ON sim_trades(owner, order_id)",
    """CREATE TABLE IF NOT EXISTS sim_positions (
        owner TEXT NOT NULL, exchange TEXT NOT NULL, tradingsymbol TEXT NOT NULL,
        product TEXT NOT NULL, instrument_token INTEGER NOT NULL DEFAULT 0,
        quantity INTEGER NOT NULL DEFAULT 0, avg_price REAL NOT NULL DEFAULT 0,
        last_price REAL NOT NULL DEFAULT 0, last_price_ms INTEGER NOT NULL DEFAULT 0,
        meta TEXT NOT NULL DEFAULT '{}', updated_ms INTEGER NOT NULL,
        PRIMARY KEY (owner, exchange, tradingsymbol, product))""",
    """CREATE TABLE IF NOT EXISTS sim_gtts (
        owner TEXT NOT NULL, trigger_id INTEGER NOT NULL, status TEXT NOT NULL,
        exchange TEXT NOT NULL, tradingsymbol TEXT NOT NULL, created_ms INTEGER NOT NULL,
        updated_ms INTEGER NOT NULL, doc TEXT NOT NULL, PRIMARY KEY (owner, trigger_id))""",
    "CREATE INDEX IF NOT EXISTS ix_sim_gtts_status ON sim_gtts(owner, status)",
    """CREATE TABLE IF NOT EXISTS sim_charges (
        id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, trade_id TEXT NOT NULL,
        order_id TEXT NOT NULL, day TEXT NOT NULL, ts_ms INTEGER NOT NULL, exchange TEXT NOT NULL,
        tradingsymbol TEXT NOT NULL, side TEXT NOT NULL, turnover REAL NOT NULL,
        brokerage REAL NOT NULL, stt REAL NOT NULL, exchange_txn REAL NOT NULL, sebi REAL NOT NULL,
        stamp REAL NOT NULL, gst REAL NOT NULL, total REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS ix_sim_charges_day ON sim_charges(owner, day)",
    """CREATE TABLE IF NOT EXISTS sim_equity (
        owner TEXT NOT NULL, ts_ms INTEGER NOT NULL, cash REAL NOT NULL, blocked REAL NOT NULL,
        unrealised REAL NOT NULL, equity REAL NOT NULL, realised_day REAL NOT NULL,
        charges_day REAL NOT NULL, positions INTEGER NOT NULL, PRIMARY KEY (owner, ts_ms))""",
)
_OWNED_TABLES = ("sim_settings", "sim_orders", "sim_order_history", "sim_trades", "sim_positions",
                 "sim_gtts", "sim_charges", "sim_equity")


class SettingsError(ValueError):
    pass


@dataclass(frozen=True)
class SimSettings:
    slippage_ticks: int = 1
    partial_fills: bool = False
    enforce_market_hours: bool = False
    enforce_tick_size: bool = True
    futures_margin_pct: float = 0.12


_SETTING_TYPES: Dict[str, Callable[[Any], Any]] = {
    "slippage_ticks": int, "futures_margin_pct": float,
    "partial_fills": lambda v: str(v).strip().lower() in ("1", "true", "yes", "on"),
    "enforce_market_hours": lambda v: str(v).strip().lower() in ("1", "true", "yes", "on"),
    "enforce_tick_size": lambda v: str(v).strip().lower() in ("1", "true", "yes", "on"),
}


def now_ms() -> int:
    return int(time.time() * 1000)


def public(doc: Optional[dict]) -> Optional[dict]:
    """A Kite-shaped copy of a stored document: bookkeeping removed."""
    return None if doc is None else {k: v for k, v in doc.items() if k != "_sim"}


# ── margin model (shared by the exchange's pre-trade check and the account view) ─────────
def position_margin(meta: dict, quantity: int, avg_price: float, *, futures_pct: float = 0.12) -> float:
    """Margin an open position of ``quantity`` at ``avg_price`` holds."""
    qty = abs(int(quantity))
    if qty == 0:
        return 0.0
    kind = instrument_kind(meta.get("tradingsymbol", ""), meta.get("instrument_type", ""))
    if kind == KIND_OPTION:
        if quantity > 0:
            return round(avg_price * qty, 2)                      # long: the premium paid
        strike = float(meta.get("strike") or 0.0) or avg_price
        return round(max(futures_pct * strike * qty, avg_price * qty), 2)
    if kind == KIND_FUTURE:
        return round(futures_pct * avg_price * qty, 2)
    return round(avg_price * qty if quantity > 0 else 0.2 * avg_price * qty, 2)


class SimLedger:
    """SQLite-backed virtual books. Cheap to construct; state lives in the database."""

    def __init__(self, connection_factory: Optional[Callable[[], Any]] = None) -> None:
        self._factory = connection_factory
        self._lock = threading.RLock()
        self._local = threading.local()        # the open transaction belongs to its own thread
        self._savepoints = itertools.count(1)
        self._schema_key: Any = None

    # ── connection plumbing ───────────────────────────────────────────────────────────
    def _connect(self):
        if self._factory is not None:
            return self._factory()
        from app.services import db
        return db.connection()

    def _key(self) -> Any:
        if self._factory is not None:
            return id(self._factory)
        from app.services import db
        return db._DB_PATH

    @contextmanager
    def _cx(self) -> Iterator[Any]:
        tx = getattr(self._local, "tx", None)
        if tx is not None:
            yield tx
            return
        with self._connect() as c:
            c.row_factory = sqlite3.Row
            self._ensure_schema(c)
            yield c

    def _ensure_schema(self, c) -> None:
        key = self._key()
        if self._schema_key == key:
            return
        for ddl in _DDL:
            c.execute(ddl)
        self._schema_key = key

    @contextmanager
    def transaction(self) -> Iterator["SimLedger"]:
        """One atomic unit of work. Re-entrant; no awaits belong inside."""
        with self._lock:
            if getattr(self._local, "tx", None) is not None:
                yield self
                return
            with self._connect() as c:
                c.row_factory = sqlite3.Row
                self._ensure_schema(c)
                c.execute("BEGIN IMMEDIATE")
                self._local.tx = c
                try:
                    yield self
                finally:
                    self._local.tx = None

    @contextmanager
    def savepoint(self) -> Iterator[None]:
        """A nested unit of work inside :meth:`transaction`: on an exception only ITS writes are undone
        (the exception still propagates), so one bad order cannot poison a whole evaluation pass."""
        with self.transaction():
            with self._cx() as c:
                name = f"sp{next(self._savepoints)}"
                c.execute(f"SAVEPOINT {name}")
                try:
                    yield
                except BaseException:
                    c.execute(f"ROLLBACK TO {name}")
                    c.execute(f"RELEASE {name}")
                    raise
                c.execute(f"RELEASE {name}")

    @staticmethod
    def _owner(owner: str) -> str:
        value = owner_uid(str(owner or "").strip())
        if not value:
            raise ValueError("owner uid is required")
        return value

    def next_id(self, name: str) -> int:
        """A globally unique, ever-increasing id (survives reset: ids are never reused)."""
        with self.transaction():
            with self._cx() as c:
                row = c.execute("SELECT value FROM sim_sequences WHERE name=?", (name,)).fetchone()
                value = (row[0] if row else 0) + 1
                c.execute("INSERT INTO sim_sequences(name, value) VALUES(?, ?) "
                          "ON CONFLICT(name) DO UPDATE SET value=excluded.value", (name, value))
                return int(value)

    # ── accounts, cash, settings ──────────────────────────────────────────────────────
    def ensure_account(self, owner: str, *, capital: float = DEFAULT_CAPITAL) -> dict:
        o = self._owner(owner)
        with self._cx() as c:
            c.execute("INSERT OR IGNORE INTO sim_accounts(owner, capital, cash, created_ms, updated_ms) "
                      "VALUES(?,?,?,?,?)", (o, float(capital), float(capital), now_ms(), now_ms()))
            return dict(c.execute("SELECT * FROM sim_accounts WHERE owner=?", (o,)).fetchone())

    def account(self, owner: str) -> Optional[dict]:
        with self._cx() as c:
            row = c.execute("SELECT * FROM sim_accounts WHERE owner=?", (self._owner(owner),)).fetchone()
            return dict(row) if row else None

    def owners(self) -> List[str]:
        with self._cx() as c:
            return [r[0] for r in c.execute("SELECT owner FROM sim_accounts ORDER BY owner")]

    def cash(self, owner: str) -> float:
        return float(self.ensure_account(owner)["cash"])

    def adjust_cash(self, owner: str, delta: float) -> float:
        o = self._owner(owner)
        with self.transaction():
            acct = self.ensure_account(o)
            cash = round(float(acct["cash"]) + float(delta), 2)
            with self._cx() as c:
                c.execute("UPDATE sim_accounts SET cash=?, updated_ms=? WHERE owner=?", (cash, now_ms(), o))
            return cash

    def set_capital(self, owner: str, amount: float) -> dict:
        """Change the starting capital; cash moves by the same delta (funds added or withdrawn)."""
        amount = float(amount)
        if not amount > 0:
            raise ValueError("capital must be positive")
        o = self._owner(owner)
        with self.transaction():
            acct = self.ensure_account(o)
            cash = round(float(acct["cash"]) + (amount - float(acct["capital"])), 2)
            with self._cx() as c:
                c.execute("UPDATE sim_accounts SET capital=?, cash=?, updated_ms=? WHERE owner=?",
                          (amount, cash, now_ms(), o))
            return self.account(o)

    def reset(self, owner: str, *, capital: Optional[float] = None) -> dict:
        """Wipe the owner's virtual books (orders, fills, positions, GTTs, charges, equity)."""
        o = self._owner(owner)
        with self.transaction():
            acct = self.ensure_account(o)
            cap = float(capital) if capital is not None else float(acct["capital"])
            if not cap > 0:
                raise ValueError("capital must be positive")
            with self._cx() as c:
                for table in _OWNED_TABLES:
                    if table == "sim_settings":
                        continue                                 # operator preferences survive a reset
                    c.execute(f"DELETE FROM {table} WHERE owner=?", (o,))
                c.execute("UPDATE sim_accounts SET capital=?, cash=?, updated_ms=?, reset_ms=? WHERE owner=?",
                          (cap, cap, now_ms(), now_ms(), o))
            return self.account(o)

    def settings(self, owner: str) -> SimSettings:
        with self._cx() as c:
            rows = c.execute("SELECT key, value FROM sim_settings WHERE owner=?", (self._owner(owner),)).fetchall()
        values = {}
        for key, raw in rows:
            if key in _SETTING_TYPES:
                try:
                    values[key] = _SETTING_TYPES[key](raw)
                except (TypeError, ValueError):
                    continue
        return SimSettings(**values)

    def set_setting(self, owner: str, key: str, value: Any) -> SimSettings:
        if key not in _SETTING_TYPES:
            raise SettingsError(f"unknown simulation setting {key!r}")
        try:
            parsed = _SETTING_TYPES[key](value)
        except (TypeError, ValueError) as exc:
            raise SettingsError(f"bad value for {key}: {value!r}") from exc
        if key == "slippage_ticks" and not 0 <= parsed <= 50:
            raise SettingsError("slippage_ticks must be between 0 and 50")
        if key == "futures_margin_pct" and not 0 < parsed <= 1:
            raise SettingsError("futures_margin_pct must be in (0, 1]")
        o = self._owner(owner)
        with self._cx() as c:
            c.execute("INSERT INTO sim_settings(owner, key, value) VALUES(?,?,?) "
                      "ON CONFLICT(owner, key) DO UPDATE SET value=excluded.value", (o, key, str(parsed)))
        return self.settings(o)

    # ── orders ────────────────────────────────────────────────────────────────────────
    def put_order(self, owner: str, doc: dict) -> None:
        o = self._owner(owner)
        sim = doc.get("_sim") or {}
        with self._cx() as c:
            c.execute(
                "INSERT INTO sim_orders(owner, order_id, day, status, exchange, tradingsymbol, placed_ms, "
                "updated_ms, doc) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(owner, order_id) DO UPDATE SET "
                "status=excluded.status, updated_ms=excluded.updated_ms, doc=excluded.doc",
                (o, str(doc["order_id"]), sim.get("day", ""), doc["status"], doc["exchange"],
                 doc["tradingsymbol"], int(sim.get("placed_ms") or now_ms()), now_ms(), json.dumps(doc)))

    def get_order(self, owner: str, order_id: str) -> Optional[dict]:
        with self._cx() as c:
            row = c.execute("SELECT doc FROM sim_orders WHERE owner=? AND order_id=?",
                            (self._owner(owner), str(order_id))).fetchone()
            return json.loads(row[0]) if row else None

    def orders(self, owner: str, *, day: Optional[str] = None, statuses: Optional[tuple] = None) -> List[dict]:
        sql, args = "SELECT doc FROM sim_orders WHERE owner=?", [self._owner(owner)]
        if day:
            sql += " AND day=?"
            args.append(day)
        if statuses:
            sql += f" AND status IN ({','.join('?' * len(statuses))})"
            args.extend(statuses)
        with self._cx() as c:
            return [json.loads(r[0]) for r in c.execute(sql + " ORDER BY placed_ms, order_id", args)]

    def open_orders(self, owner: str) -> List[dict]:
        return self.orders(owner, statuses=OPEN_STATUSES)

    def add_history(self, owner: str, doc: dict) -> None:
        o = self._owner(owner)
        with self._cx() as c:
            seq = c.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM sim_order_history WHERE owner=? AND order_id=?",
                            (o, str(doc["order_id"]))).fetchone()[0]
            c.execute("INSERT INTO sim_order_history(owner, order_id, seq, ts_ms, status, doc) VALUES(?,?,?,?,?,?)",
                      (o, str(doc["order_id"]), seq, now_ms(), doc["status"], json.dumps(doc)))

    def history(self, owner: str, order_id: str) -> List[dict]:
        with self._cx() as c:
            return [json.loads(r[0]) for r in c.execute(
                "SELECT doc FROM sim_order_history WHERE owner=? AND order_id=? ORDER BY seq",
                (self._owner(owner), str(order_id)))]

    # ── gtts ──────────────────────────────────────────────────────────────────────────
    def put_gtt(self, owner: str, doc: dict) -> None:
        o = self._owner(owner)
        cond = doc.get("condition") or {}
        with self._cx() as c:
            c.execute(
                "INSERT INTO sim_gtts(owner, trigger_id, status, exchange, tradingsymbol, created_ms, updated_ms, doc) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(owner, trigger_id) DO UPDATE SET status=excluded.status, "
                "updated_ms=excluded.updated_ms, doc=excluded.doc",
                (o, int(doc["id"]), doc["status"], cond.get("exchange", ""), cond.get("tradingsymbol", ""),
                 int((doc.get("_sim") or {}).get("created_ms") or now_ms()), now_ms(), json.dumps(doc)))

    def get_gtt(self, owner: str, trigger_id: int) -> Optional[dict]:
        with self._cx() as c:
            row = c.execute("SELECT doc FROM sim_gtts WHERE owner=? AND trigger_id=?",
                            (self._owner(owner), int(trigger_id))).fetchone()
            return json.loads(row[0]) if row else None

    def gtts(self, owner: str, *, status: Optional[str] = None) -> List[dict]:
        sql, args = "SELECT doc FROM sim_gtts WHERE owner=?", [self._owner(owner)]
        if status:
            sql += " AND status=?"
            args.append(status)
        with self._cx() as c:
            return [json.loads(r[0]) for r in c.execute(sql + " ORDER BY trigger_id", args)]

    # ── positions ─────────────────────────────────────────────────────────────────────
    def get_position(self, owner: str, exchange: str, tradingsymbol: str, product: str) -> Optional[dict]:
        with self._cx() as c:
            row = c.execute(
                "SELECT * FROM sim_positions WHERE owner=? AND exchange=? AND tradingsymbol=? AND product=?",
                (self._owner(owner), exchange, tradingsymbol, product)).fetchone()
        return self._position(row)

    @staticmethod
    def _position(row) -> Optional[dict]:
        if row is None:
            return None
        out = dict(row)
        out["meta"] = json.loads(out.get("meta") or "{}")
        return out

    def positions(self, owner: str, *, open_only: bool = False) -> List[dict]:
        sql = "SELECT * FROM sim_positions WHERE owner=?" + (" AND quantity<>0" if open_only else "")
        with self._cx() as c:
            return [self._position(r) for r in c.execute(sql + " ORDER BY exchange, tradingsymbol, product",
                                                         (self._owner(owner),))]

    def set_mark(self, owner: str, exchange: str, tradingsymbol: str, product: str, price: float) -> None:
        """Record the latest REAL price seen for a position (never an invented one)."""
        if not price or price <= 0:
            return
        with self._cx() as c:
            c.execute("UPDATE sim_positions SET last_price=?, last_price_ms=? "
                      "WHERE owner=? AND exchange=? AND tradingsymbol=? AND product=?",
                      (float(price), now_ms(), self._owner(owner), exchange, tradingsymbol, product))

    # ── fills ─────────────────────────────────────────────────────────────────────────
    def record_fill(self, owner: str, *, order: dict, quantity: int, price: float, charges: Charges,
                    meta: dict, day: str, mark: Optional[float] = None) -> dict:
        """Book one fill: trade row, charges row, position, cash. Returns the trade document."""
        o = self._owner(owner)
        qty, px = int(quantity), round(float(price), 4)
        side = str(order["transaction_type"]).upper()
        key = (order["exchange"], order["tradingsymbol"], order["product"])
        signed = qty if side == "BUY" else -qty
        with self.transaction():
            self.ensure_account(o)
            pos = self.get_position(o, *key) or {"quantity": 0, "avg_price": 0.0, "instrument_token": 0}
            old, avg = int(pos["quantity"]), float(pos["avg_price"])
            realised = 0.0
            if old == 0 or (old > 0) == (signed > 0):
                new = old + signed
                avg = round((avg * abs(old) + px * qty) / abs(new), 4)
            else:
                closing = min(qty, abs(old))
                realised = round((px - avg) * closing * (1 if old > 0 else -1), 2)
                new = old + signed
                if new == 0:
                    avg = 0.0
                elif (new > 0) != (old > 0):
                    avg = px                                      # flipped: the remainder opens here
            ts = now_ms()
            trade_id = str(900_000_000 + self.next_id("trade"))
            stamp = order.get("exchange_update_timestamp") or ""
            trade = {
                "trade_id": trade_id, "order_id": order["order_id"], "exchange": order["exchange"],
                "tradingsymbol": order["tradingsymbol"], "instrument_token": order.get("instrument_token", 0),
                "product": order["product"], "average_price": px, "quantity": qty,
                "exchange_order_id": order.get("exchange_order_id"), "transaction_type": side,
                "fill_timestamp": stamp, "order_timestamp": order.get("order_timestamp"),
                "exchange_timestamp": stamp,
                "_sim": {"realised": realised, "charges": charges.as_dict(), "tag": order.get("tag"),
                         "day": day, "fill_ms": ts},
            }
            with self._cx() as c:
                c.execute("INSERT INTO sim_trades(owner, trade_id, order_id, day, fill_ms, exchange, tradingsymbol, "
                          "product, side, quantity, price, realised, charges, doc) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (o, trade_id, order["order_id"], day, ts, order["exchange"], order["tradingsymbol"],
                           order["product"], side, qty, px, realised, charges.total, json.dumps(trade)))
                c.execute("INSERT INTO sim_charges(owner, trade_id, order_id, day, ts_ms, exchange, tradingsymbol, "
                          "side, turnover, brokerage, stt, exchange_txn, sebi, stamp, gst, total) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (o, trade_id, order["order_id"], day, ts, order["exchange"], order["tradingsymbol"], side,
                           charges.turnover, charges.brokerage, charges.stt, charges.exchange_txn, charges.sebi,
                           charges.stamp, charges.gst, charges.total))
                c.execute(
                    "INSERT INTO sim_positions(owner, exchange, tradingsymbol, product, instrument_token, quantity, "
                    "avg_price, last_price, last_price_ms, meta, updated_ms) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(owner, exchange, tradingsymbol, product) DO UPDATE SET quantity=excluded.quantity, "
                    "avg_price=excluded.avg_price, last_price=excluded.last_price, "
                    "last_price_ms=excluded.last_price_ms, meta=excluded.meta, updated_ms=excluded.updated_ms",
                    (o, *key, int(order.get("instrument_token") or 0), new, avg,
                     float(mark or px), ts, json.dumps(meta), ts))
            self.adjust_cash(o, realised - charges.total)
            return trade

    # ── trades, charges ───────────────────────────────────────────────────────────────
    def trades(self, owner: str, *, day: Optional[str] = None, order_id: Optional[str] = None) -> List[dict]:
        sql, args = "SELECT doc FROM sim_trades WHERE owner=?", [self._owner(owner)]
        if day:
            sql += " AND day=?"
            args.append(day)
        if order_id:
            sql += " AND order_id=?"
            args.append(str(order_id))
        with self._cx() as c:
            return [json.loads(r[0]) for r in c.execute(sql + " ORDER BY fill_ms, trade_id", args)]

    def charges(self, owner: str, *, day: Optional[str] = None) -> List[dict]:
        sql, args = "SELECT * FROM sim_charges WHERE owner=?", [self._owner(owner)]
        if day:
            sql += " AND day=?"
            args.append(day)
        with self._cx() as c:
            return [dict(r) for r in c.execute(sql + " ORDER BY id", args)]

    # ── account view ──────────────────────────────────────────────────────────────────
    def account_view(self, owner: str, day: str) -> dict:
        """Capital, cash, margin held, available, unrealised, equity and today's realised/charges."""
        o = self._owner(owner)
        acct = self.ensure_account(o)
        pct = self.settings(o).futures_margin_pct
        blocked = unrealised = 0.0
        open_positions = 0
        for pos in self.positions(o, open_only=True):
            open_positions += 1
            qty, avg = int(pos["quantity"]), float(pos["avg_price"])
            last = float(pos["last_price"]) or avg
            blocked += position_margin(dict(pos["meta"], tradingsymbol=pos["tradingsymbol"]), qty, avg,
                                       futures_pct=pct)
            unrealised += (last - avg) * qty
        with self._cx() as c:
            realised = c.execute("SELECT COALESCE(SUM(realised), 0) FROM sim_trades WHERE owner=? AND day=?",
                                 (o, day)).fetchone()[0]
            charges = c.execute("SELECT COALESCE(SUM(total), 0) FROM sim_charges WHERE owner=? AND day=?",
                                (o, day)).fetchone()[0]
            trades_today = c.execute("SELECT COUNT(*) FROM sim_trades WHERE owner=? AND day=?", (o, day)).fetchone()[0]
            open_orders = c.execute("SELECT COUNT(*) FROM sim_orders WHERE owner=? AND status IN (?,?)",
                                    (o, *OPEN_STATUSES)).fetchone()[0]
            active_gtts = c.execute("SELECT COUNT(*) FROM sim_gtts WHERE owner=? AND status=?",
                                    (o, ACTIVE_GTT)).fetchone()[0]
        cash, capital = float(acct["cash"]), float(acct["capital"])
        available = round(cash - blocked - max(0.0, -unrealised), 2)
        equity = round(cash + unrealised, 2)
        return {
            "owner": o, "capital": capital, "cash": round(cash, 2), "blocked_margin": round(blocked, 2),
            "available": available, "unrealised": round(unrealised, 2), "equity": equity,
            "realised_today": round(float(realised), 2), "charges_today": round(float(charges), 2),
            "net_realised_today": round(float(realised) - float(charges), 2),
            "pnl_total": round(equity - capital, 2),
            "return_pct": round((equity - capital) / capital * 100.0, 4) if capital else 0.0,
            "open_positions": open_positions, "open_orders": int(open_orders),
            "active_gtts": int(active_gtts), "trades_today": int(trades_today),
        }

    def owners_with_open_state(self) -> List[str]:
        with self._cx() as c:
            rows = c.execute(
                "SELECT owner FROM sim_orders WHERE status IN (?,?) UNION "
                "SELECT owner FROM sim_gtts WHERE status=? UNION "
                "SELECT owner FROM sim_positions WHERE quantity<>0", (*OPEN_STATUSES, ACTIVE_GTT)).fetchall()
        return sorted(r[0] for r in rows)

    # ── equity snapshots ──────────────────────────────────────────────────────────────
    def add_snapshot(self, owner: str, view: dict, *, ts_ms: Optional[int] = None) -> None:
        with self._cx() as c:
            c.execute("INSERT OR REPLACE INTO sim_equity(owner, ts_ms, cash, blocked, unrealised, equity, "
                      "realised_day, charges_day, positions) VALUES(?,?,?,?,?,?,?,?,?)",
                      (self._owner(owner), int(ts_ms or now_ms()), view["cash"], view["blocked_margin"],
                       view["unrealised"], view["equity"], view["realised_today"], view["charges_today"],
                       view["open_positions"]))

    def snapshots(self, owner: str, *, since_ms: int = 0, limit: int = 5000) -> List[dict]:
        with self._cx() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM sim_equity WHERE owner=? AND ts_ms>=? ORDER BY ts_ms LIMIT ?",
                (self._owner(owner), int(since_ms), int(limit)))]


_default: Optional[SimLedger] = None


def get_ledger() -> SimLedger:
    """The process-wide ledger over the application database."""
    global _default
    if _default is None:
        _default = SimLedger()
    return _default


__all__ = [
    "IST", "DEFAULT_CAPITAL", "OPEN_STATUSES", "ACTIVE_GTT", "SettingsError", "SimSettings", "SimLedger",
    "get_ledger", "position_margin", "public", "now_ms",
]
