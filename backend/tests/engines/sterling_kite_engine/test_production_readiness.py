"""Tests for production readiness diagnostics, production presets, and emergency square-off."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.engines.sterling_kite_engine.schemas import EngineConfigModel, ReadinessResponse
from app.services.kite_engine import positions, service, state, monitor
from app.services import live_safety


class _MockUser:
    def __init__(self, uid: str = "test_prod_user"):
        self.user_id = uid


def test_production_preset_defaults():
    """Verify that EngineConfigModel.production() carries the sweep-validated parameters."""
    cfg = EngineConfigModel.production()
    assert cfg.trail_target == "fast"
    assert cfg.exit_mode == "one_red"
    assert cfg.price_stop_exit is True
    assert cfg.adx_min == 25.0
    assert cfg.time_stop_bars == 48
    assert cfg.max_daily_loss_pct == 2.0
    assert cfg.wire_risk_infra is True
    assert cfg.block_entry_minutes_before_close == 15
    assert cfg.max_spread_pct == 5.0
    assert cfg.min_oi == 100
    assert cfg.stop_mode == "both"
    assert cfg.risk_sizing is True
    assert cfg.risk_pct == 1.0
    assert cfg.max_lots == 10
    assert cfg.allow_min_lot_over_risk is False


@pytest.mark.asyncio
async def test_apply_production_config_endpoint():
    """Verify POST /config/production endpoint applies production preset."""
    from app.api.v1.endpoints.kite_engine import apply_production_config
    user = _MockUser()
    applied = await apply_production_config(user=user)
    assert applied.adx_min == 25.0
    assert applied.time_stop_bars == 48
    assert applied.max_daily_loss_pct == 2.0
    assert applied.wire_risk_infra is True
    stored = state.get_config(user.user_id)
    assert stored.adx_min == 25.0
    assert stored.max_daily_loss_pct == 2.0


@pytest.mark.asyncio
async def test_live_readiness_no_account():
    """When no active Kite account exists, readiness reports blocked broker session."""
    uid = "test_no_acct_user"
    state.reset(uid)
    report = await service.check_live_readiness(None, uid)
    assert report["ready_for_live"] is False
    assert "broker_session" in report["checks"]
    assert report["checks"]["broker_session"]["status"] == "blocked"
    assert any("No active Kite account" in b for b in report["blockers"])


@pytest.mark.asyncio
async def test_live_readiness_kill_switch_active():
    """When kill switch is engaged, readiness blocks trading."""
    uid = "test_ks_user"
    state.reset(uid)
    live_safety.set_kill_switch(True, "Market anomaly detected")
    try:
        report = await service.check_live_readiness(None, uid)
        assert report["ready_for_live"] is False
        assert report["checks"]["kill_switch"]["status"] == "blocked"
        assert any("Kill Switch" in b for b in report["blockers"])
    finally:
        live_safety.set_kill_switch(False)


@pytest.mark.asyncio
async def test_live_readiness_reports_code_level_execution_floor(monkeypatch):
    from types import SimpleNamespace
    from app.core import lane_registry

    uid = "floor-user"
    acct = SimpleNamespace(
        id="acct-floor",
        label="Live",
        is_paper=False,
        has_credentials=True,
        kite_user_id="KU1",
    )
    session = SimpleNamespace(
        connected=True,
        message="ok",
        user_name="Trader",
        expires_at_ms=9999999999999,
    )
    client = SimpleNamespace()

    monkeypatch.setattr(
        "app.services.exchanges.kite.accounts.get_active",
        lambda _uid: acct,
    )

    async def ensure_session(*_args, **_kwargs):
        return session

    monkeypatch.setattr(
        "app.services.exchanges.kite.auth.ensure_session",
        ensure_session,
    )

    async def cap(_client):
        return 100000.0

    monkeypatch.setattr(service, "available_fo_capital", cap)
    monkeypatch.setattr(service, "autoexec_preflight", lambda _uid: [])
    monkeypatch.setattr(service.state, "daily_realized_pnl", lambda _uid: 0.0)
    monkeypatch.setattr(
        service.state,
        "get_config",
        lambda _uid: service.EngineConfigModel(
            auto_execute=True,
            supertrend_auto_execute=True,
            stop_mode="both",
            adx_min=25.0,
            time_stop_bars=48,
            wire_risk_infra=True,
            max_daily_loss_pct=2.0,
        ),
    )
    monkeypatch.setattr(service.live_safety, "kill_switch_state", lambda: {"enabled": False})
    monkeypatch.setattr(service, "is_market_open", lambda: True)
    monkeypatch.setattr(
        service.market_hours,
        "session_phase",
        lambda: "continuous",
    )
    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", False)

    report = await service.check_live_readiness(client, uid)

    assert report["ready_for_live"] is False
    assert report["checks"]["live_execution_floor"]["status"] == "blocked"
    assert report["checks"]["live_execution_floor"]["data"]["live_execution_enabled"] is False
    assert any("release-wide code-level execution floor" in item for item in report["blockers"])


@pytest.mark.asyncio
async def test_live_readiness_with_healthy_mock_account():
    """When connected with sufficient capital and no blockers, checks pass."""
    uid = "test_healthy_user"
    state.reset(uid)
    state.set_config(uid, EngineConfigModel.production())

    mock_client = AsyncMock()
    mock_client.get_margins.return_value = {
        "available": {"live_balance": 150000.0, "cash": 150000.0}
    }

    mock_acct = MagicMock()
    mock_acct.id = "acct_1"
    mock_acct.label = "Zerodha Live"
    mock_acct.is_paper = False
    mock_acct.has_credentials = True
    mock_acct.kite_user_id = "ZD1234"

    with patch("app.services.exchanges.kite.accounts.get_active", return_value=mock_acct), \
         patch("app.services.exchanges.kite.auth.ensure_session") as mock_auth, \
         patch("app.services.kite_engine.service.autoexec_preflight", return_value=[]):

        mock_auth.return_value = MagicMock(connected=True, message="Connected", user_name="Trader X", expires_at_ms=1800000000000)

        report = await service.check_live_readiness(mock_client, uid)
        assert report["checks"]["broker_session"]["status"] == "ok"
        assert report["checks"]["capital"]["status"] == "ok"
        assert report["checks"]["capital"]["data"]["available_inr"] == 150000.0
        assert report["checks"]["registry"]["status"] == "ok"
        assert report["checks"]["kill_switch"]["status"] == "ok"
        assert report["checks"]["circuit_breaker"]["status"] == "ok"
        assert report["checks"]["strategy_tuning"]["status"] == "ok"


@pytest.mark.asyncio
async def test_emergency_square_off_all():
    """Verify emergency square-off exits all positions and clears state."""
    uid = "test_sq_user"
    state.reset(uid)
    positions.reset(uid)

    # Create a mock open position
    pos = positions.OpenPosition(
        uid=uid,
        symbol="NIFTY24SEPFUT",
        exchange="NFO",
        token=12345,
        qty=50,
        lot_size=50,
        entry_premium=25000.0,
        fill_price=25000.0,
        stop_premium=24800.0,
        status=positions.OPEN,
        direction="long",
        underlying="NIFTY 50",
        guard_key="NIFTY 50",
    )
    positions.register(pos)
    state.mark_auto_open(uid, "NIFTY 50")

    mock_client = AsyncMock()
    mock_client.get_ltp.return_value = {"NFO:NIFTY24SEPFUT": {"last_price": 25100.0}}

    with patch("app.services.kite_engine.monitor._exit_position", new_callable=AsyncMock) as mock_exit:
        mock_exit.return_value = True

        res = await service.emergency_square_off_all(mock_client, uid)
        # An exit order that has been SENT is not a flat position. Until the broker
        # confirms the fill the position stays registered, protected and monitored,
        # and the auto-open guard stays set (clearing it would license a re-entry).
        assert res["status"] == "PENDING_BROKER_CONFIRMATION"
        assert res["positions_count"] == 1
        assert res["squared_off"] == 0 and res["submitted_awaiting_fill"] == 1
        assert mock_exit.called
        assert state.is_auto_open(uid, "NIFTY 50")


@pytest.mark.asyncio
async def test_emergency_halt():
    """Verify emergency halt engages kill switch and turns off auto-execute."""
    uid = "test_halt_user"
    state.reset(uid)
    positions.reset(uid)
    state.set_config(uid, EngineConfigModel(auto_execute=True))

    mock_client = AsyncMock()
    with patch("app.services.kite_engine.service.emergency_square_off_all", new_callable=AsyncMock) as mock_sq:
        mock_sq.return_value = {"status": "ok", "positions_count": 0, "squared_off": 0, "failed": 0, "details": []}

        res = await service.emergency_halt(mock_client, uid, reason="Test emergency halt")
        assert res["status"] == "ok"
        assert live_safety.kill_switch_state()["enabled"] is True
        assert state.get_config(uid).auto_execute is False
        assert mock_sq.called
    live_safety.set_kill_switch(False)

@pytest.mark.asyncio
async def test_stale_pending_cleanup_reconciles_entry_before_closed_positions(monkeypatch):
    from app.api.v1.endpoints import kite_engine as endpoint

    uid = "test_pending_cleanup"
    symbol = "NIFTY24JUN24000CE"
    positions.reset(uid)
    positions.register(positions.OpenPosition(
        uid=uid,
        symbol=symbol,
        exchange="NFO",
        token=777,
        qty=0,
        order_id="ENTRY-PENDING",
        status=positions.PENDING,
        entry_pending=True,
        guard_key="NIFTY 50",
    ))
    user = _MockUser(uid)
    calls = []

    async def fake_client(_user):
        return object()

    async def reconcile_pending(_client, got_uid):
        calls.append(("pending", got_uid))
        positions.mark_rejected(got_uid, symbol, "broker cancelled")

    async def reconcile_closed(_client, got_uid):
        calls.append(("closed", got_uid))

    monkeypatch.setattr(endpoint, "_client", fake_client)
    monkeypatch.setattr(service, "_reconcile_pending_positions", reconcile_pending)
    monkeypatch.setattr(service, "_reconcile_closed_positions", reconcile_closed)

    response = await endpoint.close_position(symbol, user=user)

    assert calls == [("pending", uid), ("closed", uid)]
    assert positions.get(uid, symbol).status == positions.REJECTED
    assert response.positions == []



# ── the account-level daily-loss limit is shown before the first trade ──────────────

async def _readiness_with(uid: str, *, capital: float, **config):
    state.reset(uid)
    state.set_config(uid, EngineConfigModel.production().model_copy(update=config))
    mock_client = AsyncMock()
    mock_client.get_margins.return_value = {"available": {"live_balance": capital, "cash": capital}}
    mock_acct = MagicMock()
    mock_acct.id, mock_acct.label = "acct_1", "Zerodha Live"
    mock_acct.is_paper, mock_acct.has_credentials, mock_acct.kite_user_id = False, True, "ZD1234"
    with patch("app.services.exchanges.kite.accounts.get_active", return_value=mock_acct), \
         patch("app.services.exchanges.kite.auth.ensure_session") as mock_auth, \
         patch("app.services.kite_engine.service.autoexec_preflight", return_value=[]):
        mock_auth.return_value = MagicMock(connected=True, message="Connected",
                                           user_name="Trader X", expires_at_ms=1800000000000)
        return await service.check_live_readiness(mock_client, uid)


@pytest.mark.asyncio
async def test_the_shipped_account_daily_loss_default_is_flagged_when_one_stop_out_exceeds_it():
    """A 1% risk budget on ₹10 lakh is ₹10,000 at risk per trade; the shipped account hard halt
    is ₹1,500 and halts every strategy for the day. That mismatch must be visible up front."""
    uid = "dl-default-user"
    live_safety.clear_daily_loss(uid)

    report = await _readiness_with(uid, capital=1_000_000.0)

    check = report["checks"]["account_daily_loss"]
    assert check["status"] == "warning"
    assert check["data"]["hard_halt_inr"] == -1500.0 and check["data"]["is_account_override"] is False
    assert check["data"]["risk_budget_per_trade_inr"] == 10_000.0
    assert "/api/v1/kite/risk/daily-loss" in check["detail"] and "shipped default" in check["detail"]
    assert any("daily-loss hard halt" in w for w in report["warnings"])
    # A warning, not a refusal: the control is not weakened and not made stricter either.
    assert not any("daily-loss hard halt" in b for b in report["blockers"])


@pytest.mark.asyncio
async def test_an_account_daily_loss_limit_that_covers_one_trade_reads_ok():
    uid = "dl-set-user"
    live_safety.configure_daily_loss(
        live_safety.DailyLossConfig(enabled=True, soft_warn_inr=-15000.0, hard_halt_inr=-20000.0),
        uid=uid)
    try:
        report = await _readiness_with(uid, capital=1_000_000.0)
    finally:
        live_safety.clear_daily_loss(uid)

    check = report["checks"]["account_daily_loss"]
    assert check["status"] == "ok", check
    assert check["data"]["is_account_override"] is True and check["data"]["hard_halt_inr"] == -20000.0
    assert not any("daily-loss hard halt" in w for w in report["warnings"])


@pytest.mark.asyncio
async def test_a_disabled_account_daily_loss_limit_is_flagged():
    uid = "dl-off-user"
    live_safety.configure_daily_loss(
        live_safety.DailyLossConfig(enabled=False, soft_warn_inr=-1000.0, hard_halt_inr=-1500.0),
        uid=uid)
    try:
        report = await _readiness_with(uid, capital=1_000_000.0)
    finally:
        live_safety.clear_daily_loss(uid)

    assert report["checks"]["account_daily_loss"]["status"] == "warning"
    assert any("DISABLED" in w for w in report["warnings"])
