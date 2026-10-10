"""One-shot, best-effort import of trades that closed before the journal existed.

Only what the durable stores state for certain is imported; everything else stays null and the row
says so (``source='backfill'``, no timeline events between the fill and the exit):

* **Snapback**  every CLOSED row of the live-position store: entry, hedge, exit and P&L are all on it.
* **Supertrend** CLOSED rows of the position registry that were entered and exited exactly once. The
  registry keeps only the latest row per contract and no exit price, so the price is taken from the
  ``fill @ ₹...`` the exit reason carries when it carries one (else the trade has no P&L).
* **Gamma Unwind** is not imported: its position row records neither an exit time nor an exit price,
  and a guessed one would put a wrong trade in the journal.

Safe to run any number of times: a trade that already has a journal row (the hooks journalled it, or
an earlier run imported it) is left exactly as it is. It reads the stores' stored bytes (it never
loads them through the engines' own registries, whose failure modes halt trading), never raises, and
never touches a simulation tenant (those were journalled from their first trade).

    python -m app.services.trade_journal_backfill            # import now
"""
from __future__ import annotations

import json
import re
from dataclasses import fields
from typing import Any, Callable, Dict, List, Optional, Sequence

from app.core.logging import get_logger
from app.services import db, trade_journal as tj
from app.services import trade_journal_adapters as ad
from app.services.simulation import identity

log = get_logger(__name__)

MARKER_KEY = "trade_journal_backfill_v1"
SOURCE = "backfill"
_FILL_RE = re.compile(r"fill @ ₹\s*([0-9][0-9,]*\.?[0-9]*)")

IMPORTED, EXISTING, SKIPPED = "imported", "existing", "skipped"


def _tally() -> Dict[str, int]:
    return {IMPORTED: 0, EXISTING: 0, SKIPPED: 0}


def _uids_with(prefix: str) -> List[str]:
    with db._conn() as conn:
        rows = conn.execute("SELECT key FROM system_config WHERE key LIKE ?", (prefix + "%",)).fetchall()
    return sorted({str(r[0])[len(prefix):] for r in rows})


def _rows(key: str) -> List[dict]:
    raw = db.get_config(key)
    decoded = json.loads(raw) if raw else []
    return [d for d in decoded if isinstance(d, dict)] if isinstance(decoded, list) else []


# --------------------------------------------------------------------------
# Supertrend
# --------------------------------------------------------------------------

def _supertrend_row(uid: str, row: dict) -> str:
    from app.services.kite_engine.positions import CLOSED, OpenPosition

    known = {f.name for f in fields(OpenPosition)}
    p = OpenPosition(**{k: v for k, v in row.items() if k in known})
    entry, qty = ad._pos(p.fill_price), ad._int(p.qty) or ad._int(p.entry_requested_qty)
    if (p.status != CLOSED or p.uid != uid or not entry or not qty or not ad._int(p.closed_ms)
            or len(p.exit_fills or {}) > 1):
        return SKIPPED                          # not closed, or partly exited (the quantity is then ambiguous)
    key = ad._st_key(p)
    if tj.trade_status(tj.trade_id_for(ad.SUPERTREND, uid, p.symbol, key)) is not None:
        return EXISTING
    opened = ad._int(p.opened_ms)
    tid = tj.record_entry(uid=uid, strategy=ad.SUPERTREND, symbol=p.symbol, key=key, status="OPEN", ts_ms=opened,
                          source=SOURCE, extra=ad._st_extra(p), entry_ts_ms=opened, **ad._st_fields(p, filled=True))
    if not tid:
        return SKIPPED
    tj.record_event(trade_id=tid, event_type=tj.FILLED, ts_ms=opened, price=entry, qty=qty,
                    event_key=f"fill:{p.order_id}:{qty}:{entry:.2f}", detail=f"Bought {qty} {p.symbol} at {entry:.2f}")
    seen = _FILL_RE.search(str(p.exit_reason or ""))
    price = ad._pos(seen.group(1).replace(",", "")) if seen else None
    tj.record_exit(uid=uid, strategy=ad.SUPERTREND, symbol=p.symbol, key=key, exit_price=price, qty=qty,
                   ts_ms=ad._int(p.closed_ms), reason=str(p.exit_reason or ""), trailed=ad._st_trailed(p))
    return IMPORTED


# --------------------------------------------------------------------------
# Snapback
# --------------------------------------------------------------------------

