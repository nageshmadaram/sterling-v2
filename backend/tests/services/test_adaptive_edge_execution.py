"""Adaptive Edge execution: entry, protection, exit, reconcile.

Every test here guards a path that moves money or decides whether money is
protected. The recurring shape is "the wrong behaviour is silent": a position
recorded without an order, a stop that exists only on screen, an exit that sells
twice, a reconcile that abandons real positions on a transient API failure.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.engines.adaptive_edge import AdaptiveEdgeConfig
from app.engines.adaptive_edge.state_machine import StrategyState
from app.services import adaptive_edge_positions as positions
from app.services import adaptive_edge_runner as runner


@pytest.fixture(autouse=True)
def store(monkeypatch):
    """In-memory config store, and a clean position cache per test."""
    data: dict[str, str] = {}
    import app.services.db as db
    get = lambda key, default="": data.get(key, default)
    set_ = lambda key, value: data.__setitem__(key, value)
    monkeypatch.setattr(db, "get_config", get)
    monkeypatch.setattr(db, "set_config", set_)
    monkeypatch.setattr(db, "get_config_strict", get)
    monkeypatch.setattr(db, "set_config_strict", set_)
    positions.reset()
    runner.clear()
    yield data
    positions.reset()
    runner.clear()


@pytest.fixture(autouse=True)
def allow_everything(monkeypatch):
    """Paper account, safety open, evidence granted — so each test isolates the
    behaviour it is actually about rather than re-testing the gates.

    The evidence gate is stubbed here on purpose. It genuinely blocks arming
    until the engine has earned the right to trade from its own live readings,
    which is correct and is covered in test_adaptive_edge_evidence.py. These
    tests are about what happens once that permission exists.
    """
    monkeypatch.setattr(runner, "is_paper", lambda uid: True)
    monkeypatch.setattr(runner, "_safety", lambda uid, key: (True, ""))
    monkeypatch.setattr(runner, "_is_market_open", lambda cfg: True)
    monkeypatch.setattr(runner, "evidence_permits_arming",
                        lambda uid: (True, "granted for this test"))


class FakeClient:
    """Records every call, so ordering assertions are possible."""

    def __init__(self, *, order_id: str = "OID-1", raise_on_place: bool = False,
                 status: str = "COMPLETE", average: float = 0.0,
                 broker_positions: Any = None, historical: Any = None):
        self._is_paper = True
        self._account_id = "acct-adaptive"
        self.calls: list[tuple[str, Any]] = []
        self._order_id = order_id
        self._raise = raise_on_place
        self._status = status
        self._average = average
        self._broker_positions = broker_positions if broker_positions is not None else {"net": []}
        self._historical = historical or {}
        self.gtt_moves: list[dict[str, Any]] = []

    async def modify_gtt(self, trigger_id, **kwargs):
        self.gtt_moves.append({"trigger_id": trigger_id, **kwargs})
        return {"trigger_id": trigger_id}

    async def place_order(self, symbol, side, qty, **kwargs):
        self.calls.append((f"place:{side}", symbol))
        if self._raise:
            raise RuntimeError("broker refused")
        return {"order_id": self._order_id} if self._order_id else {}

    async def get_order_history(self, order_id):
        return [{"status": self._status, "average_price": self._average}]

    async def get_positions(self):
        return self._broker_positions

    async def get_historical(self, token, interval, start, end):
        return self._historical.get(int(token), {"candles": []})

    async def quote(self, keys):
        return {k: {"last_price": 100.0} for k in keys}


def _signal(**over):
    row = {"signal_id": "SIG-1", "symbol": "NIFTY26090325000CE", "token": 111,
           "underlying": "NIFTY", "option_type": "CE", "lot_size": 50,
           "last_price": 120.0, "expiry": "2026-09-03",
           "entry_ok": True, "reason": "eligible"}
    row.update(over)
    return row


def _prime_scan(uid="u1", **over):
    runner._scan_states[uid] = {"candidates": [], "signals": [_signal(**over)]}


def _arm(uid="u1", client=None, monkeypatch=None, cfg=None):
    from app.services import canonical_fill_reconciliation, canonical_strategy_entry
    from app.services.execution_service import ExecutionResult

    client = client or FakeClient()
    client.canonical_calls = []
    monkeypatch.setattr(runner, "_client", lambda u: _async(client))
    if cfg is not None:
        monkeypatch.setattr(runner, "get_config", lambda _uid=None: cfg)
    monkeypatch.setattr(runner, "_place_protection",
                        lambda u, c, p, k: _async(4242))

    async def canonical_submit(**kwargs):
        client.canonical_calls.append(kwargs)
        if client._raise:
            return ExecutionResult(
                success=False,
                status="REJECTED",
                error="broker refused",
            )
        return ExecutionResult(
            success=bool(client._order_id),
            status="ACKNOWLEDGED" if client._order_id else "UNKNOWN",
            order_id=str(client._order_id or ""),
            error="" if client._order_id else "submission outcome unknown",
        )

    async def resolve_fill(**kwargs):
        if hasattr(client, "before_fill_resolution"):
            client.before_fill_resolution()
        status = str(client._status or "UNKNOWN").upper()
        expected = int(kwargs["expected_quantity"])
        filled = expected if status == "COMPLETE" else 0
        terminal = status in {"COMPLETE", "REJECTED", "CANCELLED"}
        return canonical_fill_reconciliation.FillResolution(
            order_id=str(client._order_id or ""),
            intent_key="test-intent",
            status=status,
            filled_quantity=filled,
            average_price=(
                float(client._average or kwargs["reference_price"])
                if filled else 0.0
            ),
            terminal=terminal,
            settled=terminal,
            reconciliation_required=False,
            projection_version=0,
            expected_quantity=expected,
        )

    monkeypatch.setattr(canonical_strategy_entry, "submit_entry", canonical_submit)
    monkeypatch.setattr(canonical_fill_reconciliation, "resolve_entry_fill", resolve_fill)
    return asyncio.run(runner.arm(uid, "SIG-1")), client


async def _await(value):
    return value


def _async(value):
    async def inner(*args, **kwargs):
        return value
    return inner()


# ---------------------------------------------------------- auto arming truth

@pytest.mark.parametrize(
    ("master", "strategy", "expected"),
    [
        (False, False, False),
        (False, True, False),
        (True, False, False),
        (True, True, True),
    ],
)
def test_auto_execute_requires_master_and_strategy_switch(
    monkeypatch, master, strategy, expected
):
    from types import SimpleNamespace
    from app.services.kite_engine import state as engine_state

    monkeypatch.setattr(
        engine_state, "get_config", lambda _uid: SimpleNamespace(auto_execute=master)
    )
    monkeypatch.setattr(
        runner,
        "get_config",
        lambda _uid=None: AdaptiveEdgeConfig(auto_execute=strategy).validate(),
    )
    assert runner.auto_execute("u1") is expected


def test_auto_execute_fails_closed_when_strategy_config_is_unavailable(monkeypatch):
    from types import SimpleNamespace
    from app.services.kite_engine import state as engine_state

    monkeypatch.setattr(
        engine_state, "get_config", lambda _uid: SimpleNamespace(auto_execute=True)
    )
    monkeypatch.setattr(
        runner, "get_config", lambda _uid=None: (_ for _ in ()).throw(RuntimeError("db down"))
    )
    assert runner.auto_execute("u1") is False


def test_calibration_resolver_settles_option_and_volatility_rows(store):
    from app.services import adaptive_edge_evidence as evidence
    from app.services import adaptive_edge_observations as observations

    observations.record(
        "u1",
        "2026-08-27",
        [{
            "underlying": "NIFTY",
            "symbol": "NIFTYCE",
            "token": 111,
            "option_type": "CE",
            "strike": 25000.0,
            "dte": 7,
            "spot": 25000.0,
            "last_price": 100.0,
            "oi": 60000.0,
            "volume": 5000.0,
            "bid": 99.0,
            "ask": 101.0,
        }],
        observed_ms=1000,
        horizon_bars=1,
        interval="minute",
    )
    evidence.record("u1", evidence.PendingReading(
        session="2026-08-27",
        decided_ms=1000,
        underlying="NIFTY",
        strike=25000.0,
        implied_ratio=1.2,
        implied_vol=0.2,
        realised_vol=0.15,
        credit_bps=10.0,
        max_loss_bps=30.0,
        forecast_bps=20.0,
        spot=25000.0,
        spot_token=222,
        horizon_bars=1,
        interval="minute",
    ))
    candles = {
        111: {"candles": [
            ["2026-08-27T09:22:00+05:30", 100, 125, 90, 120, 1000],
        ]},
        222: {"candles": [
            ["2026-08-27T09:22:00+05:30", 25000, 25100, 24900, 25050, 1000],
        ]},
    }
    client = FakeClient(historical=candles)
    result = asyncio.run(runner._resolve_pending_calibration(
        "u1", client, AdaptiveEdgeConfig(horizon_bars=1).validate(),
    ))

    assert result == {"option_observations": 1, "volatility_readings": 1}
    option = observations.load("u1", "2026-08-27")[0]
    assert option["forward_return_pct"] == pytest.approx(20.0)
    assert option["max_favourable_pct"] == pytest.approx(25.0)
    assert option["max_adverse_pct"] == pytest.approx(-10.0)
    assert evidence.pending("u1", "2026-08-27") == []


def test_resolution_ignores_the_candle_containing_the_decision():
    observed = int((
        __import__("datetime").datetime.fromisoformat("2026-08-27T09:21:30+05:30")
    ).timestamp() * 1000)
    client = FakeClient(historical={111: {"candles": [
        ["2026-08-27T09:21:00+05:30", 100, 999, 1, 100, 1000],
        ["2026-08-27T09:22:00+05:30", 100, 110, 95, 105, 1000],
    ]}})

    rows = asyncio.run(runner._resolution_candles(
        client,
        token=111,
        interval="minute",
        observed_ms=observed,
        horizon_bars=1,
    ))

    assert len(rows) == 1
    assert rows[0][2] == 110


def test_resolution_requires_the_full_trading_bar_horizon():
    observed = int((
        __import__("datetime").datetime.fromisoformat("2026-08-27T09:21:30+05:30")
    ).timestamp() * 1000)
    client = FakeClient(historical={111: {"candles": [
        ["2026-08-27T09:22:00+05:30", 100, 110, 95, 105, 1000],
    ]}})

    rows = asyncio.run(runner._resolution_candles(
        client,
        token=111,
        interval="minute",
        observed_ms=observed,
        horizon_bars=2,
    ))

    assert rows == []


def test_resolution_excludes_a_still_forming_candle_after_a_market_gap(monkeypatch):
    """A bar timestamp is its start; its OHLC is not evidence until it closes."""
    dt = __import__("datetime").datetime
    observed = int(dt.fromisoformat("2026-08-28T15:29:30+05:30").timestamp() * 1000)
    now_ms = int(dt.fromisoformat("2026-08-31T09:16:30+05:30").timestamp() * 1000)
    monkeypatch.setattr(runner, "ist_now_ms", lambda: now_ms)
    client = FakeClient(historical={111: {"candles": [
        ["2026-08-31T09:15:00+05:30", 100, 110, 95, 105, 1000],
        ["2026-08-31T09:16:00+05:30", 105, 999, 1, 500, 1000],
    ]}})

    rows = asyncio.run(runner._resolution_candles(
        client,
        token=111,
        interval="minute",
        observed_ms=observed,
        horizon_bars=2,
    ))

    assert rows == []


def test_calibration_resolution_rotates_past_irretrievable_rows(store):
    """One expired/missing contract cannot consume every future request batch."""
    from app.services import adaptive_edge_observations as observations

    dt = __import__("datetime").datetime
    observed = int(dt.fromisoformat("2026-08-27T09:21:30+05:30").timestamp() * 1000)
    observations.record(
        "u1",
        "2026-08-27",
        [
            {
                "underlying": "NIFTY", "symbol": "BADCE", "token": 111,
                "option_type": "CE", "strike": 25000.0, "dte": 1,
                "spot": 25000.0, "last_price": 100.0, "oi": 60000.0,
                "volume": 5000.0, "bid": 99.0, "ask": 101.0,
            },
            {
                "underlying": "NIFTY", "symbol": "GOODCE", "token": 222,
                "option_type": "CE", "strike": 25100.0, "dte": 7,
                "spot": 25000.0, "last_price": 100.0, "oi": 60000.0,
                "volume": 5000.0, "bid": 99.0, "ask": 101.0,
            },
        ],
        observed_ms=observed,
        horizon_bars=1,
        interval="minute",
    )
    client = FakeClient(historical={
        111: {"candles": []},
        222: {"candles": [
            ["2026-08-27T09:22:00+05:30", 100, 125, 90, 120, 1000],
        ]},
    })
    cfg = AdaptiveEdgeConfig(horizon_bars=1).validate()

    first = asyncio.run(runner._resolve_pending_calibration(
        "u1", client, cfg, max_requests=1,
    ))
    assert first["option_observations"] == 0
    assert store["adaptive_edge_option_resolution_cursor:u1"] == "1"

    second = asyncio.run(runner._resolve_pending_calibration(
        "u1", client, cfg, max_requests=1,
    ))
    assert second["option_observations"] == 1
    rows = observations.load("u1", "2026-08-27")
    good = next(row for row in rows if row["symbol"] == "GOODCE")
    assert good["forward_return_pct"] == pytest.approx(20.0)


def test_first_threshold_hit_uses_order_and_excludes_same_bar_ambiguity():
    target_first = [
        ["2026-08-27T09:22:00+05:30", 100, 205, 90, 190, 1000],
        ["2026-08-27T09:23:00+05:30", 190, 195, 65, 70, 1000],
    ]
    stop_first = [
        ["2026-08-27T09:22:00+05:30", 100, 110, 65, 70, 1000],
        ["2026-08-27T09:23:00+05:30", 70, 205, 68, 200, 1000],
    ]
    same_bar = [
        ["2026-08-27T09:22:00+05:30", 100, 205, 65, 100, 1000],
    ]

    assert runner._first_threshold_hit(
        target_first, entry_premium=100, target_multiple=2.0, stop_percent=30
    ) == "target"
    assert runner._first_threshold_hit(
        stop_first, entry_premium=100, target_multiple=2.0, stop_percent=30
    ) == "stop"
    assert runner._first_threshold_hit(
        same_bar, entry_premium=100, target_multiple=2.0, stop_percent=30
    ) == "ambiguous"


# ------------------------------------------------------------------ entry

def test_arm_is_refused_when_engine_is_off(monkeypatch):
    _prime_scan()
    cfg = AdaptiveEdgeConfig(enabled=False).validate()
    result, client = _arm(monkeypatch=monkeypatch, cfg=cfg)

    assert result["ok"] is False
    assert "switched off" in result["reason"]
    assert client.canonical_calls == []


def test_arm_refuses_signal_that_failed_f110(monkeypatch):
    """A direct/manual arm call cannot bypass the candidate entry gate."""
    _prime_scan(entry_ok=False, reason="conservative expected value not positive")
    result, client = _arm(monkeypatch=monkeypatch)

    assert result["ok"] is False
    assert "conservative expected value" in result["reason"]
    assert client.canonical_calls == []


def test_arm_actually_places_an_order(monkeypatch):
    """Canonical identity is session-scoped while the board identity stays stable."""
    monkeypatch.setattr(runner, "ist_today", lambda: "2026-10-03")
    _prime_scan()
    result, client = _arm(monkeypatch=monkeypatch)
    assert result["ok"] is True
    assert len(client.canonical_calls) == 1
    call = client.canonical_calls[0]
    assert call["strategy_id"] == "adaptive_edge"
    assert call["signal_id"] == "SIG-1:2026-10-03"
    assert call["exchange"] == "NFO"
    assert call["symbol"] == "NIFTY26090325000CE"
    assert call["quantity"] == 50
    assert positions.get("u1", "NIFTY26090325000CE").signal_id == "SIG-1"


def test_unknown_broker_mode_blocks_before_entry(monkeypatch):
    _prime_scan()
    client = FakeClient()
    client._is_paper = None

    result, client = _arm(client=client, monkeypatch=monkeypatch)

    assert result["ok"] is False
    assert "broker mode" in result["reason"].lower()
    assert client.canonical_calls == []


def test_live_entry_stays_blocked_while_strategy_is_unpromoted(monkeypatch):
    """Canonical transport does not bypass Adaptive Edge's promotion authority."""
    from app.core import lane_registry

    _prime_scan()
    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", True)
    monkeypatch.setattr(runner, "is_paper", lambda uid: False)

    client = FakeClient()
    client._is_paper = False

    result, client = _arm(client=client, monkeypatch=monkeypatch)

    assert result["ok"] is False
    assert result["reason"] == "strategy_promotion_required"
    assert client.canonical_calls == []


