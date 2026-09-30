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

    assert config.enabled is True
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


@pytest.mark.asyncio
async def test_live_emergency_close_requires_fresh_broker_quantity(monkeypatch):
    class Client:
        _is_paper = False
        _account_id = "acct-opening"

        async def get_positions_raw(self):
            return {"net": []}

        async def place_order_option(self, *_args, **_kwargs):
            raise AssertionError("SELL must not be sent without broker inventory proof")

    closed, note = await execution._sell_and_verify(
        Client(), "RELIANCE26SEP3000CE", "NFO", 250
    )

    assert closed is False
    assert "broker quantity 0" in note


@pytest.mark.asyncio
async def test_live_emergency_close_carries_reduce_authority(monkeypatch):
    from app.services.snapback_family_mode import INTENT_REDUCE, current_intent

    class Client:
        _is_paper = False
        _account_id = "acct-opening"

        async def get_positions_raw(self):
            return {
                "net": [
                    {
                        "tradingsymbol": "RELIANCE26SEP3000CE",
                        "exchange": "NFO",
                        "product": "NRML",
                        "quantity": 250,
                    }
                ]
            }

        async def place_order_option(self, _symbol, side, quantity, **_kwargs):
            assert side == "sell"
            assert quantity == 250
            assert _kwargs["exchange"] == "NFO"
            assert _kwargs["product"] == "NRML"
            assert current_intent() == INTENT_REDUCE
            return {"order_id": "EXIT-1"}

    monkeypatch.setattr(
        execution,
        "_resolve_fill",
        lambda *_args, **_kwargs: _async_value((250, 50.0, "COMPLETE")),
    )

    closed, note = await execution._sell_and_verify(
        Client(), "RELIANCE26SEP3000CE", "NFO", 250
    )

    assert closed is True
    assert "closed 250/250" in note


@pytest.mark.asyncio
async def test_live_emergency_close_does_not_cross_product_buckets():
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
        Client(), "RELIANCE26SEP3000CE", "NFO", 250
    )
    assert closed is False
    assert "broker quantity 0" in note


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
async def test_paper_execution_is_sized_idempotent_and_protected(monkeypatch):
    from app.services import live_safety, opening_volume_execution
    from app.services.exchanges.kite import accounts
    from app.services.kite_engine import positions, protection, state as engine_state
    from app.services.kite_engine import service as kite_service

    now = datetime(2026, 9, 3, 9, 30, tzinfo=IST)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz else now.replace(tzinfo=None)

    placed = []

    class Client:
        _is_paper = True

        async def get_ltp(self, keys):
            return {keys[0]: {"last_price": 3000.0}}

        async def place_order_option(self, symbol, side, quantity, **kwargs):
            placed.append((symbol, side, quantity, kwargs))
            return {"order_id": "PAPER-1", "paper": True}

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
    assert placed[0][0:3] == ("RELIANCE26SEP3000CE", "buy", 250)
    assert placed[0][3]["tag"] == "x" * 20
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
