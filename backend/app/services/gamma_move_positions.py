"""Durable position registry for Gamma-Unwind V1."""
from __future__ import annotations
import json
from dataclasses import asdict, fields
from typing import Optional

from app.core.logging import get_logger
from app.services import trade_journal_adapters as _journal
from app.engines.gamma_move import (
    InstrumentRef, PositionState, T1_PCT, T2_PCT, TradeRecord, target_price,
)

log = get_logger(__name__)
PENDING, OPEN, CLOSED, REJECTED = "pending", "open", "closed", "rejected"
_cache: dict[str, dict[str, PositionState]] = {}
_record_cache: dict[str, TradeRecord] = {}


def _key(uid: str) -> str:
    return f"gamma_move_positions_{uid}"


def _record_key(uid: str) -> str:
    return f"gamma_move_record_{uid}"


def _legacy_key(uid: str) -> str:
    return f"gamma_move_legacy_positions_{uid}"


_NEW_REQUIRED = frozenset({
    "list_name", "tier", "underlying", "underlying_token", "hold",
    "zone_edge", "base_low", "t1", "t2", "trade_state",
})
_LEGACY_REQUIRED = frozenset({
    "signal_id", "instrument", "entry", "stop", "quantity", "lots",
    "entered_ms", "entry_day", "status",
})


def _is_new_row(row: dict) -> bool:
    return _NEW_REQUIRED.issubset(row)


def _is_legacy_row(row: dict) -> bool:
    instrument = row.get("instrument")
    return (
        _LEGACY_REQUIRED.issubset(row)
        and isinstance(instrument, dict)
        and bool(str(instrument.get("tradingsymbol") or "").strip())
    )


def _archive_legacy_rows(uid: str, rows: list[dict]) -> None:
    """Preserve prior Gamma-Move rows without reinterpreting their economics."""
    if not rows:
        return
    from app.services import db
    # Do not erase an existing archive on read/parse failure. A silent default
    # to [] would discard previous broker-exposure history during migration.
    existing_raw = db.get_config_strict(_legacy_key(uid))
    try:
        existing = json.loads(existing_raw) if existing_raw else []
    except (TypeError, ValueError) as exc:
        raise RuntimeError("legacy_gamma_archive_unreadable") from exc
    if not isinstance(existing, list) or any(not isinstance(row, dict) for row in existing):
        raise RuntimeError("legacy_gamma_archive_invalid")
    # Old migration code deduped on (symbol, order_id, entered_ms). Two
    # distinct legacy position rows can share all three values while differing
    # in status, filled quantity, or protective-order evidence. Collapsing them
    # could erase broker exposure before an operator reconciles it. Deduplicate
    # only byte-equivalent logical rows, with deterministic key order.
    def fingerprint(row: dict) -> str:
        return json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False)

    seen = {fingerprint(row) for row in existing}
    for row in rows:
        ident = fingerprint(row)
        if ident not in seen:
            existing.append(row)
            seen.add(ident)
    # A non-strict write silently logs failures. If this persistence step
    # fails, load() must never remove legacy records from the active registry.
    db.set_config_strict(
        _legacy_key(uid),
        json.dumps(existing, separators=(",", ":"), allow_nan=False),
    )


def legacy_open_rows(uid: str) -> list[dict]:
    """Legacy Gamma-Move exposure, refusing unreadable migration evidence.

    Returning [] on an archive I/O or parsing error used to authorize new
    Gamma risk as though there were no legacy broker positions.
    """
    from app.services import db

    raw = db.get_config_strict(_legacy_key(uid))
    try:
        rows = json.loads(raw) if raw else []
    except (TypeError, ValueError) as exc:
        raise RuntimeError("legacy_gamma_archive_unreadable") from exc
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise RuntimeError("legacy_gamma_archive_invalid")
    # An unknown status is NOT proof that a broker position was closed.
    # Old code counted only pending/open, treating EXITING, RECONCILING,
    # empty or misspelled values as flat and admitting fresh real-money risk.
    # Only explicitly terminal legacy outcomes may be excluded; every other
    # archived row requires a broker reconciliation before origination.
    terminal = {CLOSED, REJECTED}
    return [
        row for row in rows
        if str(row.get("status") or "").strip().lower() not in terminal
    ]