def test_arm_records_the_position_before_confirming_the_fill(monkeypatch):
    """If the process dies between send and confirm, the position that may exist
    at the broker must be one we can find again."""
    _prime_scan()
    seen: dict[str, Any] = {}
    client = FakeClient()

    def before_fill_resolution():
        pending = positions.get("u1", "NIFTY26090325000CE")
        seen["persisted"] = pending is not None and pending.order_id == client._order_id

    client.before_fill_resolution = before_fill_resolution
    _arm(client=client, monkeypatch=monkeypatch)
    assert seen["persisted"] is True


def test_arm_reanchors_the_stop_to_the_actual_fill(monkeypatch):
    """Sizing the stop off the limit price is how it ends up against a price
    nobody traded at."""
    _prime_scan()
    client = FakeClient(status="COMPLETE", average=100.0)
    result, _ = _arm(client=client, monkeypatch=monkeypatch)
    # 30% default stop off the 100.0 fill, not off the 120.0 limit.
    assert result["entry"] == pytest.approx(100.0)
    assert result["stop"] == pytest.approx(70.0)


def test_arm_refuses_when_the_broker_rejects(monkeypatch):
    _prime_scan()
    client = FakeClient(status="REJECTED")
    result, _ = _arm(client=client, monkeypatch=monkeypatch)
    assert result["ok"] is False and "rejected" in result["reason"]
    assert positions.get("u1", "NIFTY26090325000CE").state == StrategyState.REJECTED.value


