"""The master live floor, its kill switch, and the day-one exposure envelope.

Sterling is a live-money product with one execution mode. These tests defend the
three things that make that safe to ship:

* with nothing set, a live broker client may originate entries (default ON);
* the operator kill switch (``STERLING_LIVE_EXECUTION=0``) refuses every new
  entry with ``HALTED`` -- and never touches an exit, which must keep working;
* a client that does not state its broker mode is refused as unknown, and the
  day-one envelope (``live_minimum``) refuses an entry that does not fit it,
  failing closed on anything it cannot read.

They run through :class:`CanonicalExecutionService`, the one place every order
passes, so no strategy can bypass them by forgetting its own check.
"""
from __future__ import annotations

import pytest

from app.core import lane_registry
from app.services import db, live_safety
from app.services.execution_service import (
    CanonicalExecutionService,
    ExecutionRequest,
    ExposureEffect,
    RiskApproval,
)


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    from types import SimpleNamespace

    # The Snapback family gate has its own tests; let it through so the floor
    # and the envelope are what decide here. Crucially, entry_transport_allowed
    # is NOT patched: the floor under test is the real one.
    monkeypatch.setattr(
        "app.services.execution_service.snapback_live_gate_decision",
        lambda **kw: SimpleNamespace(allowed=True, protective=False, reasons=[]),
        raising=False,
    )
    for name in (
        "STERLING_LIVE_MINIMUM_DAILY_LOSS_INR",
        "STERLING_LIVE_MINIMUM_GROSS_EXPOSURE_INR",
        "STERLING_LIVE_MINIMUM_MAX_DEPLOYED_PCT",
        "STERLING_LIVE_MINIMUM_MAX_LOTS",
    ):
        monkeypatch.delenv(name, raising=False)

    db_file = tmp_path / "floor.db"
    monkeypatch.setattr(db, "_DB_PATH", str(db_file))
    live_safety.reset_all_for_tests()
    db.init()
    with db._conn() as conn:
        conn.execute("DELETE FROM execution_control")
        conn.execute("DELETE FROM kite_order_intents")
    yield
    live_safety.reset_all_for_tests()


class _LiveBroker:
    """A broker double that states it is a real (not test-double) transport."""

    _is_paper = False

    def __init__(self, account_id: str, *, net=()):
        self._account_id = account_id
        self.orders: list[dict] = []
        self._net = list(net)

    async def get_positions_raw(self):
        return {"net": list(self._net), "day": []}

    async def place_order(self, **kwargs):
        self.orders.append(kwargs)
        return {"order_id": f"ORD-{len(self.orders)}"}


def _entry(tag: str, *, quantity: int = 75, symbol: str = "NIFTY26OCT25000CE",
           price: float = 120.0, payload: dict | None = None):
    uid, account = f"u-{tag}", f"acct-{tag}"
    from tests.feature_grants import grant_kite
    grant_kite(uid)
    request = ExecutionRequest(
        uid=uid, account_id=account, strategy_id="sterling-kite",
        generation_id="g1", signal_id=f"sig-{tag}", exchange="NFO", symbol=symbol,
        side="BUY", quantity=quantity, order_type="LIMIT", price=price,
        payload={"lot_size": 75} if payload is None else payload,
    )
    approval = RiskApproval(
        approval_id=f"ap-{tag}", uid=uid, account_id=account,
        strategy_id=request.strategy_id, signal_id=request.signal_id,
        symbol=symbol, side="BUY", quantity=quantity, generation_id="g1",
        available_capital=500_000.0, capital_required=price * quantity,
    )
    return request, approval, account


# --------------------------------------------------------------------------
# The master floor and its kill switch
# --------------------------------------------------------------------------
@pytest.mark.kite_features('default')
@pytest.mark.asyncio
async def test_a_live_entry_is_sent_when_nothing_has_turned_the_floor_off(monkeypatch):
    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", True)
    request, approval, account = _entry("default-on")
    broker = _LiveBroker(account)

    result = await CanonicalExecutionService().submit_order(
        request, broker_client=broker, risk_approval=approval
    )

    assert result.success is True, result.error
    assert result.status == "ACKNOWLEDGED"
    assert len(broker.orders) == 1


