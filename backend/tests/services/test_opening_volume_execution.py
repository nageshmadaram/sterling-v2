from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.opening_volume_execution import (
    OpeningExecutionConfig,
    eligible_candidates,
)
from app.services import opening_volume_execution as execution

IST = timezone(timedelta(hours=5, minutes=30))


def _candidate(**overrides):
    row = {
        "symbol": "RELIANCE",
        "direction": "UP",
        "signal_key": "opening-volume:2026-09-03:RELIANCE:UP",
        "decision": {
            "execution_eligible": True,
            "score": {"lower_bound": 75},
            "conviction": {"passed": 6},
        },
        "option_status": "quoted",
        "option": {
            "tradingsymbol": "RELIANCE26SEP3000CE",
            "option_type": "CE",
            "dte": 20,
            "beginner_expiry_warning": False,
        },
    }
    row.update(overrides)
    return row


def test_execution_config_uses_shared_kite_auto_execute_and_account_mode():
    config = OpeningExecutionConfig().validate()

    assert config.enabled is False
    assert config.max_trades_per_day == 2
    assert config.risk_pct == 1.0
    assert config.min_dte == 2
    assert "allow_live" not in asdict(config)


@pytest.mark.parametrize(
    "candidate",
    [
        _candidate(
            decision={
                "execution_eligible": False,
                "score": {"lower_bound": 100},
                "conviction": {"passed": 7},
            }
        ),
        _candidate(
            decision={
                "execution_eligible": True,
                "score": {"lower_bound": 54},
                "conviction": {"passed": 7},
            }
        ),
        _candidate(
            decision={
                "execution_eligible": True,
                "score": {"lower_bound": 80},
                "conviction": {"passed": 4},
            }
        ),
        _candidate(option_status="historical_quote_unavailable"),
        _candidate(option={"dte": 1, "beginner_expiry_warning": True}),
    ],
)
def test_execution_candidate_gate_rejects_each_missing_requirement(candidate):
    assert eligible_candidates(
        {"leaders": [candidate]},
        OpeningExecutionConfig(enabled=True),
    ) == []


def test_execution_candidate_gate_accepts_only_complete_decisions():
    candidate = _candidate()

    assert eligible_candidates(
        {"leaders": [candidate]},
        OpeningExecutionConfig(enabled=True),
    ) == [candidate]


@pytest.fixture
def opening_reduction_db(tmp_path, monkeypatch):
    from app.services import db, live_safety
    monkeypatch.setattr(db, "_DB_PATH", str(tmp_path / "opening-reduce.sqlite"))
    assert db.init()
    live_safety.reset_all_for_tests()
    db.set_inventory("acct-opening", "synthetic-owner", "RELIANCE26SEP3000CE", 250, exchange="NFO")
    yield
    live_safety.reset_all_for_tests()


@pytest.mark.asyncio
async def test_live_emergency_close_requires_fresh_broker_quantity(monkeypatch, opening_reduction_db):
    class Client:
        _is_paper = False
        _account_id = "acct-opening"

        async def get_positions_raw(self):
            return {"net": []}

        async def place_order_option(self, *_args, **_kwargs):
            raise AssertionError("SELL must not be sent without broker inventory proof")

    closed, note = await execution._sell_and_verify(
        Client(), "synthetic-owner", "synthetic-opening-signal", "RELIANCE26SEP3000CE", "NFO", 250
    )

    assert closed is False
    assert "Broker has no" in note