def test_arm_refuses_when_the_broker_returns_no_order_id(monkeypatch):
    """No id and no exception is the dangerous case: an order may exist."""
    _prime_scan()
    client = FakeClient(order_id="")
    result, _ = _arm(client=client, monkeypatch=monkeypatch)
    assert result["ok"] is False
    assert "submission outcome unknown" in result["reason"]


def test_arm_refuses_when_placing_raises(monkeypatch):
    _prime_scan()
    client = FakeClient(raise_on_place=True)
    result, _ = _arm(client=client, monkeypatch=monkeypatch)
    assert result["ok"] is False and "broker refused" in result["reason"]


def test_arm_will_not_double_up_on_a_contract_already_held(monkeypatch):
    _prime_scan()
    _arm(monkeypatch=monkeypatch)
    result, _ = _arm(monkeypatch=monkeypatch)
    assert result["ok"] is False and "already holding" in result["reason"]


def test_arm_respects_the_daily_loss_cap(monkeypatch):
    """The cap is denominated in rupees against this engine's own closed
    positions, not the shared USD breaker that reads zero for an INR book."""
    _prime_scan()
    cfg = AdaptiveEdgeConfig(max_daily_loss=1000.0).validate()
    closed = positions.AdaptiveEdgePosition(
        symbol="OLD", token=1, underlying="NIFTY", direction="CE", quantity=50,
        lot_size=50, entry_price=100.0, stop_price=70.0, target_price=None,
        state=StrategyState.CLOSED.value, exit_price=60.0)
    positions.load("u1")["OLD"] = closed
    positions.persist("u1")

    result, _ = _arm(monkeypatch=monkeypatch, cfg=cfg)
    assert result["ok"] is False
    assert "daily loss cap" in result["reason"]


