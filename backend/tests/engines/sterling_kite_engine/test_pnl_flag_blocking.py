"""An unresolved-P&L flag blocks auto-entry only while it can still be wrong today.

A flag on a CLOSED row (a broker-flat reconcile after a manual Kite exit, a missed
GTT postback) used to block every new entry for ever: nothing cleared it and closed
rows are never pruned. It now stops blocking once the IST day has rolled or the
ledger shows the holding flat, and an administrator can acknowledge it with a note.
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi import HTTPException

from app.core.auth import UserContext
from app.services.kite_engine import fill_ledger, positions as pos, service

SYMBOL = "NIFTY24JUN24000CE"
UID = "pnl-flag"


def _closed_flagged(*, closed_ms=None, account_id="ACC1"):
    pos.reset(UID)
    pos.register(pos.OpenPosition(
        uid=UID, symbol=SYMBOL, exchange="NFO", token=777, qty=50, lot_size=50,
        entry_premium=100.0, fill_price=100.0, stop_premium=80.0, order_id="E1",
        status=pos.OPEN, account_id=account_id))
    pos.close(UID, SYMBOL, reason="reconciled closed at broker (exit price unknown)")
    p = pos.get(UID, SYMBOL)
    p.pnl_reconciliation_required = True
    if closed_ms is not None:
        p.closed_ms = closed_ms
    pos._persist(UID)
    return p


def _book(side, order_id, qty=50, price=100.0):
    return fill_ledger.record(
        account_id="ACC1", uid=UID, symbol=SYMBOL, exchange="NFO", side=side,
        order_id=order_id, cumulative_quantity=qty, cumulative_value=qty * price,
        source="entry" if side == "BUY" else "exit", lot_size=50)


def test_a_closed_flag_from_today_with_an_open_ledger_still_blocks():
    _closed_flagged()
    _book("BUY", "E1")  # the ledger still holds 50: today's P&L is genuinely unknown

    assert service.pnl_reconciliation_blockers(UID) == [SYMBOL]


def test_a_closed_flag_from_a_previous_day_no_longer_blocks():
    _closed_flagged(closed_ms=int((time.time() - 3 * 86400) * 1000))
    _book("BUY", "E1")

    assert service.pnl_reconciliation_blockers(UID) == []


def test_a_closed_flag_stops_blocking_once_the_ledger_shows_the_holding_flat():
    _closed_flagged()
    _book("BUY", "E1")
    _book("SELL", "X1", price=110.0)

    assert service.pnl_reconciliation_blockers(UID) == []


def test_an_open_position_with_the_flag_always_blocks():
    p = _closed_flagged()
    p.status = pos.OPEN
    p.closed_ms = int((time.time() - 3 * 86400) * 1000)
    pos._persist(UID)

    assert service.pnl_reconciliation_blockers(UID) == [SYMBOL]


def test_preflight_reports_only_blocking_flags():
    _closed_flagged(closed_ms=int((time.time() - 3 * 86400) * 1000))
    _book("BUY", "E1")

    assert not any("PnL reconciliation" in r for r in service.autoexec_preflight(UID))

    _closed_flagged()  # same flag, closed today, ledger still holds the lot
    assert any("Fill-level PnL reconciliation required" in r
               for r in service.autoexec_preflight(UID))


def test_acknowledging_clears_the_flag_and_writes_an_audit_record(tmp_path, monkeypatch):
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setenv("STERLING_AUDIT_LOG", str(audit))
    _closed_flagged()
    _book("BUY", "E1")
    assert service.pnl_reconciliation_blockers(UID) == [SYMBOL]

    out = service.acknowledge_pnl_reconciliation(
        UID, SYMBOL, actor="boss", note="checked the Kite tradebook, realized P&L 450")

    assert out["cleared"] is True
    assert service.pnl_reconciliation_blockers(UID) == []
    assert pos.get(UID, SYMBOL).pnl_reconciliation_required is False
    row = json.loads(audit.read_text().strip().splitlines()[-1])
    assert row["event"] == "PNL_RECONCILIATION_ACKNOWLEDGED"
    assert row["actor"] == "boss" and row["symbol"] == SYMBOL
    assert "tradebook" in row["note"]


def test_acknowledging_an_unflagged_or_unknown_position_is_reported_not_silent():
    _closed_flagged()
    assert service.acknowledge_pnl_reconciliation(
        UID, SYMBOL, actor="boss", note="first")["cleared"] is True
    assert service.acknowledge_pnl_reconciliation(
        UID, SYMBOL, actor="boss", note="second")["cleared"] is False
    with pytest.raises(KeyError):
        service.acknowledge_pnl_reconciliation(UID, "NOPE", actor="boss", note="x" * 5)


@pytest.mark.asyncio
async def test_the_acknowledge_route_needs_an_admin_and_a_note():
    from app.api.v1.endpoints import kite_engine as ep

    _closed_flagged()
    body = ep.PnlAcknowledgeRequest(note="checked the tradebook")
    viewer = UserContext(user_id=UID, username="viewer", role="user")
    admin = UserContext(user_id=UID, username="boss", role="admin")

    with pytest.raises(HTTPException) as denied:
        await ep.acknowledge_pnl_reconciliation(SYMBOL, body, user=viewer)
    assert denied.value.status_code == 403
    assert pos.get(UID, SYMBOL).pnl_reconciliation_required is True

    with pytest.raises(HTTPException) as short:
        await ep.acknowledge_pnl_reconciliation(
            SYMBOL, ep.PnlAcknowledgeRequest(note="  "), user=admin)
    assert short.value.status_code == 400

    out = await ep.acknowledge_pnl_reconciliation(SYMBOL, body, user=admin)
    assert out["cleared"] is True
    assert pos.get(UID, SYMBOL).pnl_reconciliation_required is False

    with pytest.raises(HTTPException) as missing:
        await ep.acknowledge_pnl_reconciliation("NOSUCH", body, user=admin)
    assert missing.value.status_code == 404