@pytest.mark.kite_features('default')
@pytest.mark.asyncio
async def test_the_kill_switch_refuses_a_live_entry_as_halted(monkeypatch):
    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", False)
    request, approval, account = _entry("kill")
    broker = _LiveBroker(account)

    result = await CanonicalExecutionService().submit_order(
        request, broker_client=broker, risk_approval=approval
    )

    assert result.success is False
    assert result.status == "HALTED"
    assert "kill switch" in result.error
    assert lane_registry.LIVE_EXECUTION_ENV in result.error
    assert broker.orders == []


@pytest.mark.asyncio
async def test_the_kill_switch_never_stops_an_exit(monkeypatch):
    """A switch that stopped Sterling closing a position would be a way to lose money."""
    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", False)
    account, uid = "acct-exit", "u-exit"
    db.set_inventory(account_id=account, uid=uid, symbol="SBIN", net_quantity=20, exchange="NSE")
    broker = _LiveBroker(account, net=[{
        "tradingsymbol": "SBIN", "exchange": "NSE", "product": "NRML", "quantity": 20,
    }])
    request = ExecutionRequest(
        uid=uid, account_id=account, strategy_id="sterling-kite", generation_id="g1",
        signal_id="sig-exit", exchange="NSE", symbol="SBIN", side="SELL", quantity=20,
        exposure_effect=ExposureEffect.CLOSE_POSITION, payload={"product": "NRML"},
    )

    result = await CanonicalExecutionService().submit_order(request, broker_client=broker)

    assert result.success is True, result.error
    assert len(broker.orders) == 1
    assert broker.orders[0]["transaction_type"] == "SELL"


@pytest.mark.kite_features('default')
@pytest.mark.asyncio
async def test_a_client_that_does_not_state_its_mode_is_refused_as_unknown(monkeypatch):
    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", True)

    class UnlabelledBroker:
        def __init__(self, account_id):
            self._account_id = account_id
            self.calls = 0

        async def place_order(self, **kwargs):
            self.calls += 1
            return {"order_id": "MUST-NOT-HAPPEN"}

    request, approval, account = _entry("unknown-mode")
    broker = UnlabelledBroker(account)

    result = await CanonicalExecutionService().submit_order(
        request, broker_client=broker, risk_approval=approval
    )

    assert result.success is False
    assert result.status == "HALTED"
    assert "does not state its mode" in result.error
    assert broker.calls == 0


# --------------------------------------------------------------------------
# The day-one exposure envelope
# --------------------------------------------------------------------------
@pytest.mark.kite_features('default')
@pytest.mark.asyncio
async def test_a_normal_one_lot_entry_fits_the_default_envelope(monkeypatch):
    """The shipped defaults are derived, not invented: they must not block ordinary trading."""
    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", True)
    request, approval, account = _entry("normal", quantity=75, price=150.0)
    broker = _LiveBroker(account)

    result = await CanonicalExecutionService().submit_order(
        request, broker_client=broker, risk_approval=approval
    )

    assert result.success is True, result.error
    assert len(broker.orders) == 1


@pytest.mark.kite_features('default')
@pytest.mark.asyncio
async def test_an_entry_over_the_gross_exposure_limit_is_refused_before_it_is_journaled(monkeypatch):
    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", True)
    monkeypatch.setenv("STERLING_LIVE_MINIMUM_GROSS_EXPOSURE_INR", "5000")
    request, approval, account = _entry("cap", quantity=75, price=120.0)  # 9,000
    broker = _LiveBroker(account)

    result = await CanonicalExecutionService().submit_order(
        request, broker_client=broker, risk_approval=approval
    )

    assert result.success is False
    assert result.status == "REJECTED_BY_LIVE_MINIMUM"
    assert "GROSS_EXPOSURE_EXCEEDED" in result.error
    assert broker.orders == []
    # Refused before the journal: nothing was reserved, so nothing is unresolved.
    with db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM kite_order_intents").fetchone()[0] == 0


