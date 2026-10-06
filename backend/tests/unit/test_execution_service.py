import pytest
from app.services.execution_service import CanonicalExecutionService, ExecutionRequest, ExposureEffect, RiskApproval
from app.services import db
from app.services.kite_engine import positions


@pytest.fixture(autouse=True)
def _allow_snapback_family_gate(monkeypatch):
    """These tests predate the Snapback family gate and exercise other invariants.

    The gate itself is covered by tests/unit/test_snapback_live_execution_gate.py; here
    it is allowed through so the invariant under test is the one that decides.
    """
    from types import SimpleNamespace

    monkeypatch.setattr(
        "app.services.execution_service.snapback_live_gate_decision",
        lambda **kw: SimpleNamespace(allowed=True, protective=False, reasons=[]),
        raising=False,
    )
    # This file tests canonical execution mechanics, not the global live floor.
    # Dedicated release-safety tests own paper/live/unknown transport identity.
    monkeypatch.setattr(
        "app.core.lane_registry.entry_transport_allowed",
        lambda _client: True,
    )




@pytest.fixture(autouse=True)
def setup_db_for_test(tmp_path, monkeypatch):
    from app.services import live_safety
    db_file = str(tmp_path / "test_exec.db")
    monkeypatch.setenv("STERLING_DB_PATH", db_file)
    monkeypatch.setattr(db, "_DB_PATH", db_file)
    monkeypatch.setattr(db, "_available", True)
    live_safety.reset_all_for_tests()
    db.init()
    with db._conn() as conn:
        conn.execute("DELETE FROM execution_control")
    yield
    live_safety.reset_all_for_tests()


def test_execution_service_halted_rejection():
    service = CanonicalExecutionService()

    # Initially RUNNING
    allowed, msg = service.is_trading_allowed()
    assert allowed is True

    # Set to HALTED
    db.set_execution_control(operator_state="HALTED", reason="Emergency stop initiated")
    allowed, msg = service.is_trading_allowed()
    assert allowed is False
    assert "HALTED" in msg


@pytest.mark.asyncio
async def test_execution_service_risk_rejection():
    service = CanonicalExecutionService()
    req = ExecutionRequest(
        uid="u1",
        account_id="acc1",
        strategy_id="snapback",
        generation_id="gen1",
        signal_id="sig1",
        exchange="NFO",
        symbol="NIFTY26SEP24000PE",
        side="BUY",
        quantity=50,
        tag="st_1001",
    )

    res = await service.submit(req, risk_approved=False)
    assert res.success is False
    assert res.status == "REJECTED"
    assert "Risk check rejected" in res.error


@pytest.mark.asyncio
async def test_execution_service_broker_send_ack():
    class MockBrokerClient:
        _account_id = "acc1"
        async def place_order(self, **kwargs):
            return {"order_id": "ORD_12345"}

    service = CanonicalExecutionService()
    req = ExecutionRequest(
        uid="u1",
        account_id="acc1",
        strategy_id="snapback",
        generation_id="gen1",
        signal_id="sig101",
        exchange="NSE",
        symbol="RELIANCE",
        side="BUY",
        quantity=10,
        tag="st_1002",
    )

    approval = RiskApproval(
        approval_id="app1",
        uid="u1",
        account_id="acc1",
        strategy_id="snapback",
        signal_id="sig101",
        symbol="RELIANCE",
        side="BUY",
        quantity=10,
        generation_id="gen1",
        available_capital=100000.0,
    )

    res = await service.submit_order(req, broker_client=MockBrokerClient(), risk_approval=approval)
    assert res.success is True
    assert res.status == "ACKNOWLEDGED"
    assert res.order_id == "ORD_12345"


@pytest.mark.asyncio
async def test_execution_service_transport_uncertainty():
    class TimeoutBrokerClient:
        _account_id = "acc1"
        async def place_order(self, **kwargs):
            raise TimeoutError("Broker Gateway Timeout 504")

    service = CanonicalExecutionService()
    req = ExecutionRequest(
        uid="u1",
        account_id="acc1",
        strategy_id="snapback",
        generation_id="gen1",
        signal_id="sig102",
        exchange="NSE",
        symbol="INFY",
        side="BUY",
        quantity=5,
        tag="st_1003",
    )

    approval = RiskApproval(
        approval_id="app2",
        uid="u1",
        account_id="acc1",
        strategy_id="snapback",
        signal_id="sig102",
        symbol="INFY",
        side="BUY",
        quantity=5,
        generation_id="gen1",
        available_capital=100000.0,
    )

    res = await service.submit_order(req, broker_client=TimeoutBrokerClient(), risk_approval=approval)
    assert res.success is False
    assert res.status == "UNKNOWN"

    ctrl = db.get_execution_control(uid="u1", account_id="acc1")
    assert ctrl["recovery_state"] == "RECOVERY_REQUIRED"