# ------------------------------------------------------------------- exit

def _open_position(uid="u1", **over):
    base = dict(symbol="SYM", token=7, underlying="NIFTY", direction="CE", quantity=50,
                lot_size=50, entry_price=100.0, stop_price=70.0, target_price=200.0,
                state=StrategyState.OPEN.value, peak_price=100.0, gtt_id=99,
                gtt_at=70.0, gtt_qty=50, account_id="acct-adaptive")
    base.update(over)
    pos = positions.AdaptiveEdgePosition(**base)
    positions.put(uid, pos)
    return pos


def test_exit_cancels_the_broker_stop_before_selling(monkeypatch):
    """Selling while a GTT is armed is how one position gets sold twice."""
    order: list[str] = []
    pos = _open_position()
    client = FakeClient()

    async def cancel(uid, c, p):
        order.append("cancel")
        p.gtt_id = 0
        return True

    monkeypatch.setattr(runner, "_cancel_protection", cancel)
    original = client.place_order

    async def place(*a, **k):
        order.append("sell")
        return await original(*a, **k)

    client.place_order = place
    ok = asyncio.run(runner._exit_position("u1", client, pos,
                                           AdaptiveEdgeConfig().validate(),
                                           price=80.0, reason="stop"))
    assert ok is True
    assert order == ["cancel", "sell"]


