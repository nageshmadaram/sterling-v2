"""Durable, fail-closed Kite order intents and immutable broker fill events."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite, isclose
import time
from uuid import uuid4
from typing import Any, Dict, List, Optional

from app.services import db

TERMINAL = {"FILLED", "REJECTED", "CANCELLED"}
UNRESOLVED = {"RESERVED", "SUBMITTING", "UNKNOWN", "SUBMITTED", "PARTIAL"}
# Reserved for the separately gated, offline-only Gamma review-child writer.
_GAMMA_REVIEW_MARKER = "gamma_terminal_review_only"
_RESET_FENCE_PREFIX = "simulation_reset_fence_v1:"
_RESET_CHECKPOINT_PREFIX = "simulation_reset_checkpoint_v1:"
_RESET_STAGES = (
    "fenced", "disarmed", "virtual_ledger", "journal_rows", "order_books",
    "ema_trend_books", "snapback_books", "config_rows", "memory", "tenant",
    "control_plane", "complete",
)


@dataclass(frozen=True)
class Intent:
    intent_key: str
    uid: str
    account_id: str
    strategy_id: str
    generation_id: str
    signal_id: str
    exchange: str
    symbol: str
    side: str
    quantity: int
    tag: str
    state: str
    order_id: str
    payload: Dict[str, Any]
    error: str
    created_ms: int
    updated_ms: int
    capital_required: float = 0
    filled_quantity: int = 0
    filled_value: float = 0
    reconciliation_required: bool = False
    projection_pending: bool = False
    projection_version: int = 0


@dataclass(frozen=True)
class Observation:
    intent: Intent
    accepted: bool
    delta_quantity: int = 0
    delta_value: float = 0
    reason: str = ""

    @property
    def reconciliation_required(self) -> bool:
        return self.intent.reconciliation_required

    @property
    def state(self) -> str:
        return self.intent.state


@contextmanager
def _transaction():
    _ready()
    with db._conn() as conn:
        conn.execute("PRAGMA synchronous=FULL")
        if conn.execute("PRAGMA synchronous").fetchone()[0] != 2:
            raise RuntimeError("durable_order_journal_full_sync_unavailable")
        conn.execute("BEGIN IMMEDIATE")
        yield conn


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _reset_fence(conn, uid: str) -> Optional[dict]:
    """Read a durable per-tenant reset state from the canonical SQLite store.

    Missing means idle. Existing malformed records are never interpreted as idle.
    Called under the same BEGIN IMMEDIATE writer lock as review evidence writes.
    """
    row = conn.execute(
        "SELECT value FROM system_config WHERE key=?", (_RESET_FENCE_PREFIX + uid,),
    ).fetchone()
    if row is None:
        return None
    try:
        value = json.loads(row["value"])
        if (type(value) is not dict or set(value) != {"state", "token"}
                or value["state"] not in {"ACTIVE", "FAILED"}
                or type(value["token"]) is not str or len(value["token"]) != 32
                or any(c not in "0123456789abcdef" for c in value["token"])):
            raise ValueError("invalid_marker")
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError("simulation_reset_fence_invalid") from exc
    return value


def _reset_checkpoint(conn, uid: str) -> Optional[dict]:
    """Validate a durable reset checkpoint, including internal token (never exposed)."""
    row = conn.execute(
        "SELECT value FROM system_config WHERE key=?", (_RESET_CHECKPOINT_PREFIX + uid,),
    ).fetchone()
    if row is None:
        return None  # older fence versions had no checkpoint
    try:
        cp = json.loads(row["value"])
        if (type(cp) is not dict or set(cp) != {"state", "stage", "token"}
                or cp["state"] not in {"ACTIVE", "FAILED", "COMPLETE"}
                or cp["stage"] not in _RESET_STAGES
                or type(cp["token"]) is not str or len(cp["token"]) != 32
                or any(c not in "0123456789abcdef" for c in cp["token"])
                or ((cp["stage"] == "complete") != (cp["state"] == "COMPLETE"))):
            raise ValueError("invalid_checkpoint")
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError("simulation_reset_checkpoint_invalid") from exc
    return cp


def _assert_reset_checkpoint_consistent(fence: Optional[dict], cp: Optional[dict]) -> None:
    if cp is None:
        return  # backwards-compatible read-only diagnosis of a legacy fence
    if fence is None:
        if cp["state"] != "COMPLETE":
            raise ValueError("simulation_reset_checkpoint_inconsistent")
    elif cp["token"] != fence["token"] or cp["state"] != fence["state"]:
        raise ValueError("simulation_reset_checkpoint_inconsistent")


def simulation_reset_checkpoint_state(uid: str) -> dict:
    """Read-only forensic stage; never disclose a reset token or release a fence."""
    _text(uid, "uid")
    _ready()
    with db._conn() as conn:
        fence = _reset_fence(conn, uid)
        cp = _reset_checkpoint(conn, uid)
        _assert_reset_checkpoint_consistent(fence, cp)
    if cp is not None:
        return {"state": cp["state"], "stage": cp["stage"]}
    if fence is not None:
        return {"state": "UNKNOWN", "stage": "unknown"}
    return {"state": "CLEAR", "stage": "none"}


def checkpoint_simulation_reset(uid: str, token: str, stage: str) -> None:
    """Durably acknowledge only the contiguous, successfully committed reset prefix.

    It is deliberately not a cross-database transaction. A checkpoint follows
    each externally completed step, so a process death may leave its value
    behind the work already done but never prove future steps complete.
    """
    _text(uid, "uid")
    _text(token, "reset_token")
    if stage not in _RESET_STAGES or stage == "complete":
        raise ValueError("invalid_simulation_reset_stage")
    with _transaction() as conn:
        fence = _reset_fence(conn, uid)
        cp = _reset_checkpoint(conn, uid)
        _assert_reset_checkpoint_consistent(fence, cp)
        if (fence is None or fence["state"] != "ACTIVE" or fence["token"] != token
                or cp is None or cp["state"] != "ACTIVE"):
            raise ValueError("simulation_reset_fence_mismatch")
        if _RESET_STAGES.index(stage) < _RESET_STAGES.index(cp["stage"]):
            raise ValueError("simulation_reset_checkpoint_regression")
        conn.execute(
            "UPDATE system_config SET value=? WHERE key=?",
            (_json({"state": "ACTIVE", "stage": stage, "token": token}), _RESET_CHECKPOINT_PREFIX + uid),
        )


def _require_no_simulation_reset(conn, uid: str) -> None:
    """Refuse a fenced writer or corrupted/inconsistent checkpoint evidence."""
    fence = _reset_fence(conn, uid)
    _assert_reset_checkpoint_consistent(fence, _reset_checkpoint(conn, uid))
    if fence is not None:
        raise ValueError("simulation_reset_fenced")


def assert_simulation_boot_permitted(uid: str, *, reset_token: Optional[str] = None) -> None:
    """Refuse ordinary virtual-tenant bootstrap while reset recovery is fenced.

    The coordinator can bootstrap during its own ACTIVE reset only by presenting
    its exact durable token. This checks admission under SQLite's writer lock;
    it is not an ACID transaction across the separately stored virtual ledger.
    """
    _text(uid, "uid")
    with _transaction() as conn:
        fence = _reset_fence(conn, uid)
        _assert_reset_checkpoint_consistent(fence, _reset_checkpoint(conn, uid))
        if fence is None:
            if reset_token is not None:
                raise ValueError("simulation_reset_fenced")
            return
        if (fence["state"] != "ACTIVE" or not reset_token
                or fence["token"] != reset_token):
            raise ValueError("simulation_reset_fenced")


def simulation_reset_fence_state(uid: str) -> Optional[str]:
    """Read-only operational diagnosis; never grants execution or release."""
    _text(uid, "uid")
    _ready()
    with db._conn() as conn:
        fence = _reset_fence(conn, uid)
        checkpoint = _reset_checkpoint(conn, uid)
        _assert_reset_checkpoint_consistent(fence, checkpoint)
    return fence["state"] if fence else None


def begin_simulation_reset(uid: str) -> str:
    """Atomically validate immutable evidence and durably fence review writers.

    A failed prior reset can be retried only by a new explicit reset call; a
    stale ACTIVE marker, including after process death, requires manual review.
    """
    from app.services.simulation.identity import is_sim_uid

    _text(uid, "uid")
    if not is_sim_uid(uid):
        raise ValueError("simulation_reset_requires_virtual_uid")
    token = uuid4().hex
    with _transaction() as conn:
        _assert_tenant_cleanup_safe(conn, uid)
        prior = _reset_fence(conn, uid)
        checkpoint = _reset_checkpoint(conn, uid)
        _assert_reset_checkpoint_consistent(prior, checkpoint)
        if prior is not None and prior["state"] == "ACTIVE":
            raise ValueError("simulation_reset_in_progress")
        conn.execute(
            "INSERT INTO system_config(key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (_RESET_FENCE_PREFIX + uid, _json({"state": "ACTIVE", "token": token})),
        )
        conn.execute(
            "INSERT INTO system_config(key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (_RESET_CHECKPOINT_PREFIX + uid, _json({"state": "ACTIVE", "stage": "fenced", "token": token})),
        )
    return token


def finish_simulation_reset(uid: str, token: str, *, success: bool) -> None:
    """Conclude a fenced reset. Failure remains durable and fail-closed."""
    _text(uid, "uid")
    _text(token, "reset_token")
    if type(success) is not bool:
        raise ValueError("invalid_reset_outcome")
    with _transaction() as conn:
        current = _reset_fence(conn, uid)
        checkpoint = _reset_checkpoint(conn, uid)
        _assert_reset_checkpoint_consistent(current, checkpoint)
        if current is None or current["state"] != "ACTIVE" or current["token"] != token:
            raise ValueError("simulation_reset_fence_mismatch")
        if success:
            # The caller must prove that the full required reset protocol has
            # reached its final confirmed stage before removing the fence.
            # A token alone proves identity, NOT cleanup completeness.
            if checkpoint is None or checkpoint["stage"] != "control_plane":
                raise ValueError("simulation_reset_incomplete")
            conn.execute("DELETE FROM system_config WHERE key=?", (_RESET_FENCE_PREFIX + uid,))
        else:
            conn.execute("UPDATE system_config SET value=? WHERE key=?", (
                _json({"state": "FAILED", "token": token}), _RESET_FENCE_PREFIX + uid,
            ))
        if checkpoint is not None:
            state = "COMPLETE" if success else "FAILED"
            stage = "complete" if success else checkpoint["stage"]
            conn.execute("UPDATE system_config SET value=? WHERE key=?", (
                _json({"state": state, "stage": stage, "token": token}),
                _RESET_CHECKPOINT_PREFIX + uid,
            ))


def _text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"invalid_{label}")
    return value


def _quantity(value: int, *, zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < (0 if zero else 1):
        raise ValueError("invalid_quantity")
    return value


def _amount(value: float, label: str, *, zero: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"invalid_{label}")
    value = float(value)
    if not isfinite(value) or value < 0 or (value == 0 and not zero):
        raise ValueError(f"invalid_{label}")
    return value


def _ready() -> None:
    if not db.is_available():
        raise RuntimeError("durable_order_journal_unavailable")


def _row(row) -> Intent:
    return Intent(
        intent_key=row["intent_key"], uid=row["uid"], account_id=row["account_id"],
        strategy_id=row["strategy_id"], generation_id=row["generation_id"],
        signal_id=row["signal_id"], exchange=row["exchange"], symbol=row["symbol"],
        side=row["side"], quantity=int(row["quantity"]), tag=row["tag"], state=row["state"],
        order_id=row["order_id"], payload=json.loads(row["payload_json"] or "{}"),
        error=row["error"], created_ms=int(row["created_ms"]), updated_ms=int(row["updated_ms"]),
        capital_required=float(row["capital_required"]), filled_quantity=int(row["observed_quantity"]),
        filled_value=float(row["observed_value"]), reconciliation_required=bool(row["reconciliation_required"]),
        projection_pending=bool(row["projection_pending"]), projection_version=int(row["projection_version"]))


def _get(conn, key: str) -> Intent:
    row = conn.execute("SELECT * FROM kite_order_intents WHERE intent_key=?", (key,)).fetchone()
    if row is None:
        raise KeyError(key)
    return _row(row)


def get(intent_key: str) -> Optional[Intent]:
    """Read one canonical intent by key without reserving or mutating it."""
    _ready()
    _text(intent_key, "intent_key")
    with db._conn() as conn:
        row = conn.execute(
            "SELECT * FROM kite_order_intents WHERE intent_key=?",
            (intent_key,),
        ).fetchone()
    return _row(row) if row is not None else None


def make_key(*, uid: str, account_id: str, strategy_id: str, generation_id: str,
             signal_id: str, exchange: str, symbol: str, side: str) -> str:
    # Configuration generation is metadata, not permission to execute a signal twice.
    values = (uid, account_id, strategy_id, signal_id, exchange, symbol, side)
    for label, value in zip(("uid", "account_id", "strategy_id", "signal_id", "exchange", "symbol", "side"), values):
        _text(value, label)
    if side not in {"BUY", "SELL"}:
        raise ValueError("invalid_side")
    return sha256(_json(["kite-intent-v2", *values]).encode()).hexdigest()


def identity_exists(*, uid: str, account_id: str, strategy_id: str,
                    signal_id: str, exchange: str, symbol: str,
                    side: str) -> bool:
    """Read whether an immutable canonical identity was already reserved.

    This mirrors the legacy-key-aware identity columns queried by ``reserve``;
    it does NOT create or authorize an intent. Callers must still let the
    transactional reservation enforce uniqueness at submission time. A DB
    failure is propagated so pre-disarm callers can fail closed.
    """
    _ready()
    make_key(uid=uid, account_id=account_id, strategy_id=strategy_id,
             generation_id="read-only", signal_id=signal_id, exchange=exchange,
             symbol=symbol, side=side)
    with db._conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM kite_order_intents WHERE uid=? AND account_id=? "
            "AND strategy_id=? AND signal_id=? AND exchange=? "
            "AND symbol=? AND side=? LIMIT 1",
            (uid, account_id, strategy_id, signal_id, exchange, symbol, side),
        ).fetchone()
    return row is not None


def _mode_admission(conn, *, uid, account_id, strategy_id, payload, now):
    policy = (payload or {}).get('mode_policy')
    if not policy:
        return
    from datetime import datetime
    from app.core.mode_execution import IST
    from app.core.horizon import canonical_mode, timeline_for
    mode = canonical_mode(policy['mode']).value
    limits = timeline_for(mode)
    day_start = int(datetime.fromtimestamp(now / 1000, IST).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
    history = conn.execute('SELECT * FROM kite_order_intents WHERE uid=? AND account_id=? AND strategy_id=?',
                           (uid, account_id, strategy_id)).fetchall()
    today = 0
    active = set()
    for row in history:
        recorded = json.loads(row['payload_json'] or '{}').get('mode_policy') or {}
        if recorded.get('mode') != mode:
            continue
        if row['state'] in ('REJECTED', 'CANCELLED') and not row['observed_quantity']:
            continue
        if row['created_ms'] >= day_start:
            today += 1
        if row['state'] in UNRESOLVED or row['reconciliation_required'] or row['projection_pending']:
            active.add((row['exchange'], row['symbol']))
    if today >= limits.max_new_entries_per_session:
        raise ValueError('mode_daily_entry_limit')
    if strategy_id == 'sterling-kite':
        from app.services.kite_engine.positions import open_positions
        held = open_positions(uid)
        active.update((p.exchange, p.symbol) for p in held
                      if p.account_id == account_id and p.horizon_mode == mode)
    elif strategy_id == 'snapback':
        from app.services.snapback_live_positions import default_store
        held = default_store().open_positions(uid)
        active.update((p.option_exchange, p.option_symbol) for p in held
                      if p.account_id == account_id and (p.rules.get('mode_policy') or {}).get('mode') == mode)
    if len(active) >= limits.max_concurrent_positions:
        raise ValueError('mode_concurrent_position_limit')


def reserve(*, uid: str, account_id: str, strategy_id: str, generation_id: str,
            signal_id: str, exchange: str, symbol: str, side: str, quantity: int,
            payload: Optional[Dict[str, Any]] = None, capital_required: float = 0,
            available_capital: Optional[float] = None) -> Intent:
    """Reserve immutable intent and pending account capital atomically.

    available_capital must be fresh broker evidence. Unresolved reservations are
    deducted conservatively while holding SQLite's account-wide write lock.
    """
    _quantity(quantity)
    _text(generation_id, "generation_id")
    capital_required = _amount(capital_required, "capital_required", zero=True)
    if capital_required and available_capital is None:
        raise ValueError("available_capital_required")
    if available_capital is not None:
        available_capital = _amount(available_capital, "available_capital", zero=True)
    if payload is not None and not isinstance(payload, dict):
        raise ValueError("invalid_payload")
    # Do not let the general-purpose reservation path mint permanent review
    # evidence. The offline child writer validates a disposable database, a
    # durable review hold and the exact parent lineage before INSERT itself.
    # Even false/null/non-boolean markers are reserved to prevent ambiguous
    # rows from being treated as ordinary executable intents on restart.
    if payload is not None and _GAMMA_REVIEW_MARKER in payload:
        raise ValueError("reserved_gamma_review_marker")
    encoded = _json(payload or {})
    key = make_key(uid=uid, account_id=account_id, strategy_id=strategy_id,
                   generation_id=generation_id, signal_id=signal_id, exchange=exchange,
                   symbol=symbol, side=side)
    now = int(time.time() * 1000)
    with _transaction() as conn:
        _require_no_simulation_reset(conn, uid)
        # Match legacy draft identities too; changing key encoding must not resend.
        rows = conn.execute("""SELECT * FROM kite_order_intents WHERE uid=? AND account_id=?
            AND strategy_id=? AND signal_id=? AND exchange=? AND symbol=? AND side=?""",
            (uid, account_id, strategy_id, signal_id, exchange, symbol, side)).fetchall()
        if rows:
            if len(rows) != 1:
                raise ValueError("ambiguous_existing_intents")
            existing = _row(rows[0])
            if (existing.quantity != quantity or _json(existing.payload) != encoded
                    or existing.capital_required != capital_required):
                raise ValueError("immutable_intent_conflict")
            return existing
        _mode_admission(conn, uid=uid, account_id=account_id, strategy_id=strategy_id,
                        payload=payload, now=now)
        if capital_required:
            marks = ",".join("?" for _ in UNRESOLVED)
            held = conn.execute(f"""SELECT COALESCE(SUM(capital_required),0)
                FROM kite_order_intents WHERE account_id=?
                AND (state IN ({marks}) OR reconciliation_required=1)""",
                (account_id, *sorted(UNRESOLVED))).fetchone()[0]
            if capital_required + held > available_capital:
                raise ValueError("insufficient_unreserved_capital")
        conn.execute("""INSERT INTO kite_order_intents
            (intent_key,uid,account_id,strategy_id,generation_id,signal_id,exchange,symbol,
             side,quantity,tag,state,payload_json,capital_required,created_ms,updated_ms)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (key, uid, account_id, strategy_id, generation_id, signal_id, exchange,
             symbol, side, quantity, "ke" + key[:18], "RESERVED", encoded,
             capital_required, now, now))
        return _get(conn, key)