def _to_dict(pos: PositionState) -> dict:
    out = asdict(pos)
    out["instrument"] = asdict(pos.instrument)
    return out


def _from_dict(data: dict) -> Optional[PositionState]:
    try:
        instrument_fields = {f.name for f in fields(InstrumentRef)}
        instrument = InstrumentRef(**{
            k: v for k, v in (data.get("instrument") or {}).items()
            if k in instrument_fields
        })
        known = {f.name for f in fields(PositionState)} - {"instrument"}
        return PositionState(
            instrument=instrument,
            # Old intraday records were submitted as MIS. Preserve that bucket
            # during recovery; new reference positions use NRML for GTT support.
            product=data.get("product", "MIS" if data.get("hold") == "intraday" else "NRML"),
            **{k: v for k, v in data.items() if k in known and k != "product"},
        )
    except (TypeError, ValueError) as exc:
        log.error("gamma_unwind: unreadable persisted position: %s", exc)
        return None


def load(uid: str) -> dict[str, PositionState]:
    if uid in _cache:
        return _cache[uid]
    from app.services import db
    raw = db.get_config_strict(_key(uid))
    decoded = json.loads(raw) if raw else []
    if not isinstance(decoded, list):
        raise ValueError("gamma_unwind_position_registry_not_a_list")
    out: dict[str, PositionState] = {}
    legacy: list[dict] = []
    current_rows: list[dict] = []
    for index, row in enumerate(decoded):
        if not isinstance(row, dict):
            raise ValueError(f"invalid_gamma_unwind_position_row:{index}")
        if not _is_new_row(row):
            if _is_legacy_row(row):
                legacy.append(dict(row))
                continue
            raise ValueError(f"invalid_gamma_unwind_position_row:{index}")
        pos = _from_dict(row)
        if pos is None or not pos.instrument.tradingsymbol:
            raise ValueError(f"invalid_gamma_unwind_position_row:{index}")
        # PositionState.is_open deliberately selects known OPEN/PENDING rows.
        # Without schema validation, a corrupted status or trade_state silently
        # vanished from open_positions(), falsely declaring broker exposure
        # flat on the next account admission check.
        if pos.status not in {PENDING, OPEN, CLOSED, REJECTED}:
            raise ValueError(f"gamma_unwind_position_unknown_status:{index}")
        if pos.status in {PENDING, OPEN} and (
            pos.trade_state not in {"OPEN", "T1_TRAIL"}
            or int(pos.quantity) <= 0
        ):
            raise ValueError(f"gamma_unwind_active_position_invalid:{index}")
        symbol = pos.instrument.tradingsymbol
        if symbol in out:
            raise ValueError(f"duplicate_gamma_unwind_position:{symbol}")
        out[symbol] = pos
        current_rows.append(row)

    if legacy:
        # The retired Gamma-Move schema lacks the reference strategy's
        # list/tier/underlying/zone/base/T1/T2 context. Preserve it verbatim,
        # remove it from the active registry, and never manufacture that missing
        # strategy state merely to make a dataclass constructor succeed.
        _archive_legacy_rows(uid, legacy)
        from app.services import db
        db.set_config_strict(
            _key(uid),
            json.dumps(current_rows, separators=(",", ":"), allow_nan=False),
        )
        log.warning(
            "gamma_unwind: archived %d legacy Gamma-Move position row(s) for %s",
            len(legacy), uid,
        )

    _cache[uid] = out
    return out


def persist(uid: str) -> None:
    """Write capital-critical positions or evict unsafe speculative cache.

    Callers mutate cached objects before persisting. If SQLite rejects a write,
    the in-process registry must not continue serving those uncommitted changes
    as though they were durable broker state.
    """
    from app.services import db
    try:
        payload = json.dumps(
            [_to_dict(p) for p in _cache.get(uid, {}).values()],
            separators=(",", ":"),
            allow_nan=False,
        )
        db.set_config_strict(_key(uid), payload)
    except Exception:
        _cache.pop(uid, None)
        log.error(
            "gamma_unwind: position save failed for %s; evicted uncommitted cache",
            uid,
        )
        raise