def test_a_failed_exit_re_arms_the_protection_it_cancelled(monkeypatch):
    """Leaving it down turns a failed exit into an unprotected position."""
    pos = _open_position()
    client = FakeClient(raise_on_place=True)
    replaced: list[str] = []
    monkeypatch.setattr(runner, "_cancel_protection", lambda u, c, p: _async(True))
    monkeypatch.setattr(runner, "_place_protection",
                        lambda u, c, p, k: (replaced.append("re-armed"), _async(1))[1])
    ok = asyncio.run(runner._exit_position("u1", client, pos,
                                           AdaptiveEdgeConfig().validate(),
                                           price=80.0, reason="stop"))
    assert ok is False
    assert replaced == ["re-armed"]
    assert pos.exiting is False


def test_a_second_exit_caller_is_refused_while_one_is_in_flight(monkeypatch):
    """The tick monitor and the square-off must not both sell the same position."""
    pos = _open_position(exiting=True)
    client = FakeClient()
    ok = asyncio.run(runner._exit_position("u1", client, pos,
                                           AdaptiveEdgeConfig().validate(),
                                           price=80.0, reason="stop"))
    assert ok is False
    assert not any(call[0].startswith("place") for call in client.calls)


def test_the_exit_reason_is_recorded_as_given_not_inferred(monkeypatch):
    """A stop and a square-off can happen at the same price; recording the wrong
    one makes the exit ledger useless for calibration."""
    pos = _open_position()
    client = FakeClient()
    monkeypatch.setattr(runner, "_cancel_protection", lambda u, c, p: _async(True))
    asyncio.run(runner._exit_position("u1", client, pos, AdaptiveEdgeConfig().validate(),
                                      price=70.0, reason="square_off"))
    assert positions.get("u1", "SYM").exit_reason == "square_off"