@pytest.mark.asyncio
async def test_live_emergency_close_carries_reduce_authority(monkeypatch, opening_reduction_db):
    from app.services.snapback_family_mode import INTENT_REDUCE, current_intent

    class Client:
        _is_paper = True  # offline broker double; no production transport
        _account_id = "acct-opening"

        async def place_order(self, symbol, side, size, **kwargs):
            assert symbol == "RELIANCE26SEP3000CE"
            assert side == "SELL"
            assert size == 250
            assert kwargs["exchange"] == "NFO"
            assert kwargs["product"] == "NRML"
            assert current_intent() == INTENT_REDUCE
            return {"order_id": "EXIT-1"}

    monkeypatch.setattr(
        execution,
        "_resolve_fill",
        lambda *_args, **_kwargs: _async_value((250, 50.0, "COMPLETE")),
    )

    closed, note = await execution._sell_and_verify(
        Client(), "synthetic-owner", "synthetic-opening-signal", "RELIANCE26SEP3000CE", "NFO", 250
    )

    assert closed is True
    assert "closed 250/250" in note


@pytest.mark.asyncio
async def test_live_emergency_close_does_not_cross_product_buckets(opening_reduction_db):
    class Client:
        _is_paper = False
        _account_id = "acct-opening"

        async def get_positions_raw(self):
            return {"net": [{
                "tradingsymbol": "RELIANCE26SEP3000CE",
                "exchange": "NFO",
                "product": "MIS",
                "quantity": 250,
            }]}

        async def place_order_option(self, *_args, **_kwargs):
            raise AssertionError("NRML SELL must not be authorized by MIS inventory")

    closed, note = await execution._sell_and_verify(
        Client(), "synthetic-owner", "synthetic-opening-signal", "RELIANCE26SEP3000CE", "NFO", 250
    )
    assert closed is False
    assert "Broker has no" in note


@pytest.mark.asyncio
async def test_partial_entry_cancellation_carries_cancel_authority(monkeypatch):
    from app.services.snapback_family_mode import INTENT_CANCEL, current_intent

    class Client:
        _account_id = "acct-opening"

        async def cancel_order(self, order_id):
            assert order_id == "ENTRY-1"
            assert current_intent() == INTENT_CANCEL
            return {"order_id": order_id}

    monkeypatch.setattr(
        execution,
        "_resolve_fill",
        lambda *_args, **_kwargs: _async_value((100, 50.0, "CANCELLED")),
    )

    filled, _, status, safe = await execution._cancel_and_reconcile(
        Client(), "ENTRY-1", 250
    )

    assert filled == 100
    assert status == "CANCELLED"
    assert safe is True


@pytest.mark.asyncio
@pytest.mark.skip(reason="retired strategy: origination removed from the release scope (Supertrend, Snapback, Gamma Unwind only)")
async def test_execution_refuses_unknown_broker_mode(monkeypatch):
    class SessionClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 3, 9, 30, tzinfo=IST)
    monkeypatch.setattr(execution, "datetime", SessionClock)
    from app.services.exchanges.kite import accounts
    from app.services.kite_engine import state as engine_state

    account = SimpleNamespace(id="acct-a", is_paper=True)
    client = SimpleNamespace(_account_id="acct-a", _is_paper=None)
    monkeypatch.setattr(accounts, "get_active", lambda _uid: account)
    monkeypatch.setattr(accounts, "acquire_client", lambda _acct: _async_value(client))
    monkeypatch.setattr(
        engine_state, "get_config", lambda _uid: SimpleNamespace(auto_execute=True)
    )

    out = await execution.execute_opening_scan(
        "u1", scan={"leaders": []}, config=OpeningExecutionConfig(enabled=True)
    )
    assert out["status"] == "blocked"
    assert "mode" in out["reason"].lower()


@pytest.mark.asyncio
@pytest.mark.skip(reason="retired strategy: origination removed from the release scope (Supertrend, Snapback, Gamma Unwind only)")
async def test_execution_refuses_client_from_different_account(monkeypatch):
    class SessionClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 3, 9, 30, tzinfo=IST)
    monkeypatch.setattr(execution, "datetime", SessionClock)
    from app.services.exchanges.kite import accounts
    from app.services.kite_engine import state as engine_state

    account = SimpleNamespace(id="acct-a", is_paper=True)
    client = SimpleNamespace(_account_id="acct-b", _is_paper=True)
    monkeypatch.setattr(accounts, "get_active", lambda _uid: account)
    monkeypatch.setattr(accounts, "acquire_client", lambda _acct: _async_value(client))
    monkeypatch.setattr(
        engine_state, "get_config", lambda _uid: SimpleNamespace(auto_execute=True)
    )

    out = await execution.execute_opening_scan(
        "u1", scan={"leaders": []}, config=OpeningExecutionConfig(enabled=True)
    )
    assert out["status"] == "blocked"
    assert "identity" in out["reason"].lower() or "changed" in out["reason"].lower()


