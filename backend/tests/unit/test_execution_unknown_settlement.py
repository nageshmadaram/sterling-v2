"""A submit that provably never left the process is REJECTED, not UNKNOWN; an
UNKNOWN with no broker evidence settles from the day order book; a refused
reduction proof leaves no SUBMITTING row; a lost journal acknowledgement fences
the account instead of escaping."""
import asyncio
import time
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import db, live_safety
from app.services.execution_service import (
    CanonicalExecutionService,
    ExecutionRequest,
    ExposureEffect,
    RiskApproval,
)
from app.services.kite_engine import execution_lifecycle as life
from app.services.kite_engine import order_journal


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "app.services.execution_service.snapback_live_gate_decision",
        lambda **kw: SimpleNamespace(allowed=True, protective=False, reasons=[]),
        raising=False,
    )
    monkeypatch.setattr("app.core.lane_registry.entry_transport_allowed", lambda _c: True)
    db_file = str(tmp_path / "unknown_settlement.db")
    monkeypatch.setattr(db, "_DB_PATH", db_file)
    monkeypatch.setattr(db, "_available", True)
    live_safety.reset_all_for_tests()
    db.init()
    from app.services.kite_engine import positions
    from app.services import gamma_move_positions
    positions._positions.clear()
    gamma_move_positions.reset()
    with db._conn() as conn:
        conn.execute("DELETE FROM execution_control")
        conn.execute("DELETE FROM kite_order_intents")
    db.set_recovery_state(recovery_state="CLEAN", reason_code="RECONCILIATION_CLEAN",
                          reason="test baseline", uid="u1", account_id="acc1")
    yield
    live_safety.reset_all_for_tests()


class Broker:
    _account_id = "acc1"

    def __init__(self, error=None, rows=None):
        self.error = error
        self.rows = rows
        self.sent = 0
        # Entries use an unspecified transport so the day-one envelope (which
        # has its own tests) does not decide; reductions must prove live
        # inventory, so a broker with rows is a live one.
        self._is_paper = False if rows is not None else None

    async def get_positions_raw(self):
        return {"net": list(self.rows or []), "day": []}

    async def place_order(self, **kwargs):
        self.sent += 1
        if self.error is not None:
            raise self.error
        return {"order_id": "ORD-1"}


def _entry(signal="sig-entry"):
    req = ExecutionRequest(
        uid="u1", account_id="acc1", strategy_id="sterling-kite", generation_id="gen1",
        signal_id=signal, exchange="NSE", symbol="INFY", side="BUY", quantity=5,
    )
    approval = RiskApproval(
        approval_id="ap-" + signal, uid="u1", account_id="acc1", strategy_id="sterling-kite",
        signal_id=signal, symbol="INFY", side="BUY", quantity=5, generation_id="gen1",
        available_capital=100000.0,
    )
    return req, approval


def _exit(signal="sig-exit", qty=10):
    return ExecutionRequest(
        uid="u1", account_id="acc1", strategy_id="sterling-kite", generation_id="gen1",
        signal_id=signal, exchange="NSE", symbol="SBIN", side="SELL", quantity=qty,
        exposure_effect=ExposureEffect.CLOSE_POSITION, payload={"product": "NRML"},
    )


def _intent(req):
    keys = [i for i in _all_intents() if i.signal_id == req.signal_id]
    assert len(keys) == 1
    return keys[0]


def _all_intents():
    out = []
    with db._conn() as conn:
        for row in conn.execute("SELECT intent_key FROM kite_order_intents").fetchall():
            out.append(order_journal.get(row["intent_key"]))
    return out