@pytest.mark.kite_features('default')
@pytest.mark.asyncio
async def test_a_deployed_pct_below_100_tightens_the_derived_limit(monkeypatch):
    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", True)
    # 500,000 verified x 1% = 5,000 < the 9,000 order.
    monkeypatch.setenv("STERLING_LIVE_MINIMUM_MAX_DEPLOYED_PCT", "1")
    request, approval, account = _entry("pct", quantity=75, price=120.0)
    broker = _LiveBroker(account)

    result = await CanonicalExecutionService().submit_order(
        request, broker_client=broker, risk_approval=approval
    )

    assert result.status == "REJECTED_BY_LIVE_MINIMUM"
    assert "GROSS_EXPOSURE_EXCEEDED" in result.error
    assert broker.orders == []


@pytest.mark.kite_features('default')
@pytest.mark.asyncio
async def test_a_lot_cap_refuses_a_bigger_entry(monkeypatch):
    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", True)
    monkeypatch.setenv("STERLING_LIVE_MINIMUM_MAX_LOTS", "1")
    request, approval, account = _entry("lots", quantity=150, price=50.0,
                                        payload={"lot_size": 75})
    broker = _LiveBroker(account)

    result = await CanonicalExecutionService().submit_order(
        request, broker_client=broker, risk_approval=approval
    )

    assert result.status == "REJECTED_BY_LIVE_MINIMUM"
    assert "QUANTITY_ABOVE_MINIMUM" in result.error
    assert broker.orders == []


@pytest.mark.kite_features('default')
@pytest.mark.asyncio
async def test_a_lot_cap_with_an_unknown_lot_size_fails_closed(monkeypatch):
    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", True)
    monkeypatch.setenv("STERLING_LIVE_MINIMUM_MAX_LOTS", "1")
    request, approval, account = _entry("lotsize-unknown", quantity=75, price=50.0, payload={})
    broker = _LiveBroker(account)

    result = await CanonicalExecutionService().submit_order(
        request, broker_client=broker, risk_approval=approval
    )

    assert result.status == "REJECTED_BY_LIVE_MINIMUM"
    assert "MINIMUM_QUANTITY_UNKNOWN" in result.error
    assert broker.orders == []


class _LotSizedBroker(_LiveBroker):
    """The real KiteClient answers EXCHANGE:SYMBOL -> lot size from its instrument dump."""

    def __init__(self, account_id, sizes):
        super().__init__(account_id)
        self._sizes = sizes

    async def instrument_lot_sizes(self, symbols):
        return {key: self._sizes[key] for key in symbols if key in self._sizes}


@pytest.mark.kite_features('default')
@pytest.mark.asyncio
async def test_the_lot_cap_reads_the_lot_size_from_the_broker_instrument_dump(monkeypatch):
    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", True)
    monkeypatch.setenv("STERLING_LIVE_MINIMUM_MAX_LOTS", "1")
    sizes = {"NFO:NIFTY26OCT25000CE": 75}
    service = CanonicalExecutionService()

    one_lot, approval, account = _entry("one-lot", quantity=75, price=100.0)
    broker = _LotSizedBroker(account, sizes)
    ok = await service.submit_order(one_lot, broker_client=broker, risk_approval=approval)
    assert ok.success is True, ok.error

    two_lots, approval2, account2 = _entry("two-lots", quantity=150, price=100.0)
    broker2 = _LotSizedBroker(account2, sizes)
    refused = await service.submit_order(two_lots, broker_client=broker2, risk_approval=approval2)
    assert refused.status == "REJECTED_BY_LIVE_MINIMUM"
    assert "QUANTITY_ABOVE_MINIMUM" in refused.error
    assert broker2.orders == []


