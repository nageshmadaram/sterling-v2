from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.engines.gamma_move import (
    GammaMoveConfig,
    GammaSignal,
    GateContext,
    InstrumentRef,
    PositionState,
    TIER_ENTRY,
    TIER_STRONG_WATCHLIST,
    WatchContract,
)
from app.services import gamma_move_runner as runner

IST = timezone(timedelta(hours=5, minutes=30))
NOW_MS = int(datetime(2026, 10, 5, 11, 0, tzinfo=IST).timestamp() * 1000)


def signal(*, tier: str = TIER_ENTRY, dte: int = 1) -> GammaSignal:
    inst = InstrumentRef(
        "222", "RELIANCE26OCT3000CE", "CE", 3000,
        "2026-10-29", 10, 0.05, "NFO",
    )
    watch = WatchContract(
        "RELIANCE", 111, "BREAKOUT-CE", inst, "HOI-1",
        10_000, 1, 2990.0, 3000.0, 2.0,
        build_oi=10_000, chain_ltp=100.0, dte=dte,
    )
    passes = (
        ("zone", "liquidity", "direction", "unwind")
        if tier == TIER_ENTRY else
        ("zone", "liquidity", "unwind")
    )
    gates = GateContext(
        0.2, 1.0, 8.0, 0, True if tier == TIER_ENTRY else None,
        True if tier == TIER_ENTRY else None, True,
        (), () if tier == TIER_ENTRY else ("direction",),
        passes, "SC",
    )
    return GammaSignal(
        id=f"gamma:{tier}:{dte}",
        at_ms=NOW_MS,
        contract=watch,
        ltp=100.0,
        oi_drop_pct=10.0,
        vol_ratio=2.0,
        strong=False,
        base_low=90.0,
        base_high=99.0,
        reason="reference E1 breakout",
        snapshot={
            "oi_now": 900,
            "cycle_e2_ts_ms": NOW_MS - 5 * 60_000,
            "cycle_e2_close": 100.0,
            "oi_peak_bar_close": 99.0,
            "oi_peak_bar_oi": 1000,
        },
        gates=gates,
        tier=tier,
    )


def position(*, hold: str = "swing") -> PositionState:
    sig = signal(dte=1 if hold == "intraday" else 5)
    return PositionState.from_signal(
        sig,
        fill=100.0,
        quantity=10,
        lots=1,
        at_ms=NOW_MS,
        day="2026-10-05",
    )


@pytest.fixture(autouse=True)
def reset_runner():
    runner.clear()
    runner._schedule_done.clear()
    yield
    runner.clear()
    runner._schedule_done.clear()


@pytest.mark.asyncio
async def test_strong_watchlist_is_alert_only_and_cannot_execute(monkeypatch):
    cfg = GammaMoveConfig().validate()
    sig = signal(tier=TIER_STRONG_WATCHLIST)
    session = SimpleNamespace(signals={sig.id: sig})

    monkeypatch.setattr(runner, "get_config", lambda _uid=None: cfg)
    monkeypatch.setattr(runner, "session_for", lambda *_args, **_kwargs: session)

    result = await runner.arm("u1", sig.id)

    assert result["ok"] is False
    assert "alert-only" in result["message"]


@pytest.mark.asyncio
async def test_live_swing_entry_is_fail_closed_without_reference_broker_stop(monkeypatch):
    cfg = GammaMoveConfig().validate()
    sig = signal(tier=TIER_ENTRY, dte=5)
    session = SimpleNamespace(signals={sig.id: sig})

    class LiveClient:
        _is_paper = False
        _account_id = "acct-live"

    async def client(_uid):
        return LiveClient()

    monkeypatch.setattr(runner, "get_config", lambda _uid=None: cfg)
    monkeypatch.setattr(runner, "session_for", lambda *_args, **_kwargs: session)
    monkeypatch.setattr(runner.positions_store, "get", lambda *_args: None)
    monkeypatch.setattr(runner, "_client", client)

    result = await runner.arm("u1", sig.id)

    assert result["ok"] is False
    assert result["code"] == "reference_swing_protection_unavailable"