def put(uid: str, pos: PositionState) -> PositionState:
    load(uid)[pos.instrument.tradingsymbol] = pos
    persist(uid)
    return pos


def get(uid: str, symbol: str) -> Optional[PositionState]:
    return load(uid).get(symbol)


def open_positions(uid: str) -> list[PositionState]:
    return [p for p in load(uid).values() if p.is_open]


def mark_filled(uid: str, symbol: str, fill_price: float,
                *, gtt_id: int = 0) -> Optional[PositionState]:
    pos = get(uid, symbol)
    if pos is None:
        return None
    was_status = pos.status
    was_fill = float(pos.fill_price or 0.0)
    pos.status = OPEN
    if fill_price > 0:
        pos.fill_price = float(fill_price)
        # Targets follow actual broker fill. The reference base-low stop is a
        # structural price and is NOT shifted by slippage.
        pos.t1 = target_price(pos.fill_price, T1_PCT, pos.instrument.tick_size)
        pos.t2 = target_price(pos.fill_price, T2_PCT, pos.instrument.tick_size)
        pos.high_water = max(pos.high_water, pos.fill_price)
    if gtt_id:
        pos.gtt_id = int(gtt_id)
    persist(uid)
    if was_status != OPEN or abs(was_fill - float(pos.fill_price or 0.0)) >= 0.01:
        _journal.gm_filled(uid, pos)
        from app.services.notifications.telegram_lifecycle import enqueue_best_effort
        fill = float(pos.effective_entry or 0.0)
        stop_text = f" · SL ₹{float(pos.stop):.2f}" if float(pos.stop or 0.0) > 0 else ""
        enqueue_best_effort(
            uid,
            dedupe_key=(
                f"gamma:entry:{pos.signal_id}:{pos.order_id or pos.entered_ms}:"
                f"{int(pos.quantity)}:{fill:.2f}"
            ),
            strategy="Gamma-Unwind",
            event_type="ENTRY FILLED",
            title=pos.instrument.tradingsymbol,
            detail=(
                f"Qty {int(pos.quantity)} · Fill ₹{fill:.2f}{stop_text} "
                f"· T1 ₹{float(pos.t1):.2f} · T2 ₹{float(pos.t2):.2f} · {pos.hold}"
            ),
            created_at_ms=int(pos.entered_ms or 0) or None,
        )
    return pos


def mark_rejected(uid: str, symbol: str,
                  reason: str = "") -> Optional[PositionState]:
    pos = get(uid, symbol)
    if pos is None:
        return None
    pos.status = REJECTED
    pos.exit_reason = reason
    persist(uid)
    _journal.gm_rejected(uid, pos, reason)
    return pos


def close(
    uid: str,
    symbol: str,
    reason: str = "",
    *,
    exit_price: float = 0.0,
    quantity_closed: int = 0,
    pnl_inr: float | None = None,
) -> Optional[PositionState]:
    pos = get(uid, symbol)
    if pos is None:
        return None
    was_closed = pos.status == CLOSED
    pos.status = CLOSED
    pos.trade_state = "CLOSED"
    pos.exit_reason = reason
    persist(uid)
    if not was_closed:
        _journal.gm_closed(uid, pos, reason, exit_price, quantity_closed, pnl_inr)
        from app.services.notifications.telegram_lifecycle import enqueue_best_effort
        fill_text = f" · Exit ₹{float(exit_price):.2f}" if float(exit_price or 0.0) > 0 else ""
        pnl_text = f" · P&L ₹{float(pnl_inr):,.2f}" if pnl_inr is not None else ""
        qty = int(quantity_closed or pos.quantity or 0)
        enqueue_best_effort(
            uid,
            dedupe_key=(
                f"gamma:exit:{pos.signal_id}:{pos.exit_order_id or reason}:"
                f"{qty}:{float(exit_price or 0.0):.2f}"
            ),
            strategy="Gamma-Unwind",
            event_type="POSITION CLOSED",
            title=pos.instrument.tradingsymbol,
            detail=f"Qty {qty}{fill_text} · {reason or 'closed'}{pnl_text}",
            severity="WARNING" if str(reason).upper() in {"SL", "TRAIL"} else "INFO",
        )
    return pos


