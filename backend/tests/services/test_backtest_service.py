"""Exercise the endpoint's orchestration with the real SuperTrend replay engine."""
import json
import math

import pytest

from app.engines.sterling_kite_engine.schemas import BacktestRequest
from app.schemas.market import Candle
from app.services.kite_engine import backtest_service as service


@pytest.mark.parametrize("mode, expected", [
    ("synthetic", ["synthetic"]), ("real", ["real"]), ("both", ["synthetic", "real"]),
])
async def test_requested_modes_execute_and_serialize(monkeypatch, mode, expected):
    async def underlying(_client, _symbol):
        return 111, "NIFTY 50"

    async def option(_client, _symbol):
        return 222, "NFO"

    class Client:
        calls = []

        async def get_candles(self, instrument, timeframe, count):
            self.calls.append((instrument.zerodha_token, timeframe, count))
            base = 22_000 if instrument.zerodha_token == 111 else 100
            return [Candle(
                timestamp_ms=1_700_000_000_000 + i * 3_600_000,
                open=base + i * 0.1 + math.sin(i / 8) * 5,
                high=base + i * 0.1 + math.sin(i / 8) * 5 + 2,
                low=base + i * 0.1 + math.sin(i / 8) * 5 - 2,
                close=base + i * 0.1 + math.sin(i / 8) * 5 + 0.2,
                volume=1000,
            ) for i in range(160)]

    monkeypatch.setattr(service, "_resolve_underlying_token", underlying)
    monkeypatch.setattr(service, "_resolve_option_token", option)
    client = Client()
    result = await service.run_backtest(client, BacktestRequest(
        symbol="NIFTY 50", data_mode=mode, lookback_bars=160,
    ))
    assert [run["mode"] for run in result["runs"]] == expected
    assert len(client.calls) == len(expected)
    assert all(tf == "1H" and count == 160 for _, tf, count in client.calls)
    assert all(run["equity_curve"] and run["stats"] for run in result["runs"])
    json.dumps(result, allow_nan=False)
