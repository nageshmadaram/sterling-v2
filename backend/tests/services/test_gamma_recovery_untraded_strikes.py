"""A watchlist whose strikes had not traded by the build time is a quiet market,
not a recovery failure. Counting it as one cleared every watch and kept Gamma
Unwind dark after every mid-day restart."""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.engines.gamma_move.config import GammaMoveConfig
from app.services import gamma_move_historical as hist
from app.services import gamma_move_scanner as scanner


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,raises", [
    (f"{hist.UNTRADED_NOTE}NYKAA/EXPIRY-PE: no strike had traded", False),
    ("NYKAA/EXPIRY-PE: option contracts existed ... unavailable from Kite", True),
])
async def test_only_real_data_gaps_fail_recovery(monkeypatch, failure, raises):
    from app.services.exchanges.kite import accounts
    monkeypatch.setattr(accounts, "get_active", lambda uid: object())
    monkeypatch.setattr(accounts, "acquire_client", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(hist, "_underlyings", AsyncMock(return_value=([], {}, [], [])))
    monkeypatch.setattr(hist, "_watches_at", AsyncMock(return_value=([], [failure])))
    monkeypatch.setattr(scanner, "_prime_runtime", AsyncMock(return_value=[]))
    cfg = GammaMoveConfig()
    build = datetime.fromisoformat("2026-10-23T14:00:00+05:30")
    if raises:
        with pytest.raises(RuntimeError, match="recovery incomplete"):
            await scanner.recover_scheduled_watchlist("u-untraded", cfg, build_time=build)
    else:
        await scanner.recover_scheduled_watchlist("u-untraded", cfg, build_time=build)
        assert scanner._stats["u-untraded"]["issue"] == ""