def pnl_unreconciled(uid: str, today: str) -> list[str]:
    """Contracts whose realised P&L may be missing from today's daily-loss figure.

    An open position carrying the flag always counts. A closed one counts for the
    day it was flagged: the breaker is per day, and a P&L gap from an earlier day
    cannot change today's figure.
    """
    return sorted(
        symbol for symbol, pos in load(uid).items()
        if pos.pnl_reconciliation_required
        and (pos.is_open or str(pos.pnl_flagged_day or "") == str(today))
    )


def forget_closed(uid: str) -> None:
    _cache[uid] = {k: v for k, v in load(uid).items() if v.is_open}
    persist(uid)


def load_record(uid: str) -> TradeRecord:
    """Load the capital-critical realised-P&L ledger without hiding corruption.

    The previous get_config() swallowed disk errors and converted malformed
    records into a zero-P&L record. That could reset the daily-loss breaker
    precisely when the position database was unhealthy.
    """
    if uid in _record_cache:
        return _record_cache[uid]
    from app.services import db
    import math

    raw = db.get_config_strict(_record_key(uid))
    if not raw:
        record = TradeRecord()
    else:
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(data, dict):
                raise ValueError("ledger must be a JSON object")
            for field in ("realised_inr", "day_realised_inr"):
                number = data.get(field, 0)
                if (
                    isinstance(number, bool)
                    or not isinstance(number, (int, float))
                    or not math.isfinite(float(number))
                ):
                    raise ValueError(f"{field} is not a finite number")
            for field in (
                "trades", "wins", "losses",
                "consecutive_wins", "consecutive_losses",
            ):
                count = data.get(field, 0)
                if type(count) is not int or count < 0:
                    raise ValueError(f"{field} is not a nonnegative integer")
            if data.get("wins", 0) + data.get("losses", 0) > data.get("trades", 0):
                raise ValueError("trade outcomes exceed the trade count")
            record = TradeRecord.from_dict(data)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("gamma_unwind_trade_ledger_unreadable") from exc
    _record_cache[uid] = record
    return record


def save_record(uid: str, record: TradeRecord) -> TradeRecord:
    """Never claim a realised-P&L write succeeded when SQLite refused it."""
    from app.services import db

    try:
        db.set_config_strict(
            _record_key(uid),
            json.dumps(
                record.as_dict(), separators=(",", ":"), allow_nan=False
            ),
        )
    except Exception as exc:
        # session.record is mutable: a fill/exit may already have changed the
        # object also referenced by _record_cache. Never serve its uncommitted
        # losses as durable accounting after SQLite refused the write.
        _record_cache.pop(uid, None)
        try:
            from app.services import gamma_move_runner
            gamma_move_runner._sessions.pop(uid, None)
        except Exception:
            log.exception("gamma_unwind: could not invalidate failed session ledger")
        log.error(
            "gamma_unwind: realised-P&L persistence FAILED for %s: %s",
            uid, type(exc).__name__,
        )
        try:
            from app.services import live_safety

            live_safety.set_kill_switch(
                True,
                "Gamma-Unwind realised P&L write failed; broker reconciliation required",
                uid=uid,
                reason_code="GAMMA_PNL_PERSISTENCE_FAILED",
            )
        except Exception:
            log.exception("gamma_unwind: could not engage kill switch after ledger write failure")
        raise
    _record_cache[uid] = record
    return record


def reset(uid: str = "") -> None:
    if uid:
        _cache.pop(uid, None)
        _record_cache.pop(uid, None)
    else:
        _cache.clear()
        _record_cache.clear()
