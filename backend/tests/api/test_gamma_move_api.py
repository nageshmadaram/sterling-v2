"""Gamma-Unwind config/API contract."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints.config import router


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STERLING_DB_PATH", str(tmp_path / "test.db"))
    from app.services import db
    monkeypatch.setattr(db, "_DB_PATH", str(tmp_path / "test.db"), raising=False)
    db.init()
    from app.services.simulation import SimState, simulation_runner
    simulation_runner._state = SimState.IDLE
    simulation_runner._session_complete = False
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_descriptor_is_the_uploaded_gamma_unwind_client(client):
    body = client.get("/config/gamma-move").json()
    s = body["strategy"]
    assert s["id"] == "gamma_move"
    assert s["name"] == "Gamma Unwind V1"
    assert s["reference_package"] == "GAMMA-UNWIND client 0.8.2"
    assert s["spec_version"] == "S1.6.0"
    assert s["edition"] == "client"
    assert s["signal_parity"] is True
    assert s["client_top_n_oi"] == 1
    assert "separate safety adapter" in s["execution_note"]


def test_reference_defaults_and_effective_client_invariants_are_published(client):
    body = client.get("/config/gamma-move").json()
    d = body["defaults"]
    assert d["zone_pivot_len"] == 10
    assert d["zone_atr_len"] == 50
    assert d["stock_band_pct"] == 2.5
    assert d["strike_band_pct"] == 2.5
    assert d["top_n_oi"] == 2
    assert d["oi_drop_alert"] == pytest.approx(0.08)
    assert d["oi_drop_tolerance"] == pytest.approx(0.01)
    assert d["oi_drop_strong"] == pytest.approx(0.12)
    assert d["signal_tf_min"] == 5
    assert d["swing_days"] == 5
    assert d["mark_levels_times"] == ["09:20", "16:00"]
    assert d["build_watchlists_times"] == ["09:20", "13:15"]

    effective = body["client_effective"]
    assert effective == {
        "top_n_oi": 1,
        "base_tf_min": 1,
        "signal_tf_min": 5,
        "display_tf_min": 15,
    }


def test_vocabularies_publish_only_actual_gamma_unwind_choice_fields(client):
    v = client.get("/config/gamma-move").json()["vocabularies"]
    assert set(v["stop_mode"]) == {"monitor", "broker", "both"}
    assert set(v["v1_direction_mode"]) == {"any", "all"}
    assert v["scan_stocks"]


def test_every_config_field_round_trips_and_unknown_keys_fail(client):
    before = client.get("/config/gamma-move").json()["config"]
    r = client.put("/config/gamma-move", json={
        "oi_drop_alert": 0.09,
        "zone_max_gap_pct": 1.25,
        "execution_lots": 2,
        "late_day_warn_ist": "14:45",
    })
    assert r.status_code == 200, r.text
    cfg = r.json()["config"]
    assert cfg["oi_drop_alert"] == pytest.approx(0.09)
    assert cfg["zone_max_gap_pct"] == pytest.approx(1.25)
    assert cfg["execution_lots"] == 2
    assert cfg["late_day_warn_ist"] == "14:45"
    assert cfg["zone_pivot_len"] == before["zone_pivot_len"]

    bad = client.put("/config/gamma-move", json={"old_gamma_trigger_timeframe": "15minute"})
    assert bad.status_code == 422
    assert "old_gamma_trigger_timeframe" in bad.json()["detail"]


@pytest.mark.parametrize("payload,fragment", [
    ({"base_tf_min": 5}, "base_tf_min is fixed at 1"),
    ({"signal_tf_min": 15}, "signal_tf_min is fixed at 5"),
    ({"display_tf_min": 5}, "display_tf_min is fixed at 15"),
    ({"top_n_oi": 3}, "strategy.yaml reference value 2"),
    ({"token_poll_minutes": 5}, "token_poll_minutes is fixed"),
    ({"gates_all_lists": False}, "gates_all_lists is fixed true"),
    ({"oi_drop_tolerance": 0.5}, "oi_drop_tolerance"),
])
def test_client_fixed_or_invalid_config_is_refused(client, payload, fragment):
    r = client.put("/config/gamma-move", json=payload)
    assert r.status_code == 422
    assert fragment in r.json()["detail"]


def test_empty_update_is_refused(client):
    assert client.put("/config/gamma-move", json={}).status_code == 422


def test_zero_signal_funnel_diagnoses_each_stage():
    from app.engines.gamma_move import GammaMoveConfig
    from app.services.gamma_move import zero_signal_funnel_message

    cfg = GammaMoveConfig().validate()

    assert "has not completed" in zero_signal_funnel_message(cfg, {})
    assert "zero eligible listed F&O" in zero_signal_funnel_message(cfg, {
        "stage_a": {"scanned": 0, "near_level": 0},
    })
    assert "none were within" in zero_signal_funnel_message(cfg, {
        "stage_a": {"scanned": 180, "near_level": 0},
    })
    assert "no HOI-1 OTM contract" in zero_signal_funnel_message(cfg, {
        "stage_a": {"scanned": 180, "near_level": 4},
        "stage_b": {"candidates": 0},
    })
    assert "none could be warmed" in zero_signal_funnel_message(cfg, {
        "stage_a": {"scanned": 180, "near_level": 4},
        "stage_b": {"candidates": 3},
        "stage_c": {"watched": 0},
    })
    assert "no live E1 breakout" in zero_signal_funnel_message(cfg, {
        "stage_a": {"scanned": 180, "near_level": 4},
        "stage_b": {"candidates": 3},
        "stage_c": {"watched": 3},
    })


def test_snapshot_names_the_reference_constraints(client):
    body = client.get("/config/gamma-move/snapshot").json()
    assert body["strategy"]["name"] == "Gamma Unwind V1"
    assert any("HOI-1" in w for w in body["warnings"])
    assert any("signal-only" in w for w in body["warnings"])
    assert set(body["mode"]) >= {"is_paper", "auto_execute", "note"}


def test_snapshot_exposes_uncached_day_as_read_only_replayable(client):
    r = client.get("/config/gamma-move/snapshot?day=2026-10-01")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["day"] == "2026-10-01"
    assert body["historical"] is True
    assert body["recorded"] is False
    assert body["source"] == "available_replay"
    assert body["replay_available"] is True
    assert body["is_trading_day"] is True
    assert body["mode"]["auto_execute"] is False
    assert any("Replay day" in text for text in body["blockers"])


def test_history_endpoint_returns_multi_day_e1_ledger(client):
    from app.services import gamma_move_history
    gamma_move_history.append_signals("default", [
        {
            "id": "e1-entry",
            "at_ms": 1790915400000,
            "tier": "ENTRY",
            "underlying": "RELIANCE",
        },
        {
            "id": "e1-strong",
            "at_ms": 1790829000000,
            "tier": "STRONG_WATCHLIST",
            "underlying": "TCS",
        },
    ])

    r = client.get("/config/gamma-move/history?days=60&limit=50")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2
    assert body["tier_counts"]["entry"] == 1
    assert body["tier_counts"]["strong_watchlist"] == 1
    assert {row["id"] for row in body["signals"]} == {
        "e1-entry", "e1-strong",
    }


def test_history_replay_endpoint_caches_reconstruction(client, monkeypatch):
    async def fake_replay(uid, day, cfg):
        assert uid
        assert day.isoformat() == "2026-10-01"
        return {
            "day": "2026-10-01",
            "source": "historical_replay",
            "resolution": "1minute_ohlcv_proxy",
            "watchlist": [],
            "signals": [{
                "id": "sig-r",
                "at_ms": 1790820000000,
                "state": "armed",
                "tier": "ENTRY",
            }],
            "scan": {"replay_resolution": "1minute_ohlcv_proxy"},
            "issue": "",
            "coverage": [],
            "complete": True,
        }

    monkeypatch.setattr(
        "app.services.gamma_move_historical.replay_day",
        fake_replay,
    )
    r = client.post(
        "/config/gamma-move/history/replay",
        json={"day": "2026-10-01"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["historical"] is True
    assert body["recorded"] is True
    assert body["source"] == "historical_replay"
    assert body["replay"]["resolution"] == "1minute_ohlcv_proxy"
    assert [row["id"] for row in body["candidates"]] == ["sig-r"]


def test_history_replay_refuses_today(client):
    today = client.get("/config/gamma-move/snapshot").json()["today"]
    r = client.post("/config/gamma-move/history/replay", json={"day": today})
    assert r.status_code == 422
    assert "completed day" in r.json()["detail"]


def test_snapshot_rejects_invalid_day_query(client):
    r = client.get("/config/gamma-move/snapshot?day=not-a-date")
    assert r.status_code == 422
    assert "invalid day" in r.json()["detail"]


def test_arm_requires_signal_id(client):
    assert client.post("/config/gamma-move/arm", json={}).status_code == 422


def test_old_candle_simulator_is_retired_instead_of_claiming_false_parity(client):
    r = client.post("/config/gamma-move/simulate", json={"symbols": ["RELIANCE"], "days": 5})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "tick-driven" in detail
    assert "cannot reproduce" in detail


def test_scan_and_arm_are_refused_while_market_replay_owns_the_board(client, monkeypatch):
    from app.services.simulation import SimulationRunner
    monkeypatch.setattr(
        SimulationRunner, "has_session_view",
        property(lambda self: True),
    )
    scan = client.post("/config/gamma-move/scan")
    assert scan.status_code == 409
    assert "replay" in scan.json()["detail"]
    arm = client.post("/config/gamma-move/arm", json={"signal_id": "x"})
    assert arm.status_code == 409
    assert "replay" in arm.json()["detail"]


def test_old_gamma_move_fields_are_not_published(client):
    body = client.get("/config/gamma-move").json()
    cfg = body["config"]
    for old in (
        "level_proximity_pct", "min_oi_drop_pct", "volume_spike_mult",
        "min_price_gain_pct", "trigger_timeframe", "regime_multiplier",
        "stop_percent", "max_hold_days", "descale_factor",
    ):
        assert old not in cfg


def test_incomplete_cached_replay_is_retried_without_force(client, monkeypatch):
    from app.services import gamma_move_history

    gamma_move_history.record_replay("default", {
        "day": "2026-10-01",
        "source": "historical_replay",
        "resolution": "1minute_ohlcv_proxy",
        "watchlist": [],
        "signals": [],
        "scan": {},
        "coverage": ["temporary history outage"],
        "issue": "all selected Gamma-Unwind contracts failed historical replay",
        "complete": False,
    })

    before = client.get("/config/gamma-move/snapshot?day=2026-10-01")
    assert before.status_code == 200
    payload = before.json()
    assert payload["recorded"] is False
    assert payload["replay_available"] is True

    calls = []

    async def replay(uid, day, cfg):
        calls.append((uid, day.isoformat()))
        return {
            "day": day.isoformat(),
            "source": "historical_replay",
            "resolution": "1minute_ohlcv_proxy",
            "watchlist": [],
            "signals": [{
                "id": "retry-signal",
                "at_ms": 1790820000000,
                "state": "armed",
                "tier": "ENTRY",
            }],
            "scan": {"replay_resolution": "1minute_ohlcv_proxy"},
            "coverage": [],
            "issue": "",
            "complete": True,
        }

    monkeypatch.setattr("app.services.gamma_move_historical.replay_day", replay)

    response = client.post(
        "/config/gamma-move/history/replay",
        json={"day": "2026-10-01"},
    )

    assert response.status_code == 200
    assert calls == [("default", "2026-10-01")]
    body = response.json()
    assert body["recorded"] is True
    assert [row["id"] for row in body["candidates"]] == ["retry-signal"]
