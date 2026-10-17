"""
API endpoint tests for the Unified Multi-Strategy Backtesting route.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints.backtest import router


def get_client() -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


def test_get_unified_strategies():
    client = get_client()
    res = client.get("/api/v1/backtest/unified/strategies")
    assert res.status_code == 200
    strategies = res.json()
    assert len(strategies) == 6
    ids = [s["id"] for s in strategies]
    assert "adaptive_edge" in ids
    assert "supertrend" in ids
    assert "navigator" in ids
    assert "directional" in ids
    assert "mean_reversion" in ids
    assert "ema_trend" in ids
    ema = next(s for s in strategies if s["id"] == "ema_trend")
    assert ema["result_format"] == "EMA_TREND_LIFECYCLE_V1"
    assert ema["supported_contract_types"] == ["spot","future","option","auto"]


def test_get_unified_presets():
    client = get_client()
    res = client.get("/api/v1/backtest/unified/presets")
    assert res.status_code == 200
    presets = res.json()
    assert len(presets) >= 4


def test_run_unified_backtest_endpoint_kite_source():
    client = get_client()
    payload = {
        "strategy": "supertrend",
        "symbol": "NIFTY 50",
        "data_source": "kite",
        "timeframe": "5m",
        "lookback_days": 7,
        "starting_capital": 100000.0,
        "num_lots": 1,
        "stop_points": 40.0,
        "target_points": 80.0,
    }
    res = client.post("/api/v1/backtest/unified/run", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["strategy"] == "supertrend"
    assert data["symbol"] == "NIFTY 50"
    assert "KITE" in data["data_source"]
    assert "metrics" in data
    assert "equity_curve" in data
    assert "trades" in data
    assert data["starting_capital"] == 100000.0


def test_run_unified_backtest_endpoint_truedata_source():
    client = get_client()
    payload = {
        "strategy": "adaptive_edge",
        "symbol": "NIFTY BANK",
        "data_source": "truedata",
        "timeframe": "5m",
        "lookback_days": 7,
        "starting_capital": 150000.0,
        "num_lots": 2,
        "stop_points": 80.0,
        "target_points": 160.0,
    }
    res = client.post("/api/v1/backtest/unified/run", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["strategy"] == "adaptive_edge"
    assert data["symbol"] == "NIFTY BANK"
    assert "TRUEDATA" in data["data_source"]
    assert "metrics" in data
    assert "equity_curve" in data
    assert "trades" in data
    assert data["starting_capital"] == 150000.0


def test_run_adaptive_edge_compare_endpoint():
    client = get_client()
    payload = {
        "strategy": "adaptive_edge",
        "symbol": "NIFTY 50",
        "data_source": "kite",
        "timeframe": "5m",
        "lookback_days": 10,
        "starting_capital": 150000.0,
        "num_lots": 2,
    }
    res = client.post("/api/v1/backtest/adaptive-edge/compare", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert "v1" in data
    assert "v2" in data
    assert "comparison" in data
    cmp = data["comparison"]
    for key in ("net_pnl_delta_inr", "win_rate_delta_pct", "profit_factor_v1", "profit_factor_v2", "max_drawdown_reduction_pct", "toxic_trades_avoided"):
        assert key in cmp



def test_unknown_strategy_rejected_before_any_data_loader(monkeypatch):
    from app.api.v1.endpoints import backtest
    async def forbidden(*args,**kwargs):
        raise AssertionError("invalid strategy reached data loader")
    monkeypatch.setattr(backtest,"_load_backtest_candles",forbidden)
    res=get_client().post("/api/v1/backtest/unified/run",json={"strategy":"typo_strategy","symbol":"NIFTY 50"})
    assert res.status_code==422
    assert "no EMA fallback" in res.json()["detail"]


def test_ema_provider_mismatch_rejected_without_legacy_fallback(monkeypatch):
    from types import SimpleNamespace
    from app.core.auth import get_current_user
    from app.api.v1.endpoints import backtest
    from app.services.ema_trend import scanner
    async def forbidden(*args,**kwargs):
        raise AssertionError("EMA invalid request reached market or legacy fallback")
    monkeypatch.setattr(backtest,"_load_backtest_candles",forbidden)
    monkeypatch.setattr(scanner,"market_client",forbidden)
    client=get_client()
    client.app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(user_id="alice")
    response=client.post("/api/v1/backtest/unified/run",json={"strategy":"ema_trend","symbol":"NSE:TCS",
        "contract_type":"spot","instrument_scope":"single","data_source":"truedata","timeframe":"1d"})
    assert response.status_code==422
    assert "no synthetic/provider fallback" in response.json()["detail"]