def _require_mutable_intent(intent: Intent) -> None:
    """Review-only Gamma child rows are immutable, not just non-submittable.

    A fabricated ACK/observation, projection acknowledgement or cancellation
    must never alter the frozen offline evidence or change its replay outcome.
    This check runs inside the same journal transaction as the mutation.
    """
    if _GAMMA_REVIEW_MARKER in intent.payload:
        raise ValueError("gamma_offline_review_intent_immutable")


def claim_submission(intent_key: str) -> bool:
    """Only the winning durable RESERVED→SUBMITTING CAS may call the broker."""
    with _transaction() as conn:
        intent = _get(conn, intent_key)
        _require_no_simulation_reset(conn, intent.uid)
        # Offline canonical child prototypes are durable review evidence, never
        # executable broker orders. Even a direct call to claim_submission must
        # not turn one into SUBMITTING. This is defense in depth on the only
        # canonical RESERVED -> SUBMITTING broker-send admission transition.
        if _GAMMA_REVIEW_MARKER in intent.payload:
            return False
        changed = conn.execute("""UPDATE kite_order_intents SET state='SUBMITTING',updated_ms=?
            WHERE intent_key=? AND state='RESERVED' AND reconciliation_required=0""",
            (int(time.time() * 1000), intent_key)).rowcount
        return changed == 1