@pytest.mark.kite_features('default', 'u1')
@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    httpx.ConnectError("name resolution failed"),
    httpx.ConnectTimeout("connect timed out"),
    httpx.PoolTimeout("no free connection"),
])
async def test_pre_write_transport_failure_is_a_definitive_rejection(error):
    req, approval = _entry()
    res = await CanonicalExecutionService().submit_order(
        req, broker_client=Broker(error=error), risk_approval=approval)

    assert res.success is False and res.status == "REJECTED"
    assert _intent(req).state == "REJECTED"
    # Nothing was sent, so nothing needs reconciling and no later order is fenced.
    assert db.get_execution_control(uid="u1", account_id="acc1")["recovery_state"] == "CLEAN"


@pytest.mark.kite_features('default', 'u1')
@pytest.mark.asyncio
async def test_a_read_timeout_stays_unknown_and_fences_the_account():
    req, approval = _entry()
    res = await CanonicalExecutionService().submit_order(
        req, broker_client=Broker(error=httpx.ReadTimeout("no answer")), risk_approval=approval)

    assert res.status == "UNKNOWN"
    assert _intent(req).state == "UNKNOWN"
    assert db.get_execution_control(uid="u1", account_id="acc1")["recovery_state"] == "RECOVERY_REQUIRED"


@pytest.mark.kite_features('default', 'u1')
@pytest.mark.asyncio
async def test_a_cancelled_send_is_recorded_as_uncertain_not_left_submitting():
    req, approval = _entry()
    with pytest.raises(asyncio.CancelledError):
        await CanonicalExecutionService().submit_order(
            req, broker_client=Broker(error=asyncio.CancelledError()), risk_approval=approval)

    assert _intent(req).state == "UNKNOWN"
    assert db.get_execution_control(uid="u1", account_id="acc1")["recovery_state"] == "RECOVERY_REQUIRED"


@pytest.mark.asyncio
async def test_a_refused_reduction_proof_leaves_a_reserved_row_not_a_submitting_one():
    # The durable ledger still shows the holding but the broker holds nothing, so
    # the fresh-inventory proof refuses before any claim is taken.
    db.set_inventory(account_id="acc1", uid="u1", symbol="SBIN", net_quantity=10, exchange="NSE")
    broker = Broker(rows=[])
    res = await CanonicalExecutionService().submit_order(_exit(), broker_client=broker)

    assert res.success is False and res.status == "REJECTED"
    assert broker.sent == 0
    assert _intent(_exit()).state == "RESERVED"

    # With inventory visible, the same intent key goes through on the retry.
    broker.rows = [{"tradingsymbol": "SBIN", "exchange": "NSE", "product": "NRML", "quantity": 10}]
    again = await CanonicalExecutionService().submit_order(_exit(), broker_client=broker)
    assert again.success is True and again.order_id == "ORD-1"
    assert broker.sent == 1


@pytest.mark.asyncio
async def test_broker_reduction_inventory_disappearing_between_proofs_never_sends():
    """Pre-reservation inventory is not sufficient to authorize a final SELL."""
    db.set_inventory(account_id="acc1", uid="u1", symbol="SBIN", net_quantity=10, exchange="NSE")

    class BrokerThatLosesInventory(Broker):
        def __init__(self):
            super().__init__(rows=[{
                "tradingsymbol": "SBIN", "exchange": "NSE",
                "product": "NRML", "quantity": 10,
            }])
            self.reads = 0

        async def get_positions_raw(self):
            self.reads += 1
            if self.reads == 1:
                return await super().get_positions_raw()
            return {"net": [], "day": []}

    broker = BrokerThatLosesInventory()
    req = _exit(signal="inventory-flipped-before-send")
    result = await CanonicalExecutionService().submit_order(req, broker_client=broker)
    assert result.success is False
    assert result.status == "REJECTED"
    assert "reduction proof changed" in result.error
    assert broker.reads == 2
    assert broker.sent == 0
    assert _intent(req).state == "REJECTED"
    assert db.get_execution_control(uid="u1", account_id="acc1")["recovery_state"] == "CLEAN"