@pytest.mark.asyncio
async def test_execution_service_cancel_and_protection(monkeypatch):
    # Exercise the real Family Mode transport contract: canonical service methods
    # must carry the correct capability through the broker call.
    monkeypatch.setenv("STERLING_FAMILY_MODE", "true")

    class MockBrokerClient:
        _account_id = "acc1"
        _is_paper = False

        async def cancel_order(self, variety, order_id):
            from app.services.snapback_family_mode import guard_broker_write
            guard_broker_write("cancel_order")
            return True

        async def get_positions_raw(self):
            return {
                "net": [{
                    "tradingsymbol": "SBIN",
                    "exchange": "NSE",
                    "product": "NRML",
                    "quantity": 10,
                }]
            }

        async def place_gtt(self, **kwargs):
            from app.services.snapback_family_mode import guard_broker_write
            guard_broker_write("place_gtt")
            assert kwargs["orders"][0]["quantity"] == 10
            assert kwargs["orders"][0]["product"] == "NRML"
            return {"trigger_id": "999"}

    service = CanonicalExecutionService()
    db.set_execution_control(operator_state="HALTED", uid="u1", account_id="acc1")

    # A live generic cancel is permitted during HALTED only when durable identity
    # proves the target is an unresolved canonical ENTRY.
    from app.services.kite_engine import order_journal

    entry = order_journal.reserve(
        uid="u1",
        account_id="acc1",
        strategy_id="snapback",
        generation_id="g-cancel",
        signal_id="sig-cancel",
        exchange="NSE",
        symbol="SBIN",
        side="BUY",
        quantity=10,
        payload={"intent_type": "ENTRY"},
    )
    assert order_journal.claim_submission(entry.intent_key)
    order_journal.transition(entry.intent_key, "SUBMITTED", order_id="ORD_12345")

    cancel_res = await service.cancel_order(
        uid="u1", account_id="acc1", order_id="ORD_12345", broker_client=MockBrokerClient()
    )
    assert cancel_res.success is True
    assert cancel_res.status == "ACKNOWLEDGED"

    # Register open position for protection placement
    positions.register(
        positions.OpenPosition(
            uid="u1", account_id="acc1", symbol="SBIN", exchange="NSE", qty=10, order_id="ORD_12345", status=positions.OPEN
        )
    )

    # Protection permitted even when HALTED
    prot_res = await service.place_protection(
        uid="u1",
        account_id="acc1",
        position_id="ORD_12345",
        protection_params={"trigger_type": "single", "tradingsymbol": "SBIN", "trigger_premium": 90.0},
        broker_client=MockBrokerClient(),
    )
    assert prot_res.success is True
    assert prot_res.order_id == "999"

    # No stop level, no broker stop: refused rather than sent as a malformed GTT.
    positions.register(
        positions.OpenPosition(
            uid="u1", account_id="acc1", symbol="SBIN2", exchange="NSE", qty=10, order_id="ORD_9", status=positions.OPEN
        )
    )
    missing = await service.place_protection(
        uid="u1", account_id="acc1", position_id="ORD_9",
        protection_params={"trigger_type": "single", "tradingsymbol": "SBIN2"},
        broker_client=MockBrokerClient(),
    )
    assert missing.success is False and "stop price is missing" in (missing.error or "")


@pytest.mark.asyncio
async def test_live_generic_cancel_refuses_exit_intent_even_when_halted():
    from app.services.kite_engine import order_journal

    class Broker:
        _account_id = "acc1"
        _is_paper = False

        def __init__(self):
            self.calls = 0

        async def cancel_order(self, **_kwargs):
            self.calls += 1
            return {"order_id": "EXIT_1"}

    db.set_execution_control(operator_state="HALTED", uid="u1", account_id="acc1")
    intent = order_journal.reserve(
        uid="u1",
        account_id="acc1",
        strategy_id="snapback",
        generation_id="g-exit",
        signal_id="sig-exit",
        exchange="NSE",
        symbol="SBIN",
        side="SELL",
        quantity=10,
        payload={"intent_type": "EXIT"},
    )
    assert order_journal.claim_submission(intent.intent_key)
    order_journal.transition(intent.intent_key, "SUBMITTED", order_id="EXIT_1")

    broker = Broker()
    out = await CanonicalExecutionService().cancel_order(
        uid="u1", account_id="acc1", order_id="EXIT_1", broker_client=broker
    )

    assert out.success is False
    assert out.status == "REJECTED"
    assert "ENTRY orders" in out.error
    assert broker.calls == 0


@pytest.mark.asyncio
async def test_live_generic_cancel_refuses_unknown_order_identity():
    class Broker:
        _account_id = "acc1"
        _is_paper = False

        def __init__(self):
            self.calls = 0

        async def cancel_order(self, **_kwargs):
            self.calls += 1
            return {"order_id": "UNKNOWN"}

    broker = Broker()
    out = await CanonicalExecutionService().cancel_order(
        uid="u1", account_id="acc1", order_id="UNKNOWN", broker_client=broker
    )

    assert out.success is False
    assert out.status == "REJECTED"
    assert "ENTRY orders" in out.error
    assert broker.calls == 0


@pytest.mark.asyncio
async def test_startup_recovery_clean():
    service = CanonicalExecutionService()
    res = await service.startup_recovery(uid="u1", account_id="acc1")
    assert res["status"] == "success"
    assert res["recovery_state"] == "CLEAN"
