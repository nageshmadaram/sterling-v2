from fastapi import FastAPI
from fastapi.testclient import TestClient
from types import SimpleNamespace

from app.api.v1.endpoints import adaptive_edge as adaptive_module
from app.api.v1.endpoints.adaptive_edge import router


def test_settings_round_trip_and_snapshot_shape():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    client = TestClient(app)
    got = client.get("/api/v1/adaptive-edge/settings")
    assert got.status_code == 200
    assert got.json()["live_trading"] is False
    body = got.json()["settings"]
    body["stop_points"] = 90
    put = client.put("/api/v1/adaptive-edge/settings", json=body)
    assert put.status_code == 200
    assert put.json()["settings"]["stop_points"] == 90
    snap = client.get("/api/v1/adaptive-edge/snapshot")
    assert snap.status_code == 200
    payload = snap.json()
    assert payload["live_trading"] is False
    assert payload["production_gate_authorized"] is False
    assert payload["registry_locked"] is True
    assert "session" in payload
    assert "readiness" in payload
    assert "mode_counts" in payload
    assert "formula_table" in payload
    assert "signals" in payload
    assert payload["production_gate_authorized"] is False


def test_legacy_settings_are_scoped_to_authenticated_user(monkeypatch):
    values = {}
    monkeypatch.setattr(
        adaptive_module.db,
        "get_config",
        lambda key, default="": values.get(key, default),
    )
    monkeypatch.setattr(
        adaptive_module.db,
        "set_config",
        lambda key, value: values.__setitem__(key, value),
    )

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    current = {"uid": "tenant-one"}
    app.dependency_overrides[adaptive_module.get_current_user] = lambda: SimpleNamespace(
        user_id=current["uid"]
    )
    client = TestClient(app)

    one = client.get("/api/v1/adaptive-edge/settings").json()["settings"]
    one["stop_points"] = 91
    assert client.put("/api/v1/adaptive-edge/settings", json=one).status_code == 200

    current["uid"] = "tenant-two"
    two = client.get("/api/v1/adaptive-edge/settings").json()["settings"]
    assert two["stop_points"] != 91
    two["stop_points"] = 73
    assert client.put("/api/v1/adaptive-edge/settings", json=two).status_code == 200

    current["uid"] = "tenant-one"
    assert client.get("/api/v1/adaptive-edge/settings").json()["settings"]["stop_points"] == 91


def test_disconnected_tenant_does_not_consume_candle_sync_throttle(monkeypatch):
    from app.api.v1.endpoints import adaptive_edge as mod
    from app.services.exchanges.kite import accounts

    mod._LAST_SYNC_TS.clear()
    monkeypatch.setattr(mod.time, "time", lambda: 1000.0)
    monkeypatch.setattr("app.services.ohlcv_store.get_candles", lambda *_a, **_k: [])
    monkeypatch.setattr(accounts, "bootstrap", lambda: None)
    monkeypatch.setattr(accounts, "_loaded", True, raising=False)
    monkeypatch.setattr(accounts, "get_active", lambda uid: None)

    import asyncio
    asyncio.run(mod._sync_live_5m_candles_if_needed("tenant-offline", ["NIFTY 50"]))

    assert "tenant-offline" not in mod._LAST_SYNC_TS


def test_settings_accepts_universe_and_ladder():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    client = TestClient(app)
    body = client.get("/api/v1/adaptive-edge/settings").json()["settings"]
    body["scan_indices"] = ["NIFTY 50", "NIFTY BANK"]
    body["strike_moneyness"] = ["ITM2", "ITM1", "ATM", "OTM1", "OTM2"]
    put = client.put("/api/v1/adaptive-edge/settings", json=body)
    assert put.status_code == 200
    assert put.json()["live_trading"] is False
    assert put.json()["settings"]["strike_moneyness"] == ["ITM2", "ITM1", "ATM", "OTM1", "OTM2"]
    assert put.json()["settings"]["symbols"] == ["NIFTY-I", "BANKNIFTY-I"]
    assert put.json()["settings"]["symbol"] == "NIFTY-I"


def test_settings_accepts_engine_scope_fields():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    client = TestClient(app)
    body = client.get("/api/v1/adaptive-edge/settings").json()["settings"]
    body["scan_source"] = "spot"
    body["scan_stock_contracts"] = False
    body["scan_all_stocks"] = False
    body["scan_stocks"] = []
    body["scan_expiries_indices"] = ["weekly", "monthly"]
    put = client.put("/api/v1/adaptive-edge/settings", json=body)
    assert put.status_code == 200
    saved = put.json()["settings"]
    assert saved["scan_source"] == "spot"
    assert saved["scan_stock_contracts"] is False
    assert saved["scan_expiries"] == ["weekly", "monthly"]
    assert put.json()["live_trading"] is False


def test_snapshot_returns_simulation_signals_when_active():
    from app.services.simulation import simulation_runner, SimState, SimSignalEvent
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    client = TestClient(app)

    try:
        simulation_runner._state = SimState.PAUSED
        simulation_runner._stats.events = [
            SimSignalEvent(
                time_iso="09:14:00",
                timestamp_ms=1787889240000,
                strategy="adaptive_edge",
                instrument="KOTAKBANK",
                direction="BULLISH",
                strength="STRONG",
                entry=1785.45,
                stop=1763.25,
                target=1822.45,
            ),
            SimSignalEvent(
                time_iso="09:14:00",
                timestamp_ms=1787889240000,
                strategy="adaptive_edge",
                instrument="AXISBANK",
                direction="BULLISH",
                strength="STRONG",
                entry=1162.30,
                stop=1148.80,
                target=1184.80,
            ),
            SimSignalEvent(
                time_iso="09:14:00",
                timestamp_ms=1787889240000,
                strategy="adaptive_edge",
                instrument="SENSEX",
                direction="BEARISH",
                strength="STRONG",
                entry=81342.10,
                stop=81612.10,
                target=80892.10,
            ),
        ]

        snap = client.get("/api/v1/adaptive-edge/snapshot")
        assert snap.status_code == 200
        payload = snap.json()
        assert payload["label"] == "SIMULATION_REPLAY"
        assert "signals" in payload
        assert len(payload["signals"]) == 3
        underlyings = [s["underlying"] for s in payload["signals"]]
        assert underlyings == ["KOTAKBANK", "AXISBANK", "SENSEX"]
        
        # Verify structure for rowsFromSnapshot
        for s in payload["signals"]:
            assert s["scanned"] is True
            assert len(s["legs"]) > 0
            for leg in s["legs"]:
                assert "moneyness" in leg
                assert "option_type" in leg
                assert "strike" in leg
                assert "entry_premium" in leg
    finally:
        simulation_runner._state = SimState.IDLE
        simulation_runner._stats.events = []