@pytest.mark.kite_features('default', 'u1')
@pytest.mark.asyncio
async def test_a_lost_journal_acknowledgement_fences_the_account(monkeypatch):
    req, approval = _entry()

    def boom(*_a, **_k):
        raise RuntimeError("disk I/O error")

    monkeypatch.setattr(order_journal, "acknowledge", boom)
    broker = Broker()
    res = await CanonicalExecutionService().submit_order(
        req, broker_client=broker, risk_approval=approval)

    assert broker.sent == 1
    assert res.success is False and res.status == "UNKNOWN"
    assert res.order_id == "ORD-1"
    assert _intent(req).state == "UNKNOWN"
    assert db.get_execution_control(uid="u1", account_id="acc1")["recovery_state"] == "RECOVERY_REQUIRED"


# -- recover(): the day order book settles an intent that has no broker evidence --

def _unknown_intent(*, age_s, signal="sig-lost", created_ms=None):
    intent = order_journal.reserve(
        uid="u1", account_id="acc1", strategy_id="sterling-kite", generation_id="gen1",
        signal_id=signal, exchange="NFO", symbol="OPT", side="BUY", quantity=75,
        payload={"product": "NRML"})
    assert order_journal.claim_submission(intent.intent_key)
    order_journal.submission_uncertain(intent.intent_key, "ReadTimeout")
    stamp = created_ms if created_ms is not None else int((time.time() - age_s) * 1000)
    with db._conn() as conn:
        conn.execute("UPDATE kite_order_intents SET created_ms=?, updated_ms=? WHERE intent_key=?",
                     (stamp, stamp, intent.intent_key))
    return intent


class BookClient:
    _is_paper = False
    _account_id = "acc1"

    def __init__(self, book):
        self.get_orders = AsyncMock(return_value=book)
        self.get_order_history = AsyncMock(return_value=[])


@pytest.mark.asyncio
async def test_real_unknown_remains_fenced_without_broker_completeness_proof():
    intent = _unknown_intent(age_s=600)
    await life.recover(BookClient([]), "u1")
    settled = order_journal.get(intent.intent_key)
    assert settled.state == "UNKNOWN"


@pytest.mark.asyncio
async def test_recover_leaves_a_fresh_unknown_alone():
    intent = _unknown_intent(age_s=30)
    await life.recover(BookClient([]), "u1")
    assert order_journal.get(intent.intent_key).state == "UNKNOWN"


@pytest.mark.asyncio
async def test_recover_leaves_a_previous_day_unknown_to_the_operator():
    intent = _unknown_intent(age_s=0, created_ms=int((time.time() - 3 * 86400) * 1000))
    await life.recover(BookClient([]), "u1")
    assert order_journal.get(intent.intent_key).state == "UNKNOWN"


@pytest.mark.asyncio
async def test_recover_does_not_settle_when_a_foreign_order_has_the_tag_ambiguously():
    intent = _unknown_intent(age_s=600)
    twin = {"order_id": "A", "tag": intent.tag, "tradingsymbol": "OPT", "exchange": "NFO",
            "status": "OPEN", "quantity": 75, "filled_quantity": 0}
    book = [twin, {**twin, "order_id": "B"}]
    await life.recover(BookClient(book), "u1")
    assert order_journal.get(intent.intent_key).state == "UNKNOWN"


def test_the_periodic_pass_sees_a_mid_flight_intent_under_a_clean_control_row():
    from app.services import live_runtime

    assert live_runtime._journal_unsettled("u1", "acc1") is False
    intent = order_journal.reserve(
        uid="u1", account_id="acc1", strategy_id="sterling-kite", generation_id="gen1",
        signal_id="sig-mid-flight", exchange="NFO", symbol="OPT", side="BUY", quantity=75,
        payload={"product": "NRML"})
    assert order_journal.claim_submission(intent.intent_key)
    assert db.get_execution_control(uid="u1", account_id="acc1")["recovery_state"] == "CLEAN"
    assert live_runtime._journal_unsettled("u1", "acc1") is True