def _check_order_id(conn, intent: Intent, order_id: str) -> str:
    if order_id:
        _text(order_id, "order_id")
    if intent.order_id and order_id and intent.order_id != order_id:
        raise ValueError("conflicting_broker_order_id")
    oid = order_id or intent.order_id
    if oid and conn.execute("""SELECT intent_key FROM kite_order_intents
            WHERE account_id=? AND order_id=? AND intent_key<>?""",
            (intent.account_id, oid, intent.intent_key)).fetchone():
        raise ValueError("broker_order_id_already_attributed")
    return oid


def transition(intent_key: str, state: str, *, order_id: str="", error: str="") -> Intent:
    state=state.upper()
    if state == "SUBMITTING":
        raise ValueError("use_claim_submission")
    if state in {"PARTIAL", "FILLED"}:
        raise ValueError("use_observe_order_for_fills")
    if state not in TERMINAL | UNRESOLVED:
        raise ValueError("invalid_intent_state")
    allowed={
      "RESERVED":{"CANCELLED"}, "SUBMITTING":{"SUBMITTED","UNKNOWN","REJECTED"},
      "UNKNOWN":{"SUBMITTED","REJECTED","CANCELLED"},
      "SUBMITTED":{"REJECTED","CANCELLED","UNKNOWN"},
      "PARTIAL":{"CANCELLED","UNKNOWN"}}
    with _transaction() as conn:
        intent = _get(conn, intent_key)
        _require_no_simulation_reset(conn, intent.uid)
        _require_mutable_intent(intent)
        current=intent.state
        if state != current and state not in allowed.get(current,set()):
            raise ValueError(f"invalid_intent_transition:{current}->{state}")
        oid = _check_order_id(conn, intent, order_id)
        if state == "SUBMITTED" and not oid:
            raise ValueError("submitted_order_id_required")
        conn.execute("UPDATE kite_order_intents SET state=?,order_id=?,error=?,updated_ms=? WHERE intent_key=?",
                     (state,oid,error,int(time.time()*1000),intent_key))
        return _get(conn, intent_key)