@pytest.mark.kite_features('default')
@pytest.mark.asyncio
async def test_an_unusable_limit_is_refused_not_widened_to_the_default(monkeypatch):
    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", True)
    monkeypatch.setenv("STERLING_LIVE_MINIMUM_GROSS_EXPOSURE_INR", "lots")
    request, approval, account = _entry("typo")
    broker = _LiveBroker(account)

    result = await CanonicalExecutionService().submit_order(
        request, broker_client=broker, risk_approval=approval
    )

    assert result.status == "REJECTED_BY_LIVE_MINIMUM"
    assert "LIVE_MINIMUM_CONFIG_INVALID" in result.error
    assert broker.orders == []


@pytest.mark.kite_features('default')
@pytest.mark.asyncio
async def test_unreadable_inventory_fails_closed(monkeypatch):
    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", True)

    def _boom(*_a, **_k):
        raise RuntimeError("inventory database unavailable")

    monkeypatch.setattr(db, "get_inventory_all_strict", _boom)
    request, approval, account = _entry("inv-unreadable")
    broker = _LiveBroker(account)

    result = await CanonicalExecutionService().submit_order(
        request, broker_client=broker, risk_approval=approval
    )

    assert result.status == "REJECTED_BY_LIVE_MINIMUM"
    assert "GROSS_EXPOSURE_UNKNOWN" in result.error
    assert broker.orders == []


@pytest.mark.kite_features('default')
@pytest.mark.asyncio
async def test_a_spent_daily_loss_budget_refuses_a_new_entry(monkeypatch):
    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", True)
    monkeypatch.setattr(
        live_safety, "daily_loss_state",
        lambda *_a, **_k: {"pnl_inr": -1200.0, "hard_halt_inr": -1500.0,
                           "level": "ok", "enabled": True},
    )
    # A tighter operator budget than the account's own halt.
    monkeypatch.setenv("STERLING_LIVE_MINIMUM_DAILY_LOSS_INR", "1000")
    request, approval, account = _entry("loss")
    broker = _LiveBroker(account)

    result = await CanonicalExecutionService().submit_order(
        request, broker_client=broker, risk_approval=approval
    )

    assert result.status == "REJECTED_BY_LIVE_MINIMUM"
    assert "DAILY_LOSS_BUDGET_SPENT" in result.error
    assert broker.orders == []


@pytest.mark.kite_features('default')
@pytest.mark.asyncio
async def test_an_unreadable_daily_loss_fails_closed(monkeypatch):
    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", True)

    def _boom(*_a, **_k):
        raise RuntimeError("pnl store down")

    monkeypatch.setattr(live_safety, "daily_loss_state", _boom)
    request, approval, account = _entry("loss-unreadable")
    broker = _LiveBroker(account)

    result = await CanonicalExecutionService().submit_order(
        request, broker_client=broker, risk_approval=approval
    )

    assert result.status in ("REJECTED_BY_LIVE_MINIMUM", "HALTED")
    assert broker.orders == []


@pytest.mark.asyncio
async def test_the_envelope_does_not_hold_a_test_double_transport(monkeypatch):
    """A double that states it is not a real broker moves no capital, so it is exempt."""
    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", True)
    monkeypatch.setenv("STERLING_LIVE_MINIMUM_GROSS_EXPOSURE_INR", "1")
    request, approval, account = _entry("double")
    broker = _LiveBroker(account)
    broker._is_paper = True

    result = await CanonicalExecutionService().submit_order(
        request, broker_client=broker, risk_approval=approval
    )

    assert result.status != "REJECTED_BY_LIVE_MINIMUM"