@pytest.mark.asyncio
async def test_live_opening_execution_requires_strategy_promotion(monkeypatch):
    from app.services.exchanges.kite import accounts
    from app.services.kite_engine import state as engine_state

    now = datetime(2026, 9, 3, 9, 30, tzinfo=IST)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz else now.replace(tzinfo=None)

    account = SimpleNamespace(id="acct-live", is_paper=False)
    client = SimpleNamespace(_account_id="acct-live", _is_paper=False)
    monkeypatch.setattr(execution, "datetime", FrozenDatetime)
    monkeypatch.setattr(accounts, "get_active", lambda _uid: account)
    monkeypatch.setattr(accounts, "acquire_client", lambda _acct: _async_value(client))
    monkeypatch.setattr(
        engine_state,
        "get_config",
        lambda _uid: SimpleNamespace(auto_execute=True),
    )

    out = await execution.execute_opening_scan(
        "u1",
        scan={"leaders": [], "enrichment": {"historical_quotes_omitted": False}},
        config=OpeningExecutionConfig(enabled=True),
    )

    assert out["status"] == "blocked"
    assert out["code"] == "strategy_promotion_required"
    assert "no approved live-validation" in out["reason"]


@pytest.mark.asyncio
async def test_paper_execution_is_sized_idempotent_and_protected(monkeypatch):
    from app.services import (
        canonical_fill_reconciliation,
        canonical_strategy_entry,
        live_safety,
        opening_volume_execution,
    )
    from app.services.exchanges.kite import accounts
    from app.services.kite_engine import positions, protection, state as engine_state
    from app.services.kite_engine import service as kite_service

    now = datetime(2026, 9, 3, 9, 30, tzinfo=IST)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz else now.replace(tzinfo=None)

    canonical_calls = []

    class Client:
        _is_paper = True
        _account_id = "KITE-PAPER-1"

        async def get_ltp(self, keys):
            return {keys[0]: {"last_price": 3000.0}}

    client = Client()
    account = SimpleNamespace(id="KITE-PAPER-1", is_paper=True)
    monkeypatch.setattr(execution, "datetime", FrozenDatetime)
    monkeypatch.setattr(
        execution,
        "_trade_state",
        lambda *_: {"date": "2026-09-03", "count": 0, "signals": []},
    )
    saved = []
    monkeypatch.setattr(
        execution,
        "_save_trade_state",
        lambda _uid, state: saved.append(dict(state)),
    )
    monkeypatch.setattr(accounts, "get_active", lambda _uid: account)

    async def acquire(_account):
        return client

    monkeypatch.setattr(accounts, "acquire_client", acquire)
    monkeypatch.setattr(
        engine_state,
        "get_config",
        lambda _uid: SimpleNamespace(
            auto_execute=True, stop_mode="monitor", exit_mode="one_red"
        ),
    )
    monkeypatch.setattr(positions, "open_positions", lambda _uid: [])
    idempotency_parts = []
    safety_kwargs = {}

    def make_idempotency(*parts):
        idempotency_parts.append(parts)
        return "x" * 32

    def safe_to_trade(*_args, **kwargs):
        safety_kwargs.update(kwargs)
        return SimpleNamespace(allowed=True, reason="", code="ok")

    monkeypatch.setattr(live_safety, "make_idempotency_key", make_idempotency)
    monkeypatch.setattr(live_safety, "assert_safe_to_trade", safe_to_trade)
    monkeypatch.setattr(live_safety, "record_idempotency", lambda *_: None)
    monkeypatch.setattr(
        kite_service,
        "available_fo_capital",
        lambda _client: _async_value(500_000.0),
    )
    monkeypatch.setattr(
        opening_volume_execution,
        "_find_contract",
        lambda *_: _async_value(
            (
                "NFO",
                {
                    "instrument_type": "CE",
                    "strike": 3000,
                    "expiry": "2026-09-24",
                    "lot_size": 250,
                    "instrument_token": 123,
                },
            )
        ),
    )
    monkeypatch.setattr(
        opening_volume_execution,
        "_fresh_quote",
        lambda *_: _async_value(
            {
                "bid": 49.5,
                "ask": 50.0,
                "ltp": 50.0,
                "spread_pct": 1.0,
                "age_s": 1.0,
                "volume": 1000,
                "oi": 1000,
            }
        ),
    )
    monkeypatch.setattr(
        opening_volume_execution,
        "_existing_order_by_tag",
        lambda *_: _async_value((True, None)),
    )

    async def canonical_submit(**kwargs):
        canonical_calls.append(kwargs)
        return SimpleNamespace(
            success=True,
            status="ACKNOWLEDGED",
            order_id="PAPER-1",
            error="",
        )

    async def resolve_fill(**kwargs):
        expected = int(kwargs["expected_quantity"])
        return canonical_fill_reconciliation.FillResolution(
            order_id="PAPER-1",
            intent_key="test-intent",
            status="COMPLETE",
            filled_quantity=expected,
            average_price=float(kwargs["reference_price"]),
            terminal=True,
            settled=True,
            reconciliation_required=False,
            projection_version=0,
            expected_quantity=expected,
        )

    monkeypatch.setattr(canonical_strategy_entry, "submit_entry", canonical_submit)
    monkeypatch.setattr(canonical_fill_reconciliation, "resolve_entry_fill", resolve_fill)

    def no_paper_poll(*_args):
        raise AssertionError("paper execution must not poll a fake broker order")

    monkeypatch.setattr(opening_volume_execution, "_resolve_fill", no_paper_poll)
    monkeypatch.setattr(
        protection,
        "arm_position",
        lambda *_args, **_kwargs: _async_value(
            SimpleNamespace(protected=True, describe=lambda: "protected")
        ),
    )

    row = _candidate(
        observed_at=now.isoformat(),
        live_price=3000.0,
        current_price=3000.0,
        orb_break_level=2995.0,
        playbook={"recommended_risk_pct": 1.0},
        option={
            "tradingsymbol": "RELIANCE26SEP3000CE",
            "option_type": "CE",
            "strike": 3000,
            "expiry": "2026-09-24",
            "lot_size": 250,
            "dte": 21,
            "beginner_expiry_warning": False,
        },
    )
    result = await execution.execute_opening_scan(
        "tenant-a",
        scan={"leaders": [row], "enrichment": {"historical_quotes_omitted": False}},
        config=OpeningExecutionConfig(enabled=True),
    )

    assert result["status"] == "executed"
    assert result["executed"][0]["protected"] is True
    assert len(canonical_calls) == 1
    assert canonical_calls[0]["strategy_id"] == "opening_volume_leaders"
    assert canonical_calls[0]["generation_id"] == "1.4.0"
    assert canonical_calls[0]["signal_id"] == "opening-volume:2026-09-03:RELIANCE:UP"
    assert canonical_calls[0]["symbol"] == "RELIANCE26SEP3000CE"
    assert canonical_calls[0]["quantity"] == 250
    assert idempotency_parts == [
        (
            "tenant-a",
            "KITE-PAPER-1",
            "paper",
            "opening-volume:2026-09-03:RELIANCE:UP",
            "BUY",
        )
    ]
    assert safety_kwargs["account_id"] == "KITE-PAPER-1"
    assert saved[0]["count"] == 1


async def _async_value(value):
    return value