def unresolved(uid: str, account_id: Optional[str] = None) -> List[Intent]:
    _ready()
    marks = ",".join("?" for _ in UNRESOLVED)
    query = f"SELECT * FROM kite_order_intents WHERE uid=? AND (state IN ({marks}) OR reconciliation_required=1)"
    args = [uid, *sorted(UNRESOLVED)]
    if account_id is not None:
        query += " AND account_id=?"
        args.append(_text(account_id, "account_id"))
    with db._conn() as conn:
        rows = conn.execute(query + " ORDER BY created_ms", args).fetchall()
    return [_row(r) for r in rows]



def account_bucket_has_unresolved(*, account_id: str, exchange: str,
                                  symbol: str) -> bool:
    """Does *any* user have in-flight/unprojected risk for this account bucket?

    Read-only, with no user identities returned. Multi-user accounts must not
    interpret a user-local empty journal as permission to reduce exposure.
    A missing/unreadable durable journal raises so callers fail closed.
    This is diagnostic only; not an atomic broker reservation or order gate.
    """
    _ready()
    for label, value in (("account_id", account_id), ("exchange", exchange),
                         ("symbol", symbol)):
        _text(value, label)
    markers = ",".join("?" for _ in UNRESOLVED)
    sql = ("SELECT 1 FROM kite_order_intents "
           "WHERE account_id=? AND exchange=? AND symbol=? "
           f"AND (state IN ({markers}) OR reconciliation_required=1 "
           "OR projection_pending=1) LIMIT 1")
    with db._conn() as conn:
        found = conn.execute(sql,
                             (account_id, exchange, symbol,
                              *sorted(UNRESOLVED))).fetchone()
    return found is not None