def test_live_exit_refuses_when_broker_holds_less_than_local_quantity(monkeypatch):
    pos = _open_position(quantity=50)
    client = FakeClient(broker_positions={"net": []})
    client._is_paper = False

    ok = asyncio.run(runner._exit_position(
        "u1", client, pos, AdaptiveEdgeConfig().validate(),
        price=80.0, reason="stop",
    ))

    assert ok is False
    assert positions.get("u1", "SYM").is_open
    assert positions.get("u1", "SYM").gtt_id == 99
    assert not any(kind == "place:sell" for kind, _ in client.calls)


def test_active_gtt_blocks_adaptive_edge_second_sell(monkeypatch):
    from app.services.kite_engine import protective_stop as pstop

    pos = _open_position()
    client = FakeClient()

    async def gone(*_args, **_kwargs):
        return pstop.GONE

    async def active(*_args, **_kwargs):
        return pstop.STOP_ACTIVE

    monkeypatch.setattr(pstop, "cancel_stop_result", gone)
    monkeypatch.setattr(pstop, "stop_status", active)

    ok = asyncio.run(runner._exit_position(
        "u1", client, pos, AdaptiveEdgeConfig().validate(),
        price=80.0, reason="stop",
    ))

    assert ok is False
    assert positions.get("u1", "SYM").gtt_id == 99
    assert not any(kind == "place:sell" for kind, _ in client.calls)


def test_proven_absent_gtt_allows_adaptive_edge_exit(monkeypatch):
    from app.services.kite_engine import protective_stop as pstop

    pos = _open_position()
    client = FakeClient()

    async def gone(*_args, **_kwargs):
        return pstop.GONE

    async def absent(*_args, **_kwargs):
        return pstop.STOP_ABSENT

    monkeypatch.setattr(pstop, "cancel_stop_result", gone)
    monkeypatch.setattr(pstop, "stop_status", absent)

    ok = asyncio.run(runner._exit_position(
        "u1", client, pos, AdaptiveEdgeConfig().validate(),
        price=80.0, reason="stop",
    ))

    assert ok is True
    assert positions.get("u1", "SYM").is_open is False
    assert any(kind == "place:sell" for kind, _ in client.calls)


# -------------------------------------------------------------- tick loop

def _ticks(token=7, price=100.0):
    return [{"instrument_token": token, "last_price": price}]


def test_ticks_exit_on_the_stop(monkeypatch):
    _open_position()
    client = FakeClient()
    monkeypatch.setattr(runner, "_client", lambda u: _async(client))
    monkeypatch.setattr(runner, "_cancel_protection", lambda u, c, p: _async(True))
    assert asyncio.run(runner.on_ticks("u1", _ticks(price=69.0))) == "exited"
    assert positions.get("u1", "SYM").exit_reason == "stop"


def test_ticks_exit_on_the_target(monkeypatch):
    _open_position()
    client = FakeClient()
    monkeypatch.setattr(runner, "_client", lambda u: _async(client))
    monkeypatch.setattr(runner, "_cancel_protection", lambda u, c, p: _async(True))
    asyncio.run(runner.on_ticks("u1", _ticks(price=210.0)))
    assert positions.get("u1", "SYM").exit_reason == "target"


def test_ticks_flatten_after_the_session_window(monkeypatch):
    _open_position()
    client = FakeClient()
    monkeypatch.setattr(runner, "_client", lambda u: _async(client))
    monkeypatch.setattr(runner, "_is_market_open", lambda cfg: False)
    monkeypatch.setattr(runner, "_cancel_protection", lambda u, c, p: _async(True))
    asyncio.run(runner.on_ticks("u1", _ticks(price=120.0)))
    assert positions.get("u1", "SYM").exit_reason == "session_end"


