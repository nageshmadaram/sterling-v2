"""Offline regression evidence. Broker writes use mocks or MockTransport only."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from tests.engines.sterling_kite_engine.test_live_manual_execution import live_manual


@pytest.mark.parametrize("source", ["spot", "derivatives"])
@pytest.mark.parametrize("mode,minutes", [
    ("ultra_scalping", 1), ("scalping", 1), ("scalping", 3),
    ("scalping", 5), ("intraday", 5), ("intraday", 15),
    ("intraday", 30), ("overnight", 30), ("overnight", 60), ("swing", 60),
])
def test_fresh_completed_candle_admission(monkeypatch, mode, minutes, source):
    from app.services.kite_engine import service

    now = datetime.fromisoformat("2026-10-07T11:00:30+05:30")

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)

    monkeypatch.setattr(service, "datetime", Clock)
    monkeypatch.setattr(service.market_hours, "entry_block_reason", lambda **kw: "")
    row = SimpleNamespace(source=source, horizon_mode=mode,
                          signal_timeframe_minutes=minutes,
                          timestamp_ms=int((now-timedelta(minutes=minutes, seconds=30)).timestamp()*1000))
    reason = service.entry_data_block_reason(row, exchange="NFO", buffer_minutes=5)
    assert reason == ""
    print(f"FRESH_CLOSED_BAR mode={mode} timeframe={minutes}m observed={reason or 'ACCEPTED'}")


@pytest.mark.parametrize("variety", ["regular", "amo"])
@pytest.mark.asyncio
async def test_stale_manual_signal_never_submits_or_becomes_amo(live_manual, monkeypatch, tmp_path, variety):
    from app.services.kite_engine import service
    from app.services import db
    from tests.conftest import reconciled_control_plane

    monkeypatch.setattr(db, "_DB_PATH", str(tmp_path / "qa-manual-test.db"))
    db.init()
    reconciled_control_plane()
    monkeypatch.setattr(service.positions, "_positions", {})
    monkeypatch.setattr(service.positions, "_unreadable", {})

    client, _ = live_manual
    monkeypatch.setattr(service, "entry_data_block_reason", lambda *a, **kw: "stale_or_unclosed_signal")
    result = await service.place_manual_order("u", "OPT", "BUY", 50, variety=variety)
    assert result["status"] == "blocked", result
    assert result["reason"] == "stale_or_closed_manual_signal"
    client.place_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_offline_amo_broker_wire_and_cancel(monkeypatch):
    """Real serializer and HTTP adapter; all replies are synthetic."""
    import json
    import socket
    from urllib.parse import parse_qs
    import httpx
    from tests.test_kite_orders import _client
    from app.services.snapback_family_mode import canonical_broker_capability, INTENT_CANCEL

    def blocked(*args, **kwargs):
        raise AssertionError("QA must never open a network socket")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    trace = []

    def handler(request):
        body = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        trace.append({"method": request.method, "path": request.url.path, "body": body})
        return httpx.Response(200, json={"status": "success", "data": {"order_id": "QA-AMO-1"}})

    client = _client(handler)
    try:
        with canonical_broker_capability("qa-amo-wire"):
            reply = await client.place_order("NFO:NIFTY26OCT25000CE", "buy", 75,
                order_type="limit_order", limit_price=100.05, product="NRML", variety="amo", allow_amo=True)
        with canonical_broker_capability("qa-amo-cancel", intent=INTENT_CANCEL):
            await client.cancel_order(reply["order_id"], variety="amo")
        assert [r["path"] for r in trace] == ["/orders/amo", "/orders/amo/QA-AMO-1"]
        assert trace[0]["body"]["quantity"] == "75"
        assert trace[0]["body"]["price"] == "100.05"
        assert trace[0]["body"]["product"] == "NRML"
        assert trace[1]["method"] == "DELETE"
        print("OFFLINE_AMO_WIRE " + json.dumps(trace, sort_keys=True))
    finally:
        await client.close()


@pytest.mark.parametrize("offset,mode,minutes,expected", [
    (0, "intraday", 15, "stale_or_unclosed_signal"),
    (-1, "intraday", 15, "stale_or_unclosed_signal"),
    (1440, "intraday", 15, "stale_or_unclosed_signal"),
    (30, "ultra_scalping", 15, "invalid_signal_timeframe_or_timestamp"),
    (3, "ultra_scalping", 1, "stale_or_unclosed_signal"),
])
def test_invalid_signal_age_and_interval_refused(monkeypatch, offset, mode, minutes, expected):
    from app.services.kite_engine import service
    now = datetime.fromisoformat("2026-10-07T11:00:30+05:30")
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now
    monkeypatch.setattr(service, "datetime", Clock)
    monkeypatch.setattr(service.market_hours, "entry_block_reason", lambda **kw: "")
    row = SimpleNamespace(source="derivatives", horizon_mode=mode,
                          signal_timeframe_minutes=minutes,
                          timestamp_ms=int((now-timedelta(minutes=offset)).timestamp()*1000))
    assert service.entry_data_block_reason(row, exchange="NFO", buffer_minutes=5) == expected


def test_runtime_identity_is_frozen_and_reports_drift(monkeypatch):
    from app.core import runtime_identity as identity
    monkeypatch.setattr(identity, "_BOOT", None)
    monkeypatch.setattr(identity, "checkout_sha", lambda: "a"*40)
    monkeypatch.setattr(identity, "source_fingerprint", lambda: "initial")
    monkeypatch.setattr(identity, "source_dirty", lambda: False)
    monkeypatch.setenv("STERLING_RUNTIME_SHA", "b"*40)
    boot = identity.capture_startup()
    monkeypatch.setattr(identity, "source_fingerprint", lambda: "edited")
    assert identity.capture_startup() == boot
    report = identity.diagnostics("c"*40, "old-api")
    assert report["compatible"] is False
    assert set(report["blockers"]) == {"backend_source_changed_since_start", "declared_backend_release_mismatch",
                                      "frontend_backend_release_mismatch", "frontend_backend_contract_mismatch"}