def known_uids() -> List[str]:
    """Every user with durable canonical intent history.

    Exposure certification cannot discover users only from the position
    projection: an unresolved submission may exist before any position row was
    projected.  Read the journal itself and fail closed if it is unavailable.
    """
    _ready()
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT uid FROM kite_order_intents "
            "WHERE uid IS NOT NULL AND TRIM(uid) <> '' ORDER BY uid"
        ).fetchall()
    return [str(row["uid"]) for row in rows]


def acknowledge(intent_key: str, order_id: str) -> Intent:
    """Bind the HTTP ACK without regressing a postback that arrived first."""
    _text(order_id, "order_id")
    with _transaction() as conn:
        current = _get(conn, intent_key)
        _require_no_simulation_reset(conn, current.uid)
        _require_mutable_intent(current)
        _check_order_id(conn, current, order_id)
        if current.state == "RESERVED":
            raise ValueError("unclaimed_acknowledgement")
        state = "SUBMITTED" if current.state in {"SUBMITTING", "UNKNOWN"} else current.state
        conn.execute("UPDATE kite_order_intents SET state=?,order_id=?,updated_ms=? WHERE intent_key=?",
                     (state, order_id, int(time.time() * 1000), intent_key))
        return _get(conn, intent_key)


def submission_uncertain(intent_key: str, error: str) -> Intent:
    """Timeout cannot erase stronger broker evidence delivered concurrently."""
    with _transaction() as conn:
        current = _get(conn, intent_key)
        _require_no_simulation_reset(conn, current.uid)
        _require_mutable_intent(current)
        if current.state == "SUBMITTING":
            conn.execute("UPDATE kite_order_intents SET state='UNKNOWN',error=?,updated_ms=? WHERE intent_key=?",
                         (error, int(time.time() * 1000), intent_key))
        return _get(conn, intent_key)