def _snapback_row(uid: str, pos: Any) -> str:
    qty, entry = ad._int(pos.option_qty), ad._pos(pos.option_entry_price)
    if not qty or not entry or not ad._int(pos.closed_ms):
        return SKIPPED
    key, symbol = str(pos.position_id), str(pos.option_symbol)
    if tj.trade_status(tj.trade_id_for(ad.SNAPBACK, uid, symbol, key)) is not None:
        return EXISTING
    hedge = None
    if pos.hedge_symbol and ad._int(pos.hedge_qty):
        hedge = {"symbol": pos.hedge_symbol, "side": str(pos.hedge_side or "").upper() or None,
                 "qty": ad._int(pos.hedge_qty), "entry_price": ad._pos(pos.hedge_entry_price),
                 "exit_price": ad._pos(pos.hedge_exit_price)}
    opened = ad._int(pos.opened_ms) or None
    rules = dict(pos.rules or {})
    extra = {"exchange": pos.option_exchange, "lane": pos.lane, "plan_id": key, "hedge": hedge,
             "high_water": ad._pos(pos.high_water), "stop_mode": "broker", "product": pos.product}
    tid = tj.record_entry(
        uid=uid, strategy=ad.SNAPBACK, symbol=symbol, key=key, status="OPEN", ts_ms=opened, source=SOURCE,
        account_id=str(pos.account_id or ""), underlying=str(pos.underlying or ""),
        option_type=str(pos.option_type or ""), expiry=str(pos.option_expiry or ""), side="BUY",
        lot_size=ad._int(pos.lot_size), lots=ad._lots(qty, pos.lot_size), qty=qty,
        signal_id=str(pos.signal_id or ""), entry_order_id=str(pos.option_entry_order_id or ""),
        entry_price=entry, entry_ts_ms=opened, initial_sl=ad._pos(pos.stop_premium),
        target=ad._pos(rules.get("target_premium")), signal_context=ad._sb_position_context(pos) or None,
        extra={k: v for k, v in extra.items() if v is not None})
    if not tid:
        return SKIPPED
    tj.record_event(trade_id=tid, event_type=tj.FILLED, ts_ms=opened, price=entry, qty=qty,
                    event_key=f"fill:{pos.option_entry_order_id}:{qty}:{entry:.2f}",
                    detail=f"Bought {qty} {symbol} at {entry:.2f}")
    if hedge and hedge["entry_price"]:
        tj.record_event(trade_id=tid, event_type=tj.HEDGE_FILLED, ts_ms=opened, price=hedge["entry_price"],
                        qty=hedge["qty"], event_key=f"hedge:{pos.hedge_entry_order_id}:{hedge['qty']}",
                        detail=f"Hedge: {hedge['side'] or ''} {hedge['qty']} {pos.hedge_symbol} at "
                               f"{hedge['entry_price']:.2f}".replace("  ", " "))
    priced = max(0, int(pos.option_exit_qty or 0) - int(pos.option_exit_unknown_qty or 0))
    price = ad._pos(pos.option_exit_price)
    tj.record_exit(
        uid=uid, strategy=ad.SNAPBACK, symbol=symbol, key=key, exit_price=price, qty=priced or None,
        ts_ms=ad._int(pos.closed_ms), reason=str(pos.exit_reason or ""), trailed=ad._sb_trailed(pos),
        gross_pnl=ad._num(pos.realised_pnl) if price else None, fill_key=f"exit:{key}",
        extra={"hedge": hedge, "high_water": ad._pos(pos.high_water),
               "exit_unknown_qty": int(pos.option_exit_unknown_qty or 0), "flags": list(pos.flags or [])})
    return IMPORTED


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

def backfill(uids: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Import the closed trades the journal has not seen. Returns ``{strategy: {imported, existing,
    skipped}, "errors": [...]}``. Never raises; one bad row or store never stops the rest."""
    report: Dict[str, Any] = {ad.SUPERTREND: _tally(), ad.SNAPBACK: _tally(), "errors": []}

    def run(strategy: str, uid: str, items: Callable[[], Sequence[Any]], one: Callable[[str, Any], str]) -> None:
        try:
            batch = list(items())
        except Exception as exc:  # noqa: BLE001
            report["errors"].append(f"{strategy}:{uid}:{type(exc).__name__}:{exc}")
            log.warning("trade journal backfill: %s store for %s unreadable: %s", strategy, uid, exc)
            return
        for item in batch:
            try:
                report[strategy][one(uid, item)] += 1
            except Exception as exc:  # noqa: BLE001
                report[strategy][SKIPPED] += 1
                report["errors"].append(f"{strategy}:{uid}:{type(exc).__name__}:{exc}")
                log.warning("trade journal backfill: a %s row of %s skipped: %s", strategy, uid, exc)

    try:
        if not db.is_available():
            report["errors"].append("database_unavailable")
            return report
        from app.services import snapback_live_positions as sbp

        wanted = None if uids is None else {str(u) for u in uids}

        def real(uid: str) -> bool:
            return bool(uid) and not identity.is_sim_uid(uid) and (wanted is None or uid in wanted)

        for uid in filter(real, _uids_with("kite_engine_positions_")):
            run(ad.SUPERTREND, uid, lambda uid=uid: _rows(f"kite_engine_positions_{uid}"), _supertrend_row)
        store = sbp.default_store()
        for uid in filter(real, store.known_uids()):
            run(ad.SNAPBACK, uid, lambda uid=uid: store.list(uid, statuses=("CLOSED",)), _snapback_row)
    except Exception as exc:  # noqa: BLE001
        report["errors"].append(f"{type(exc).__name__}:{exc}")
        log.warning("trade journal backfill failed: %s", exc)
    return report


def run_once() -> Dict[str, Any]:
    """The startup call: :func:`backfill` the first time only (a marker in the application database
    records it), so a trade the journal deliberately lacks is never resurrected on a later boot."""
    try:
        if not db.is_available() or db.get_config(MARKER_KEY):
            return {"status": "skipped"}
        report = backfill()
        if not report["errors"]:
            db.set_config(MARKER_KEY, json.dumps({"ts": tj._now_ms(), "report": {
                k: v for k, v in report.items() if k != "errors"}}))
        imported = sum(v[IMPORTED] for k, v in report.items() if k != "errors")
        if imported or report["errors"]:
            log.info("trade journal backfill: imported %d trade(s), %d error(s)", imported, len(report["errors"]))
        return {"status": "done" if not report["errors"] else "partial", **report}
    except Exception as exc:  # noqa: BLE001
        log.warning("trade journal backfill skipped: %s", exc)
        return {"status": "error", "error": str(exc)}


if __name__ == "__main__":  # pragma: no cover - operator convenience
    db.init()
    print(json.dumps(backfill(), indent=2))
