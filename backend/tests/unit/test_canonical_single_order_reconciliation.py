"""Offline canonical broker-order observation: read-only, account-scoped, fail-closed."""
import pytest

from app.services import db
from app.services.execution_service import CanonicalExecutionService
from app.services.kite_engine import order_journal


@pytest.fixture
def claimed(tmp_path, monkeypatch):
    db_path = str(tmp_path / "canonical_observation.sqlite")
    monkeypatch.setattr(db, "_DB_PATH", db_path)
    monkeypatch.setattr(db, "_available", True)
    db.init()
    row = order_journal.reserve(
        uid="observer", account_id="synthetic-account", strategy_id="synthetic",
        generation_id="gen1", signal_id="sig1", exchange="NFO",
        symbol="SYNTHETIC-OPTION", side="BUY", quantity=2,
        payload={"product": "NRML"},
    )
    assert order_journal.claim_submission(row.intent_key)
    order_journal.acknowledge(row.intent_key, "SYNTHETIC-ORDER")
    return row


class ReadOnlyBroker:
    _account_id = "synthetic-account"
    _is_paper = True

    def __init__(self, rows):
        self.rows = rows
        self.reads = 0
        self.writes = 0

    async def get_order_history(self, order_id):
        assert order_id == "SYNTHETIC-ORDER"
        self.reads += 1
        return self.rows

    async def get_order_trades(self, order_id):
        return []

    async def place_order(self, **kwargs):
        self.writes += 1
        raise AssertionError("reconciliation must never send an order")

    async def cancel_order(self, **kwargs):
        self.writes += 1
        raise AssertionError("reconciliation must never cancel an order")


async def observe(client, method="reconcile_order", account="synthetic-account"):
    return await getattr(CanonicalExecutionService(), method)(
        uid="observer", account_id=account, order_id="SYNTHETIC-ORDER",
        broker_client=client,
    )


@pytest.mark.asyncio
async def test_verified_fill_is_recorded_exactly_once_and_never_dispatches(claimed):
    broker = ReadOnlyBroker([{
        "order_id": "SYNTHETIC-ORDER", "status": "COMPLETE",
        "filled_quantity": 2, "average_price": 120,
    }])
    first = await observe(broker)
    assert first.success and first.status == "FILLED"
    assert first.filled_quantity == 2 and first.filled_price == 120
    assert order_journal.get(claimed.intent_key).projection_pending
    replay = await observe(broker, method="record_execution_outcome")
    assert replay.success and replay.revision == first.revision
    assert replay.filled_quantity == 2
    assert broker.reads == 2 and broker.writes == 0


@pytest.mark.asyncio
async def test_broker_account_mismatch_is_rejected_before_read(claimed):
    broker = ReadOnlyBroker([])
    broker._account_id = "another-account"
    result = await observe(broker)
    assert not result.success and result.status == "REJECTED"
    assert broker.reads == 0 and broker.writes == 0
    assert order_journal.get(claimed.intent_key).state == "SUBMITTED"


@pytest.mark.asyncio
async def test_cross_order_evidence_requires_recovery_without_projecting(claimed):
    broker = ReadOnlyBroker([{
        "order_id": "FOREIGN-ORDER", "status": "COMPLETE",
        "filled_quantity": 2, "average_price": 120,
    }])
    result = await observe(broker)
    assert not result.success and result.status == "UNKNOWN"
    assert order_journal.get(claimed.intent_key).filled_quantity == 0
    ctrl = db.get_execution_control(uid="observer", account_id="synthetic-account")
    assert ctrl["recovery_state"] == "RECOVERY_REQUIRED"
    assert broker.writes == 0


@pytest.mark.asyncio
async def test_overfilled_broker_order_is_quarantined_without_replay(claimed):
    broker = ReadOnlyBroker([{
        "order_id": "SYNTHETIC-ORDER", "status": "COMPLETE",
        "filled_quantity": 3, "average_price": 120,
    }])
    result = await observe(broker)
    assert not result.success and result.status == "UNKNOWN"
    row = order_journal.get(claimed.intent_key)
    assert row.reconciliation_required and row.filled_quantity == 0
    assert broker.writes == 0


@pytest.mark.asyncio
async def test_transport_without_explicit_mode_fails_closed(claimed):
    broker = ReadOnlyBroker([])
    broker._is_paper = None
    result = await observe(broker)
    assert not result.success and result.status == "REJECTED"
    assert broker.reads == broker.writes == 0