def find(*, uid: str, account_id: str, order_id: str = "", tag: str = "",
         exchange: str = "", symbol: str = "", side: str = "") -> Optional[Intent]:
    """Resolve within account+user; tag fallback recovers a lost acknowledgement.

    Supplied identity fields must all agree. Ambiguity is an explicit error.
    """
    _ready()
    _text(uid, "uid")
    _text(account_id, "account_id")
    if not order_id and not tag:
        return None
    with db._conn() as conn:
        rows = conn.execute("""SELECT * FROM kite_order_intents WHERE uid=? AND account_id=?
            AND ((?<>'' AND order_id=?) OR (?<>'' AND tag=?))""",
            (uid, account_id, order_id, order_id, tag, tag)).fetchall()
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError("ambiguous_order_identity")
    intent = _row(rows[0])
    for supplied, actual in ((order_id, intent.order_id), (tag, intent.tag),
                             (exchange, intent.exchange), (symbol, intent.symbol), (side, intent.side)):
        if supplied and actual and supplied != actual:
            raise ValueError("order_identity_conflict")
    return intent


def pending_projection(uid: str, account_id: Optional[str] = None) -> List[Intent]:
    """Include terminal orders whose latest evidence has not reached the registry."""
    _ready()
    query = "SELECT * FROM kite_order_intents WHERE uid=? AND projection_pending=1"
    args = [uid]
    if account_id is not None:
        query += " AND account_id=?"
        args.append(_text(account_id, "account_id"))
    with db._conn() as conn:
        return [_row(r) for r in conn.execute(query + " ORDER BY created_ms", args).fetchall()]