@pytest.mark.asyncio
async def test_paper_intraday_entry_routes_through_canonical_authority(monkeypatch):
    cfg = GammaMoveConfig(execution_lots=2).validate()
    sig = signal(tier=TIER_ENTRY, dte=1)
    notes = []
    session = SimpleNamespace(
        signals={sig.id: sig},
        note=lambda kind, message: notes.append((kind, message)),
    )
    stored = {}

    class PaperClient:
        _is_paper = True
        _account_id = "paper-1"

    async def client(_uid):
        return PaperClient()

    async def submit(**kwargs):
        stored["submit"] = kwargs
        return SimpleNamespace(
            success=True, status="ACKNOWLEDGED",
            order_id="PAPER-GAMMA-1", error=None,
        )

    async def resolve(**kwargs):
        return SimpleNamespace(
            filled_quantity=20,
            average_price=100.0,
            terminal=True,
            settled=True,
            reconciliation_required=False,
            projection_version=0,
            status="COMPLETE",
            reason="",
        )

    def put(_uid, pos):
        stored["pos"] = pos
        return pos

    def mark_filled(_uid, _symbol, price):
        pos = stored["pos"]
        pos.status = "open"
        pos.fill_price = price
        return pos

    monkeypatch.setattr(runner, "get_config", lambda _uid=None: cfg)
    monkeypatch.setattr(runner, "session_for", lambda *_args, **_kwargs: session)
    monkeypatch.setattr(runner.positions_store, "get", lambda *_args: None)
    monkeypatch.setattr(runner.positions_store, "put", put)
    monkeypatch.setattr(runner.positions_store, "mark_filled", mark_filled)
    monkeypatch.setattr(runner, "_client", client)
    monkeypatch.setattr(runner, "_safety", lambda *_args, **_kwargs: (True, ""))
    async def ensure_protection(*_args, **_kwargs):
        return True

    async def subscribe(*_args, **_kwargs):
        return None

    monkeypatch.setattr(runner, "_ensure_protection", ensure_protection)
    monkeypatch.setattr(runner, "_subscribe_watched", subscribe)

    from app.services import canonical_fill_reconciliation, canonical_strategy_entry
    monkeypatch.setattr(canonical_strategy_entry, "submit_entry", submit)
    monkeypatch.setattr(canonical_fill_reconciliation, "resolve_entry_fill", resolve)

    result = await runner.arm("u1", sig.id)

    assert result["ok"] is True
    assert result["quantity"] == 20
    assert result["hold"] == "intraday"
    assert result["stop"] == pytest.approx(90.0)
    assert stored["submit"]["strategy_id"] == "gamma_move"
    assert stored["submit"]["product"] == "MIS"
    assert stored["submit"]["quantity"] == 20


@pytest.mark.asyncio
async def test_partial_terminal_exit_reduces_only_confirmed_quantity(monkeypatch):
    from app.engines.gamma_move import TradeRecord
    from app.services import canonical_fill_reconciliation as fills

    pos = position(hold="intraday")
    pos.status = "open"
    pos.account_id = "paper-1"
    pos.exiting = True
    pos.exit_order_id = "EXIT-1"
    pos.exit_requested_qty = 10
    pos.exit_requested_price = 95.0
    pos.exit_pending_reason = "EOD"

    record = TradeRecord()
    notes = []
    protected = []

    class Client:
        _is_paper = True
        _account_id = "paper-1"

    async def resolved(**_kwargs):
        return fills.BrokerOrderResolution(
            order_id="EXIT-1",
            status="CANCELLED",
            filled_quantity=4,
            average_price=95.0,
            terminal=True,
            settled=True,
        )

    monkeypatch.setattr(fills, "resolve_acknowledged_order", resolved)
    monkeypatch.setattr(runner.positions_store, "put", lambda _uid, p: p)
    monkeypatch.setattr(runner.positions_store, "save_record", lambda _uid, r: r)
    monkeypatch.setattr(
        runner,
        "session_for",
        lambda _uid: SimpleNamespace(
            record=record,
            note=lambda kind, message: notes.append((kind, message)),
        ),
    )

    async def ensure(_uid, _client, p):
        protected.append(p.quantity)
        return True

    monkeypatch.setattr(runner, "_ensure_protection", ensure)

    done, status = await runner._settle_known_exit("u1", Client(), pos)

    assert done is False
    assert status == "partial_exit"
    assert pos.quantity == 6
    assert pos.exiting is False
    assert pos.exit_order_id == ""
    assert protected == [6]
    assert record.trades == 0
    assert record.realised_inr == pytest.approx(-20.0)
    assert pos.partial_realised_inr == pytest.approx(-20.0)


