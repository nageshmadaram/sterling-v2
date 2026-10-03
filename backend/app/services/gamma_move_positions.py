"""Durable position registry for Gamma-Unwind V1."""
from __future__ import annotations
import json
from dataclasses import asdict, fields
from typing import Optional

from app.core.logging import get_logger
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
    existing_raw = db.get_config(_legacy_key(uid))
    try:
        existing = json.loads(existing_raw) if existing_raw else []
    except Exception:
        existing = []
    if not isinstance(existing, list):
        existing = []
    seen = {
        (
            str((row.get("instrument") or {}).get("tradingsymbol") or ""),
            str(row.get("order_id") or ""),
            int(row.get("entered_ms") or 0),
        )
        for row in existing if isinstance(row, dict)
    }
    for row in rows:
        ident = (
            str((row.get("instrument") or {}).get("tradingsymbol") or ""),
            str(row.get("order_id") or ""),
            int(row.get("entered_ms") or 0),
        )
        if ident not in seen:
            existing.append(row)
            seen.add(ident)
    db.set_config(
        _legacy_key(uid),
        json.dumps(existing, separators=(",", ":"), allow_nan=False),
    )


def legacy_open_rows(uid: str) -> list[dict]:
    """Legacy Gamma-Move rows that may still represent broker exposure."""
    try:
        from app.services import db
        raw = db.get_config(_legacy_key(uid))
        rows = json.loads(raw) if raw else []
    except Exception:
        return []
    if not isinstance(rows, list):
        return []
    return [
        row for row in rows
        if isinstance(row, dict)
        and str(row.get("status") or "").lower() in {PENDING, OPEN}
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
            **{k: v for k, v in data.items() if k in known},
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
    from app.services import db
    db.set_config_strict(
        _key(uid),
        json.dumps(
            [_to_dict(p) for p in _cache.get(uid, {}).values()],
            separators=(",", ":"),
            allow_nan=False,
        ),
    )


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
    return pos


def mark_rejected(uid: str, symbol: str,
                  reason: str = "") -> Optional[PositionState]:
    pos = get(uid, symbol)
    if pos is None:
        return None
    pos.status = REJECTED
    pos.exit_reason = reason
    persist(uid)
    return pos


def close(uid: str, symbol: str,
          reason: str = "") -> Optional[PositionState]:
    pos = get(uid, symbol)
    if pos is None:
        return None
    pos.status = CLOSED
    pos.trade_state = "CLOSED"
    pos.exit_reason = reason
    persist(uid)
    return pos


def forget_closed(uid: str) -> None:
    _cache[uid] = {k: v for k, v in load(uid).items() if v.is_open}
    persist(uid)


def load_record(uid: str) -> TradeRecord:
    if uid in _record_cache:
        return _record_cache[uid]
    record = TradeRecord()
    try:
        from app.services import db
        raw = db.get_config(_record_key(uid))
        if raw:
            data = json.loads(raw) if isinstance(raw, str) else raw
            record = TradeRecord.from_dict(data if isinstance(data, dict) else {})
    except Exception as exc:
        log.error("gamma_unwind: trade record unreadable for %s: %s", uid, exc)
    _record_cache[uid] = record
    return record


def save_record(uid: str, record: TradeRecord) -> TradeRecord:
    _record_cache[uid] = record
    try:
        from app.services import db
        db.set_config(
            _record_key(uid),
            json.dumps(record.as_dict(), separators=(",", ":"),
                       allow_nan=False),
        )
    except Exception as exc:
        log.error("gamma_unwind: failed to persist record for %s: %s", uid, exc)
    return record


def reset(uid: str = "") -> None:
    if uid:
        _cache.pop(uid, None)
        _record_cache.pop(uid, None)
    else:
        _cache.clear()
        _record_cache.clear()