def mark_projected(intent_key: str, projection_version: int) -> bool:
    """Acknowledge exactly the saved registry snapshot, never a newer fill."""
    _quantity(projection_version, zero=True)
    with _transaction() as conn:
        intent = _get(conn, intent_key)
        _require_no_simulation_reset(conn, intent.uid)
        _require_mutable_intent(intent)
        changed = conn.execute("""UPDATE kite_order_intents SET projection_pending=0
            WHERE intent_key=? AND projection_version=? AND projection_pending=1""",
            (intent_key, projection_version)).rowcount
        return changed == 1


def observe_order(intent_key: str, *, status: str, order_id: str,
                  filled_quantity: int, average_price: float,
                  raw: Optional[Dict[str, Any]] = None) -> Observation:
    """Apply cumulative evidence once, then tell the operator what it meant."""
    observation = _observe_order(
        intent_key, status=status, order_id=order_id,
        filled_quantity=filled_quantity, average_price=average_price, raw=raw)
    try:  # alerting is never allowed to alter or fail journal evidence
        from app.services.notifications import system_alerts
        system_alerts.order_observed(observation)
    except Exception:  # noqa: BLE001
        pass
    return observation


def _observe_order(intent_key: str, *, status: str, order_id: str,
                   filled_quantity: int, average_price: float,
                   raw: Optional[Dict[str, Any]] = None) -> Observation:
    """Apply cumulative evidence once; never manufacture per-trade fills.

    Lower cumulative quantity is older evidence. Price corrections, impossible
    quantities/values or conflicting terminal evidence quarantine the intent.
    """
    _text(order_id, "order_id")
    filled_quantity = _quantity(filled_quantity, zero=True)
    average_price = _amount(average_price, "average_price", zero=(filled_quantity == 0))
    status = status.upper()
    allowed_statuses = {"OPEN", "COMPLETE", "CANCELLED", "REJECTED", "UPDATE", "TRIGGER PENDING",
        "PUT ORDER REQ RECEIVED", "VALIDATION PENDING", "OPEN PENDING", "MODIFY VALIDATION PENDING",
        "MODIFY PENDING", "CANCEL PENDING", "AMO REQ RECEIVED"}
    if status not in allowed_statuses:
        raise ValueError("unknown_broker_order_status")
    value = filled_quantity * average_price
    if not isfinite(value):
        raise ValueError("invalid_filled_value")
    fingerprint = sha256(_json([order_id, status, filled_quantity, average_price]).encode()).hexdigest()
    encoded_raw = _json(raw or {})
    with _transaction() as conn:
        current = _get(conn, intent_key)
        _require_no_simulation_reset(conn, current.uid)
        _require_mutable_intent(current)
        _check_order_id(conn, current, order_id)
        if conn.execute("SELECT 1 FROM kite_order_observations WHERE intent_key=? AND fingerprint=?",
                        (intent_key, fingerprint)).fetchone():
            return Observation(current, False, reason="duplicate")
        reason = "accepted"
        target = current.state
        delta_qty = filled_quantity - current.filled_quantity
        delta_value = value - current.filled_value
        if current.reconciliation_required:
            reason = "reconciliation_pending"
        elif current.state == "RESERVED":
            reason = "unclaimed_order_evidence"
        elif filled_quantity > current.quantity:
            reason = "overfilled_order"
        elif status == "COMPLETE" and filled_quantity != current.quantity:
            reason = "incomplete_complete_status"
        elif delta_qty < 0:
            reason = "older_cumulative_observation"
        elif delta_qty == 0 and not isclose(value, current.filled_value, rel_tol=1e-10, abs_tol=1e-8):
            reason = "cumulative_price_correction"
        elif delta_qty > 0 and delta_value <= 0:
            reason = "nonpositive_incremental_fill_value"
        elif current.state in TERMINAL:
            expected = {"COMPLETE": "FILLED", "CANCELLED": "CANCELLED", "REJECTED": "REJECTED"}.get(status)
            if delta_qty > 0 or (expected and expected != current.state):
                reason = "terminal_order_conflict"
            else:
                reason = "terminal_duplicate_or_older_status"
        else:
            target = ("FILLED" if status == "COMPLETE" else status if status in {"CANCELLED", "REJECTED"}
                      else "PARTIAL" if filled_quantity else "SUBMITTED")
        benign = {"accepted", "older_cumulative_observation", "terminal_duplicate_or_older_status"}
        conflict = reason not in benign
        now = int(time.time() * 1000)
        conn.execute("""INSERT INTO kite_order_observations
            (intent_key,fingerprint,order_id,broker_status,filled_quantity,filled_value,
             disposition,received_ts_ms,raw_json) VALUES (?,?,?,?,?,?,?,?,?)""",
            (intent_key, fingerprint, order_id, status, filled_quantity, value, reason, now, encoded_raw))
        if reason == "accepted":
            conn.execute("""UPDATE kite_order_intents SET state=?,order_id=?,observed_quantity=?,
                observed_value=?,updated_ms=?,projection_pending=1,
                projection_version=projection_version+1 WHERE intent_key=?""",
                (target, order_id, filled_quantity, value, now, intent_key))
        elif conflict:
            conn.execute("""UPDATE kite_order_intents SET reconciliation_required=1,error=?,
                order_id=?,updated_ms=?,projection_pending=1,
                projection_version=projection_version+1 WHERE intent_key=?""", (reason, order_id, now, intent_key))
        return Observation(_get(conn, intent_key), reason == "accepted",
            delta_qty if reason == "accepted" else 0, delta_value if reason == "accepted" else 0, reason)