@pytest.mark.asyncio
async def test_unresolved_exit_keeps_single_sell_identity_and_no_reprotection(monkeypatch):
    from app.services import canonical_fill_reconciliation as fills

    pos = position(hold="intraday")
    pos.status = "open"
    pos.account_id = "paper-1"
    pos.exiting = True
    pos.exit_order_id = "EXIT-1"
    pos.exit_requested_qty = 10
    pos.exit_requested_price = 95.0
    pos.exit_pending_reason = "EOD"
    protected = []

    class Client:
        _is_paper = True
        _account_id = "paper-1"

    async def unresolved(**_kwargs):
        return fills.BrokerOrderResolution(
            order_id="EXIT-1",
            status="OPEN",
            filled_quantity=4,
            average_price=95.0,
            terminal=False,
            settled=False,
            reason="broker_order_unresolved_after_bounded_reconciliation",
        )

    monkeypatch.setattr(fills, "resolve_acknowledged_order", unresolved)
    monkeypatch.setattr(runner.positions_store, "put", lambda _uid, p: p)

    async def ensure(*args, **kwargs):
        protected.append(True)
        return True

    monkeypatch.setattr(runner, "_ensure_protection", ensure)

    done, status = await runner._settle_known_exit("u1", Client(), pos)

    assert done is False
    assert status == "exit_reconciliation_required"
    assert pos.exiting is True
    assert pos.exit_order_id == "EXIT-1"
    assert pos.quantity == 10
    assert protected == []


@pytest.mark.asyncio
async def test_reconcile_resizes_intraday_protection_to_confirmed_partial_entry(
    monkeypatch,
):
    from app.services import canonical_fill_reconciliation as fills

    pos = position(hold="intraday")
    pos.status = "pending"
    pos.order_id = "ENTRY-1"
    pos.account_id = "paper-1"
    pos.quantity = 3
    protected = []

    class Client:
        _is_paper = True
        _account_id = "paper-1"

    async def client(_uid):
        return Client()

    async def resolved(**_kwargs):
        return fills.FillResolution(
            order_id="ENTRY-1",
            intent_key="intent-1",
            status="OPEN",
            filled_quantity=5,
            average_price=101.0,
            terminal=False,
            settled=False,
            reconciliation_required=True,
            projection_version=2,
            reason="entry_fill_unresolved_after_bounded_reconciliation",
            expected_quantity=10,
        )

    def mark_filled(_uid, _symbol, price):
        pos.status = "open"
        pos.fill_price = price
        return pos

    async def ensure(_uid, _client, p):
        protected.append(p.quantity)
        return True

    monkeypatch.setattr(runner, "_client", client)
    monkeypatch.setattr(runner.positions_store, "open_positions", lambda _uid: [pos])
    monkeypatch.setattr(runner.positions_store, "get", lambda _uid, _sym: pos)
    monkeypatch.setattr(runner.positions_store, "put", lambda _uid, p: p)
    monkeypatch.setattr(runner.positions_store, "mark_filled", mark_filled)
    monkeypatch.setattr(fills, "resolve_known_entry", resolved)
    monkeypatch.setattr(fills, "mark_projected", lambda _resolution: True)
    monkeypatch.setattr(runner, "_ensure_protection", ensure)
    monkeypatch.setattr(runner, "_subscribe_watched", _async(None))

    result = await runner.reconcile("u1")

    assert result["restored"] == 0
    assert pos.status == "pending"
    assert pos.quantity == 5
    assert protected == [5]


@pytest.mark.asyncio
async def test_1520_sweep_retries_when_quote_is_temporarily_missing(monkeypatch):
    pos = position(hold="intraday")
    pos.status = "open"
    pos.account_id = "paper-1"
    stored = {pos.instrument.tradingsymbol: pos}
    quotes = iter([0.0, 95.0])
    exits = []

    class Client:
        _is_paper = True
        _account_id = "paper-1"

    async def client(_uid):
        return Client()

    monkeypatch.setattr(runner, "_client", client)
    monkeypatch.setattr(runner.positions_store, "open_positions", lambda _uid: [
        p for p in stored.values() if p.is_open
    ])
    monkeypatch.setattr(
        runner.positions_store, "get",
        lambda _uid, symbol: stored.get(symbol),
    )
    monkeypatch.setattr(runner.positions_store, "put", lambda _uid, p: stored.__setitem__(
        p.instrument.tradingsymbol, p
    ) or p)
    monkeypatch.setattr(runner, "_quote_ltp", lambda *_args, **_kwargs: _async(next(quotes)))

    async def execute(_uid, p, *, market_ltp, reason, grade_price=None):
        exits.append((reason, market_ltp))
        p.status = "closed"
        p.trade_state = "CLOSED"
        stored[p.instrument.tradingsymbol] = p
        return True, "closed"

    monkeypatch.setattr(runner, "_execute_exit", execute)
    monkeypatch.setattr(
        runner, "session_for",
        lambda _uid: SimpleNamespace(
            cfg=GammaMoveConfig().validate(),
            note=lambda *_args: None,
        ),
    )

    now = datetime(2026, 10, 5, 15, 21, tzinfo=IST)
    await runner._reference_time_sweeps("u1", now)
    key = ("u1", "2026-10-05", "15:20")
    assert key not in runner._schedule_done
    assert exits == []

    await runner._reference_time_sweeps("u1", now + timedelta(seconds=5))
    assert exits == [("EOD", 95.0)]
    assert key in runner._schedule_done