def test_the_trail_ratchets_up_and_never_widens(monkeypatch):
    """Widening a stop is an expansion of risk the position never authorized."""
    _open_position(stop_price=70.0)
    client = FakeClient()
    monkeypatch.setattr(runner, "_client", lambda u: _async(client))
    monkeypatch.setattr(runner, "_sync_trail", lambda u, c, p, k: _async(None))

    asyncio.run(runner.on_ticks("u1", _ticks(price=180.0)))
    raised = positions.get("u1", "SYM").stop_price
    assert raised > 70.0

    asyncio.run(runner.on_ticks("u1", _ticks(price=150.0)))
    assert positions.get("u1", "SYM").stop_price == raised


def test_no_open_position_is_idle(monkeypatch):
    assert asyncio.run(runner.on_ticks("u1", _ticks())) == "idle"


# -------------------------------------------------------------- reconcile

def test_reconcile_closes_a_position_the_broker_no_longer_holds(monkeypatch):
    """Closed behind our back — by a GTT, by hand, or by the exchange. Leaving it
    open blocks the next entry and reports a P&L that is not real."""
    _open_position()
    client = FakeClient(broker_positions={"net": []})
    monkeypatch.setattr(runner, "_client", lambda u: _async(client))
    out = asyncio.run(runner.reconcile("u1"))
    assert out["closed"] == 1
    assert positions.get("u1", "SYM").is_open is False


def test_reconcile_keeps_a_position_the_broker_still_holds(monkeypatch):
    _open_position()
    client = FakeClient(broker_positions={"net": [{"tradingsymbol": "SYM", "exchange": "NFO", "product": "MIS", "quantity": 50}]})
    monkeypatch.setattr(runner, "_client", lambda u: _async(client))
    out = asyncio.run(runner.reconcile("u1"))
    assert out["closed"] == 0
    assert positions.get("u1", "SYM").is_open is True


def test_live_foreign_account_position_is_not_mutated_or_closed(monkeypatch):
    p = _open_position(account_id="other-account", gtt_id=0)
    client = FakeClient(broker_positions={"net": [{
        "tradingsymbol": "SYM",
        "exchange": "NFO",
        "product": "MIS",
        "quantity": 50,
    }]})
    client._is_paper = False
    monkeypatch.setattr(runner, "_client", lambda u: _async(client))

    assert asyncio.run(runner._broker_long_quantity(client, p)) is None
    out = asyncio.run(runner.reconcile("u1"))

    assert p.is_open
    assert out["closed"] == 0
    assert out["reprotected"] == 0
    assert any("ownership unverified" in e for e in out["errors"])


def test_reconcile_resizes_existing_gtt_to_confirmed_quantity(monkeypatch):
    pos = _open_position(quantity=50, gtt_id=99, gtt_at=70.0, gtt_qty=20)
    client = FakeClient(broker_positions={"net": [{
        "tradingsymbol": pos.symbol,
        "exchange": pos.exchange,
        "product": "MIS",
        "quantity": 50,
    }]})
    monkeypatch.setattr(runner, "_client", lambda _uid: _async(client))

    result = asyncio.run(runner.reconcile("u1"))

    current = positions.get("u1", pos.symbol)
    assert result["reprotected"] == 1
    assert current.gtt_id == 99
    assert current.gtt_qty == 50
    assert client.gtt_moves[-1]["orders"][0]["quantity"] == 50


def test_reconcile_does_not_accept_same_symbol_nrml_as_the_mis_position(monkeypatch):
    _open_position()
    client = FakeClient(broker_positions={"net": [{
        "tradingsymbol": "SYM",
        "exchange": "NFO",
        "product": "NRML",
        "quantity": 50,
    }]})
    monkeypatch.setattr(runner, "_client", lambda u: _async(client))
    out = asyncio.run(runner.reconcile("u1"))
    assert out["closed"] == 1
    assert positions.get("u1", "SYM").is_open is False


def test_reconcile_does_not_abandon_positions_when_the_broker_is_unreachable(monkeypatch):
    """Unknown broker state is not an empty one. Closing here would abandon real
    positions on a transient API failure."""
    _open_position()

    class Broken(FakeClient):
        async def get_positions(self):
            raise RuntimeError("api down")

    monkeypatch.setattr(runner, "_client", lambda u: _async(Broken()))
    out = asyncio.run(runner.reconcile("u1"))
    assert out["closed"] == 0
    assert out["errors"]
    assert positions.get("u1", "SYM").is_open is True


def test_reconcile_re_protects_a_position_that_lost_its_broker_stop(monkeypatch):
    """The whole point of running reconcile on a restart."""
    _open_position(gtt_id=0)
    client = FakeClient(broker_positions={"net": [{"tradingsymbol": "SYM", "exchange": "NFO", "product": "MIS", "quantity": 50}]})
    monkeypatch.setattr(runner, "_client", lambda u: _async(client))
    monkeypatch.setattr(runner, "_place_protection", lambda u, c, p, k: _async(555))
    out = asyncio.run(runner.reconcile("u1"))
    assert out["reprotected"] == 1