def _assert_tenant_cleanup_safe(conn, uid: str) -> None:
    """Check owned review rows under a caller-held SQLite transaction."""
    rows = conn.execute(
        "SELECT * FROM kite_order_intents WHERE uid=?", (uid,),
    ).fetchall()
    for row in rows:
        _require_mutable_intent(_row(row))
    # The offline Gamma proof is durably held BEFORE the test-only child
    # exists. A crash between these commits leaves REVIEW_HELD without a
    # marked child; erasing the parent would invalidate its recovery proof.
    # The table is optional in ordinary installations, but if it exists and
    # is unreadable we propagate the error rather than assume no review hold.
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='gamma_terminal_review_holds'"
    ).fetchone():
        if conn.execute(
            "SELECT 1 FROM gamma_terminal_review_holds WHERE uid=? LIMIT 1",
            (uid,),
        ).fetchone():
            raise ValueError("gamma_offline_review_hold_immutable")


def assert_cleanup_safe(uid: str) -> None:
    """Preflight simulation reset before other subsystems can be modified.

    Advisory only: another process can change the database after return.
    clear_for_tests still repeats the check with its own durable write lock.
    """
    _text(uid, "uid")
    with _transaction() as conn:
        _assert_tenant_cleanup_safe(conn, uid)
        _require_no_simulation_reset(conn, uid)


def clear_for_tests(uid: str, *, reset_token: Optional[str] = None) -> None:
    """Clear ordinary tenant simulation data, not immutable review evidence.

    Simulation reset still uses this legacy name outside pytest. If the tenant
    has an offline-only Gamma child, deleting its canonical identity and
    observations would leave a dangling review hold and permit misleading
    replay. Check all owned intents under the same SQLite writer lock before
    *any* deletion. Other tenants can still reset their simulation normally.
    """
    _text(uid, "uid")
    with _transaction() as conn:
        _assert_tenant_cleanup_safe(conn, uid)
        fence = _reset_fence(conn, uid)
        if fence is not None:
            if (fence["state"] != "ACTIVE" or not reset_token
                    or fence["token"] != reset_token):
                raise ValueError("simulation_reset_fenced")
        elif reset_token is not None:
            raise ValueError("simulation_reset_fence_mismatch")
        conn.execute("DELETE FROM kite_order_observations WHERE intent_key IN (SELECT intent_key FROM kite_order_intents WHERE uid=?)", (uid,))
        conn.execute("DELETE FROM kite_fill_ledger WHERE uid=?",(uid,))
        conn.execute("DELETE FROM kite_order_intents WHERE uid=?",(uid,))


def record_fill(*, account_id: str, order_id: str, trade_id: str, uid: str,
                symbol: str, side: str, quantity: int, price: float, fees: float=0,
                exchange_ts_ms: int=0, raw: Optional[Dict[str, Any]]=None) -> bool:
    """Insert one immutable fill. False means exact broker execution already consumed."""
    _ready(); now=int(time.time()*1000)
    with _transaction() as conn:
        _require_no_simulation_reset(conn, uid)
        before=conn.total_changes
        conn.execute("""INSERT OR IGNORE INTO kite_fill_ledger
          (account_id,order_id,trade_id,uid,symbol,side,quantity,price,fees,
           exchange_ts_ms,received_ts_ms,raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
          (account_id,order_id,trade_id,uid,symbol,side,int(quantity),float(price),float(fees),
           int(exchange_ts_ms),now,json.dumps(raw or {},sort_keys=True)))
        return conn.total_changes > before
