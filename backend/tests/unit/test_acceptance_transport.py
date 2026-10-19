import asyncio
from datetime import datetime, timezone, date
from types import SimpleNamespace
import threading
import httpx
from study.acceptance_transport import ReadOnlyKite, EvidenceTicker
from study.snapback_live_evidence_acceptance import _force_transport_disconnect


def test_read_only_rest_uses_auth_header_and_preserves_vendor_types():
    seen = []

    def respond(req):
        seen.append(req)
        assert req.method == "GET"
        assert req.headers["Authorization"] == "token synthetic-key:synthetic-token"
        assert "synthetic-token" not in str(req.url)
        if req.url.path.startswith("/instruments"):
            return httpx.Response(
                200,
                text="instrument_token,exchange_token,lot_size,strike,tick_size,expiry\n1,2,25,23000,0.05,2026-10-29\n",
            )
        return httpx.Response(
            200,
            json={"status": "success", "data": {"NSE:NIFTY 50": {"last_price": 23000}}},
        )

    client = ReadOnlyKite("synthetic-key", transport=httpx.MockTransport(respond))
    client.set_access_token("synthetic-token")
    row = client.instruments("NFO")[0]
    assert row["lot_size"] == 25 and row["expiry"] == date(2026, 10, 29)
    assert client.ltp(["NSE:NIFTY 50"])["NSE:NIFTY 50"]["last_price"] == 23000
    assert not hasattr(client, "place_order")
    client.client.close()


def test_evidence_facade_uses_production_retry_and_stops_its_thread(monkeypatch):
    from app.services.exchanges.kite import ticker as native

    subscribed = threading.Event()
    aborted = threading.Event()
    stopped = threading.Event()
    observed = []

    class FakeNative:
        def __init__(self, *args, on_ticks, on_status, **kwargs):
            self.ticks = on_ticks
            self.status = on_status
            self._ws = SimpleNamespace(transport=SimpleNamespace(abort=aborted.set))

        async def start(self):
            await self.status({"connected": True})
            await self.ticks(
                [
                    {
                        "instrument_token": 1,
                        "exchange_timestamp": 1700000000,
                        "last_trade_time": 1700000000,
                    }
                ]
            )

        async def subscribe(self, tokens, mode):
            assert tokens == [1] and mode == "full"
            subscribed.set()

        async def stop(self):
            stopped.set()

    monkeypatch.setattr(native, "KiteTicker", FakeNative)
    feed = EvidenceTicker("synthetic-key", "synthetic-token")
    feed.on_connect = lambda ws, response: ws.subscribe([1])
    feed.on_ticks = lambda ws, rows: observed.extend(rows)
    try:
        feed.connect(threaded=True)
        assert subscribed.wait(2)
        assert observed[0]["exchange_timestamp"] == datetime.fromtimestamp(
            1700000000, timezone.utc
        )
        ok, _ = _force_transport_disconnect(feed)
        assert ok and aborted.wait(2)
    finally:
        feed.close()
    assert stopped.is_set() and not feed._thread.is_alive()