# ------------------------------------------------------------ square off

def test_square_off_flattens_everything(monkeypatch):
    _open_position()
    client = FakeClient()
    monkeypatch.setattr(runner, "_client", lambda u: _async(client))
    monkeypatch.setattr(runner, "_cancel_protection", lambda u, c, p: _async(True))
    out = asyncio.run(runner.square_off_all("u1"))
    assert out["closed"] == 1
    assert positions.get("u1", "SYM").exit_reason == "square_off"


def test_square_off_is_a_no_op_when_flat():
    assert asyncio.run(runner.square_off_all("u1"))["closed"] == 0


def test_past_square_off_uses_the_configured_time(monkeypatch):
    cfg = AdaptiveEdgeConfig(square_off_time="15:15").validate()
    monkeypatch.setattr(runner, "_hhmm_now", lambda: "15:20")
    assert runner.past_square_off(cfg) is True
    monkeypatch.setattr(runner, "_hhmm_now", lambda: "14:00")
    assert runner.past_square_off(cfg) is False


# ----------------------------------------------------------------- adopt

def test_adopt_protects_the_position_it_takes_over(monkeypatch):
    """A hand-placed position this engine manages but has not protected is the
    worst of both worlds: nobody is watching and everybody assumes somebody is."""
    client = FakeClient()
    monkeypatch.setattr(runner, "_client", lambda u: _async(client))
    monkeypatch.setattr(runner, "_place_protection", lambda u, c, p, k: _async(777))
    result = asyncio.run(runner.adopt("u1", "TAKEN", 50, 100.0))
    assert result["ok"] is True and result["gtt_id"] == 777
    assert positions.get("u1", "TAKEN").is_open is True
    assert positions.get("u1", "TAKEN").stop_price == pytest.approx(70.0)
    assert positions.get("u1", "TAKEN").account_id == "acct-adaptive"


def test_live_adopt_refuses_inventory_the_broker_does_not_hold(monkeypatch):
    client = FakeClient(broker_positions={"net": []})
    client._is_paper = False
    monkeypatch.setattr(runner, "_client", lambda u: _async(client))

    called = []

    async def should_not_protect(*_args, **_kwargs):
        called.append(True)
        return 777

    monkeypatch.setattr(runner, "_place_protection", should_not_protect)
    result = asyncio.run(runner.adopt("u1", "TAKEN", 50, 100.0))

    assert result["ok"] is False
    assert "broker quantity" in result["reason"]
    assert positions.get("u1", "TAKEN") is None
    assert called == []


def test_adopt_refuses_a_contract_already_managed(monkeypatch):
    _open_position(symbol="TAKEN")
    result = asyncio.run(runner.adopt("u1", "TAKEN", 50, 100.0))
    assert result["ok"] is False and "already managing" in result["reason"]


def test_adopt_refuses_nonsense_quantities():
    assert asyncio.run(runner.adopt("u1", "X", 0, 100.0))["ok"] is False
    assert asyncio.run(runner.adopt("u1", "X", 50, 0.0))["ok"] is False


@pytest.mark.asyncio
async def test_release_maintenance_mode_never_starts_new_adaptive_edge_scan(monkeypatch):
    cfg = AdaptiveEdgeConfig(enabled=True).validate()
    monkeypatch.setattr(runner, "_kite_user_ids", lambda: ["u1"])
    monkeypatch.setattr(runner, "get_config", lambda _uid=None: cfg)
    monkeypatch.setattr(runner, "past_square_off", lambda _cfg: False)
    calls = []

    async def rec(uid):
        calls.append(("reconcile", uid))
        return {"checked": 1, "closed": 0, "reprotected": 1, "errors": []}

    async def sub(uid):
        calls.append(("subscribe", uid))

    async def forbidden_scan(_uid):
        raise AssertionError("release maintenance mode must not generate Adaptive Edge signals")

    async def forbidden_auto(_uid):
        raise AssertionError("release maintenance mode must not auto-enter Adaptive Edge")

    monkeypatch.setattr(runner, "reconcile", rec)
    monkeypatch.setattr(runner, "_subscribe_watched", sub)
    monkeypatch.setattr(runner, "scan_once", forbidden_scan)
    monkeypatch.setattr(runner, "_auto_enter", forbidden_auto)

    out = await runner.scan_all_once(manage_only=True)

    assert calls == [("reconcile", "u1"), ("subscribe", "u1")]
    assert out["u1"] == "maintenance only"