# --------------------------------------------------------------------------
# The gate's own pieces
# --------------------------------------------------------------------------
class TestOpenGrossExposure:
    def test_it_sums_net_quantity_times_cost(self):
        from app.services.live_minimum_gate import open_gross_exposure

        rows = [
            {"net_quantity": 75, "average_cost": 100.0},
            {"net_quantity": -50, "average_cost": 10.0},
            {"net_quantity": 0, "average_cost": 999.0},
        ]
        assert open_gross_exposure(rows) == 8000.0

    def test_no_rows_is_flat_but_an_unreadable_book_is_unknown(self):
        from app.services.live_minimum_gate import open_gross_exposure

        assert open_gross_exposure([]) == 0.0
        assert open_gross_exposure(None) is None

    def test_a_held_position_with_no_cost_basis_is_unknown_not_free(self):
        from app.services.live_minimum_gate import open_gross_exposure

        assert open_gross_exposure([{"net_quantity": 75, "average_cost": 0}]) is None

    def test_a_row_the_ledger_flags_as_contradictory_is_unknown(self):
        from app.services.live_minimum_gate import open_gross_exposure

        row = {"net_quantity": 75, "average_cost": 100.0, "reconciliation_required": True}
        assert open_gross_exposure([row]) is None


class TestResolveEnvelope:
    def _resolve(self, **kw):
        from app.core.live_minimum import configured_envelope
        from app.services.live_minimum_gate import resolve_envelope

        explicit = configured_envelope(kw.pop("env", {}))
        return resolve_envelope(
            explicit,
            hard_halt_inr=kw.pop("hard_halt_inr", 1500.0),
            available_capital=kw.pop("available_capital", 100_000.0),
            open_gross=kw.pop("open_gross", 20_000.0),
            **kw,
        )

    def test_defaults_come_from_the_loss_halt_and_verified_capital(self):
        envelope = self._resolve()
        assert envelope.daily_loss_budget == 1500.0
        # verified available + already deployed = the whole account.
        assert envelope.gross_exposure_limit == 120_000.0
        assert envelope.configured
        assert "hard halt" in envelope.sources["daily_loss_budget"]

    def test_an_explicit_limit_wins_over_the_derived_one(self):
        envelope = self._resolve(env={
            "STERLING_LIVE_MINIMUM_DAILY_LOSS_INR": "800",
            "STERLING_LIVE_MINIMUM_GROSS_EXPOSURE_INR": "30000",
        })
        assert envelope.daily_loss_budget == 800.0
        assert envelope.gross_exposure_limit == 30_000.0

    def test_a_bad_limit_is_never_overwritten_by_a_derived_one(self):
        envelope = self._resolve(env={"STERLING_LIVE_MINIMUM_GROSS_EXPOSURE_INR": "-5"})
        assert envelope.gross_exposure_limit is None
        assert "STERLING_LIVE_MINIMUM_GROSS_EXPOSURE_INR" in envelope.invalid
        assert not envelope.configured

    def test_unknown_capital_leaves_the_exposure_limit_unset_so_the_entry_refuses(self):
        envelope = self._resolve(available_capital=None)
        assert envelope.gross_exposure_limit is None
        assert not envelope.configured

    def test_deployed_pct_scales_the_derived_limit(self):
        envelope = self._resolve(deployed_pct=50.0)
        assert envelope.gross_exposure_limit == 60_000.0


@pytest.mark.kite_features('default')
@pytest.mark.asyncio
async def test_a_repeat_of_an_already_placed_signal_is_not_refused_by_the_envelope(monkeypatch):
    """An idempotent replay resolves to the existing intent in the journal; the
    envelope must not turn that into a refusal."""
    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", True)
    request, approval, account = _entry("replay", quantity=75, price=120.0)
    broker = _LiveBroker(account)
    service = CanonicalExecutionService()

    first = await service.submit_order(request, broker_client=broker, risk_approval=approval)
    assert first.success is True, first.error

    # Now a limit the first order would have broken, applied to the same signal.
    monkeypatch.setenv("STERLING_LIVE_MINIMUM_GROSS_EXPOSURE_INR", "1000")
    again = await service.submit_order(request, broker_client=broker, risk_approval=approval)

    assert again.status != "REJECTED_BY_LIVE_MINIMUM"
    assert len(broker.orders) == 1
