"""Durable registry of Snapback's own live positions.

Snapback owns a position schema of its own: one bought option plus, on the swing
lane, a futures hedge that must live and die with it. The shared Kite-engine
registry cannot hold that (it would attach SuperTrend stop/target semantics to a
Snapback trade), and the canonical order journal only knows single orders. So the
strategy keeps its own row per plan, written the moment a leg is CONFIRMED filled.

Three things read this registry and must be able to rely on it:

* the exit manager, which owns stops, trails, targets, time exits and the atomic
  closing of both legs;
* the broker-exposure observer, which must recognise these positions (and the
  protective GTT recorded here) as Sterling's own rather than as foreign exposure;
* the live-runtime boot check, which asks every registry for positions whose
  protection is not confirmed.

Every write is a read-modify-write inside ``BEGIN IMMEDIATE``, so a tick-driven
exit and a recovery pass cannot overwrite each other's progress.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from app.core.state_paths import state_root
from app.services import trade_journal_adapters as _journal

log = logging.getLogger(__name__)

DB_ENV = "STERLING_SNAPBACK_LIVE_DB"
DEFAULT_DB = Path("data") / "snapback" / "live_positions.db"

ENTERING = "ENTERING"       # option leg confirmed; hedge / protection still being established
OPEN = "OPEN"
EXITING = "EXITING"         # an exit is in flight; exit order ids are durable
RECONCILING = "RECONCILING" # the book and the broker disagree; no automatic action
CLOSED = "CLOSED"

#: States in which the position still exists and is managed.
LIVE_STATES = frozenset({ENTERING, OPEN, EXITING, RECONCILING})

PROTECTION_NONE = "NONE"
PROTECTION_ACTIVE = "ACTIVE"
PROTECTION_FAILED = "FAILED"
PROTECTION_UNKNOWN = "UNKNOWN"


def db_path() -> Path:
    configured = (os.environ.get(DB_ENV) or "").strip()
    return Path(configured).expanduser() if configured else state_root() / DEFAULT_DB


@dataclass
class LivePosition:
    position_id: str                 # == the plan id
    uid: str
    account_id: str
    signal_id: str
    lane: str                        # "swing" | "scalp"
    underlying: str
    side: str                        # fade_up | fade_down
    option_type: str                 # CE | PE
    option_exchange: str
    option_symbol: str

    option_token: int = 0
    lot_size: int = 0
    option_qty: int = 0
    option_entry_price: float = 0.0
    option_entry_order_id: str = ""
    option_expiry: str = ""

    hedge_exchange: str = ""
    hedge_symbol: str = ""
    hedge_side: str = ""             # BUY / SELL: the side that OPENED the hedge
    hedge_qty: int = 0
    hedge_entry_price: float = 0.0
    hedge_entry_order_id: str = ""
    hedge_lot_size: int = 0

    product: str = "NRML"
    tick_size: float = 0.05
    #: Exit rules frozen at entry (premium stop %, hold days, runner, scalp points...).
    rules: Dict[str, Any] = field(default_factory=dict)

    status: str = ENTERING
    stop_premium: float = 0.0        # the initial protective stop, as a price
    desired_stop: float = 0.0        # the ratcheted stop; only ever rises
    high_water: float = 0.0
    peak_close: float = 0.0          # runner peak, advanced from end-of-day marks only
    is_runner: bool = False
    phase: str = "OPEN_SCALP"
    last_eod_session: str = ""       # IST date of the last end-of-day rule pass

    gtt_id: int = 0
    gtt_stop: float = 0.0
    gtt_qty: int = 0
    protection_state: str = PROTECTION_NONE
    #: The placement fence. A broker stop is placed with a POST whose reply can be lost (a
    #: timeout, a 5xx): the trigger may then rest at the broker with no id on file. This is
    #: latched durably BEFORE the POST and lifted only when the broker answers or its trigger
    #: book proves the outcome; while it is latched no second stop is ever sent.
    gtt_pending: bool = False
    gtt_pending_ms: int = 0
    gtt_pending_qty: int = 0
    gtt_pending_trigger: float = 0.0

    exit_reason: str = ""
    exit_started_ms: int = 0
    #: Option exit progress. ``option_exit_order_id`` is the order currently being
    #: resolved ("" when none is in flight); ``option_exit_qty`` / ``_value`` are the
    #: CONFIRMED cumulative quantity and rupee value across every exit order.
    option_exit_order_id: str = ""
    option_exit_requested: int = 0
    option_exit_qty: int = 0
    option_exit_value: float = 0.0
    option_exit_price: float = 0.0
    #: Units of ``option_exit_qty`` that left the account without a price Sterling
    #: observed (sold in the broker app, or the broker no longer held them). They
    #: count as exited so the hedge can close, but carry no value.
    option_exit_unknown_qty: int = 0
    #: Ids of every option exit order whose fill is already counted in ``option_exit_qty``
    #: (market exits and the broker stop's own child). A SELL on the contract that is not in
    #: here and is not the stop's own child order is somebody else's, never this position's.
    option_exit_booked_ids: List[str] = field(default_factory=list)
    hedge_exit_booked_ids: List[str] = field(default_factory=list)
    #: Per-order cumulative quantities at the time a canonical terminal ACK
    #: was durably projected. Older rows without this field remain readable,
    #: but their ambiguous in-flight ACKs require manual reconciliation.
    option_exit_booked_quantities: Dict[str, int] = field(default_factory=dict)
    hedge_exit_booked_quantities: Dict[str, int] = field(default_factory=dict)
    #: A leg the broker's positions book does not show as held. One reading is not proof (a
    #: partial or empty reply is as likely), so the shortfall is only acted on once it has been
    #: seen on several readings over a minute; any reading that finds the leg held resets it.
    shortfall_leg: str = ""
    shortfall_obs: int = 0
    shortfall_first_ms: int = 0
    #: When the broker stop's own SELL was first seen resting unfilled (0 = never).
    stop_fill_pending_ms: int = 0
    hedge_exit_order_id: str = ""
    hedge_exit_requested: int = 0
    hedge_exit_qty: int = 0
    hedge_exit_value: float = 0.0
    hedge_exit_price: float = 0.0
    exit_attempts: int = 0
    realised_pnl: float = 0.0
    flags: List[str] = field(default_factory=list)
    note: str = ""

    opened_ms: int = 0
    entry_day: str = ""
    updated_ms: int = 0
    closed_ms: int = 0

    # ------------------------------------------------------------- registry
    # The attribute names below are what the shared registry readers look for.

    @property
    def symbol(self) -> str:
        return self.option_symbol

    @property
    def exchange(self) -> str:
        return self.option_exchange

    @property
    def is_open(self) -> bool:
        return self.status in LIVE_STATES

    @property
    def protection_pending(self) -> bool:
        """An option is held and durable state cannot prove full broker protection.

        The state label alone is not evidence. A partially written/corrupted row
        that says ACTIVE but lacks the trigger id, trigger level or exact remaining
        quantity must fail closed so boot/reconciliation cannot normalize an
        unprotected or over-sized stop.
        """
        remaining = max(0, int(self.option_qty or 0) - int(self.option_exit_qty or 0))
        if not self.is_open or self.status == EXITING or remaining <= 0:
            return False
        return not (
            self.protection_state == PROTECTION_ACTIVE
            and int(self.gtt_id or 0) > 0
            and float(self.gtt_stop or 0.0) > 0
            and int(self.gtt_qty or 0) == remaining
        )

    @property
    def has_hedge(self) -> bool:
        return bool(self.hedge_symbol) and self.hedge_qty > 0

    # The shared broker-exposure observer attributes broker orders to a position by
    # these attribute names (as it does for every other strategy's registry).
    @property
    def order_id(self) -> str:
        return self.option_entry_order_id

    @property
    def exit_order_id(self) -> str:
        return self.option_exit_order_id

    @property
    def qty_by_order(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for oid, qty in (
            (self.option_entry_order_id, self.option_qty),
            (self.hedge_entry_order_id, self.hedge_qty),
            (self.option_exit_order_id, self.option_exit_requested),
            (self.hedge_exit_order_id, self.hedge_exit_requested),
        ):
            if oid:
                out[str(oid)] = int(qty or 0)
        return out

    def as_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["symbol"] = self.symbol
        out["exchange"] = self.exchange
        out["protection_pending"] = self.protection_pending
        out["has_hedge"] = self.has_hedge
        return out

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "LivePosition":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in dict(raw).items() if k in known})


def _now_ms() -> int:
    return int(time.time() * 1000)


class LivePositionStore:
    def __init__(self, path: Optional[Path | str] = None) -> None:
        self.path = Path(path) if path else db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=15.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    def _init(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS snapback_live_positions (
                    position_id TEXT PRIMARY KEY,
                    uid         TEXT NOT NULL,
                    account_id  TEXT NOT NULL,
                    status      TEXT NOT NULL,
                    signal_id   TEXT NOT NULL,
                    option_symbol TEXT NOT NULL,
                    updated_ms  INTEGER NOT NULL,
                    data_json   TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_snapback_live_uid_status "
                "ON snapback_live_positions(uid, status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_snapback_live_signal "
                "ON snapback_live_positions(signal_id)"
            )
        finally:
            conn.close()

    # ---------------------------------------------------------------- reads

    @staticmethod
    def _hydrate(row: sqlite3.Row) -> LivePosition:
        return LivePosition.from_dict(json.loads(row["data_json"]))

    def get(self, position_id: str) -> Optional[LivePosition]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT data_json FROM snapback_live_positions WHERE position_id=?",
                (position_id,),
            ).fetchone()
        finally:
            conn.close()
        return self._hydrate(row) if row else None

    def by_signal(self, signal_id: str) -> Optional[LivePosition]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT data_json FROM snapback_live_positions WHERE signal_id=? "
                "ORDER BY updated_ms DESC LIMIT 1",
                (signal_id,),
            ).fetchone()
        finally:
            conn.close()
        return self._hydrate(row) if row else None

    def list(self, uid: str, *, statuses: Optional[Iterable[str]] = None,
             account_id: str = "") -> List[LivePosition]:
        sql = "SELECT data_json FROM snapback_live_positions WHERE uid=?"
        args: List[Any] = [uid]
        wanted = list(statuses) if statuses is not None else None
        if wanted:
            sql += " AND status IN (%s)" % ",".join("?" * len(wanted))
            args.extend(wanted)
        if account_id:
            sql += " AND account_id=?"
            args.append(account_id)
        sql += " ORDER BY updated_ms"
        conn = self._connect()
        try:
            rows = conn.execute(sql, args).fetchall()
        finally:
            conn.close()
        return [self._hydrate(r) for r in rows]

    def open_positions(self, uid: str, account_id: str = "") -> List[LivePosition]:
        return self.list(uid, statuses=LIVE_STATES, account_id=account_id)

    def known_uids(self) -> List[str]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT DISTINCT uid FROM snapback_live_positions"
            ).fetchall()
        finally:
            conn.close()
        return sorted(str(r["uid"]) for r in rows if str(r["uid"] or "").strip())

    # --------------------------------------------------------------- writes

    def put(self, pos: LivePosition) -> LivePosition:
        """Replace only the same owner/account/contract; never transfer identity."""
        pos.updated_ms = _now_ms()
        payload = json.dumps(pos.as_dict(), default=str, separators=(",", ":"))
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute(
                """
                INSERT INTO snapback_live_positions
                    (position_id, uid, account_id, status, signal_id, option_symbol,
                     updated_ms, data_json)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(position_id) DO UPDATE SET
                    status=excluded.status, signal_id=excluded.signal_id,
                    updated_ms=excluded.updated_ms, data_json=excluded.data_json
                WHERE snapback_live_positions.uid=excluded.uid
                  AND snapback_live_positions.account_id=excluded.account_id
                  AND snapback_live_positions.option_symbol=excluded.option_symbol
                """,
                (pos.position_id, pos.uid, pos.account_id, pos.status, pos.signal_id,
                 pos.option_symbol, pos.updated_ms, payload),
            )
            if changed.rowcount != 1:
                raise PermissionError("snapback_position_owner_or_contract_collision")
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()
        _journal.sb_sync(pos)   # trade journal: derived from this row, never raises
        return pos

    def insert_new(self, pos: LivePosition) -> LivePosition:
        """Create a first-fill position without replacing any existing owner.

        A concurrent fill/restart can beat a prior get() read. put() is a
        deliberate replace operation used by existing callers; first booking
        instead needs an exclusive, fail-closed insert under the DB lock.
        """
        pos.updated_ms = _now_ms()
        payload = json.dumps(pos.as_dict(), default=str, separators=(",", ":"))
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO snapback_live_positions
                    (position_id, uid, account_id, status, signal_id, option_symbol,
                     updated_ms, data_json)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (pos.position_id, pos.uid, pos.account_id, pos.status, pos.signal_id,
                 pos.option_symbol, pos.updated_ms, payload),
            )
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()
        _journal.sb_sync(pos)
        return pos

    def mutate(self, position_id: str,
               fn: Callable[[LivePosition], Optional[bool]]) -> Optional[LivePosition]:
        """Apply ``fn`` to the freshest row inside one write transaction.

        ``fn`` may return ``False`` to abandon the change. Returns the stored row
        (or ``None`` when no such position exists).
        """
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT data_json FROM snapback_live_positions WHERE position_id=?",
                (position_id,),
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return None
            pos = self._hydrate(row)
            if fn(pos) is False:
                conn.execute("ROLLBACK")
                return pos
            pos.updated_ms = _now_ms()
            conn.execute(
                "UPDATE snapback_live_positions SET uid=?, account_id=?, status=?, "
                "signal_id=?, option_symbol=?, updated_ms=?, data_json=? "
                "WHERE position_id=?",
                (pos.uid, pos.account_id, pos.status, pos.signal_id,
                 pos.option_symbol, pos.updated_ms,
                 json.dumps(pos.as_dict(), default=str, separators=(",", ":")),
                 position_id),
            )
            conn.execute("COMMIT")
            _journal.sb_sync(pos)   # trade journal: derived from this row, never raises
            return pos
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

    def add_flag(self, position_id: str, flag: str, note: str = "") -> Optional[LivePosition]:
        def apply(pos: LivePosition) -> None:
            if flag not in pos.flags:
                pos.flags.append(flag)
            if note:
                pos.note = note

        return self.mutate(position_id, apply)


# --------------------------------------------------------------------------
# Module-level default store, shaped like the other strategy registries
# (``open_positions(uid)``, ``known_uids()``) so the shared readers can treat
# it exactly like them.
# --------------------------------------------------------------------------

_default: Optional[LivePositionStore] = None
_default_path: Optional[Path] = None


def default_store() -> LivePositionStore:
    """The process-wide store. Re-resolved when the configured path changes."""
    global _default, _default_path
    path = db_path()
    if _default is None or _default_path != path:
        _default = LivePositionStore(path)
        _default_path = path
    return _default


def open_positions(uid: str) -> List[LivePosition]:
    return default_store().open_positions(uid)


def known_uids() -> List[str]:
    return default_store().known_uids()