@pytest.mark.asyncio
async def test_time_sweep_reconciles_existing_sell_without_duplicate_submit(monkeypatch):
    pos = position(hold="intraday")
    pos.status = "open"
    pos.account_id = "paper-1"
    pos.exiting = True
    pos.exit_order_id = "EXIT-1"
    pos.exit_requested_qty = 10
    pos.exit_requested_price = 95.0
    pos.exit_pending_reason = "EOD"
    stored = {pos.instrument.tradingsymbol: pos}
    settled = []

    class Client:
        _is_paper = True
        _account_id = "paper-1"

    async def client(_uid):
        return Client()

    async def settle(_uid, _client, p):
        settled.append(p.exit_order_id)
        return False, "exit_reconciliation_required"

    async def duplicate(*_args, **_kwargs):
        raise AssertionError("time sweep must not submit a second SELL")

    monkeypatch.setattr(runner, "_client", client)
    monkeypatch.setattr(runner, "_settle_known_exit", settle)
    monkeypatch.setattr(runner, "_execute_exit", duplicate)
    monkeypatch.setattr(runner.positions_store, "open_positions", lambda _uid: [pos])
    monkeypatch.setattr(runner.positions_store, "get", lambda _uid, _symbol: pos)
    monkeypatch.setattr(runner.positions_store, "put", lambda _uid, p: p)
    monkeypatch.setattr(
        runner, "session_for",
        lambda _uid: SimpleNamespace(
            cfg=GammaMoveConfig().validate(),
            note=lambda *_args: None,
        ),
    )

    await runner._reference_time_sweeps(
        "u1", datetime(2026, 10, 5, 15, 21, tzinfo=IST)
    )

    assert settled == ["EXIT-1"]
    assert pos.exiting is True


def _async(value):
    async def inner(*_args, **_kwargs):
        return value
    return inner()


def test_scheduler_claims_latest_due_slot_once():
    now = datetime(2026, 10, 5, 14, 0, tzinfo=IST)
    slots = ("09:20", "13:15")

    first = runner._claim_latest_due_slot(
        "u1", now, "watchlist", slots,
        not_after=datetime(2026, 10, 5, 15, 20, tzinfo=IST).time(),
    )
    second = runner._claim_latest_due_slot(
        "u1", now, "watchlist", slots,
        not_after=datetime(2026, 10, 5, 15, 20, tzinfo=IST).time(),
    )

    assert first == "13:15"
    assert second is None
    assert ("u1", "2026-10-05", "watchlist:09:20") in runner._schedule_done
    assert ("u1", "2026-10-05", "watchlist:13:15") in runner._schedule_done


@pytest.mark.asyncio
async def test_t1_moves_to_breakeven_without_inventing_partial_book(monkeypatch):
    pos = position(hold="swing")
    pos.status = "open"
    saved = []
    notes = []
    monkeypatch.setattr(
        runner.positions_store, "put",
        lambda _uid, p: saved.append((p.trade_state, p.stop, p.quantity)) or p,
    )
    monkeypatch.setattr(
        runner, "session_for",
        lambda _uid: SimpleNamespace(note=lambda k, m: notes.append((k, m))),
    )

    state = await runner._manage_position_tick("u1", pos, pos.t1)

    assert state == "watching"
    assert pos.trade_state == "T1_TRAIL"
    assert pos.stop == pytest.approx(pos.effective_entry)
    assert pos.quantity == 10
    assert any(kind == "T1" for kind, _ in notes)


@pytest.mark.asyncio
async def test_same_tick_through_t1_and_t2_closes_at_t2(monkeypatch):
    pos = position(hold="swing")
    pos.status = "open"
    exits = []

    monkeypatch.setattr(runner.positions_store, "put", lambda _uid, p: p)
    monkeypatch.setattr(
        runner, "session_for",
        lambda _uid: SimpleNamespace(note=lambda *_args: None),
    )

    async def execute(_uid, _pos, *, market_ltp, reason, grade_price=None):
        exits.append((market_ltp, reason, grade_price))
        return True, "closed"

    monkeypatch.setattr(runner, "_execute_exit", execute)

    state = await runner._manage_position_tick("u1", pos, pos.t2 + 5)

    assert state == "exited"
    assert pos.trade_state == "T1_TRAIL"
    assert exits == [(pos.t2 + 5, "T2", pos.t2 + 5)]
