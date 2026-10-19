"""Read-only evidence transport using the API's audited HTTP/WS dependencies."""

from __future__ import annotations
import asyncio
import csv
from datetime import date, datetime, timezone
import io
import threading
import httpx


class ReadOnlyKite:
    def __init__(self, api_key, *, transport=None):
        self.api_key = api_key
        self.client = httpx.Client(
            base_url="https://api.kite.trade", timeout=20, transport=transport
        )

    def set_access_token(self, token):
        self.client.headers.update(
            {"Authorization": f"token {self.api_key}:{token}", "X-Kite-Version": "3"}
        )

    def instruments(self, exchange):
        response = self.client.get("/instruments/" + exchange)
        response.raise_for_status()
        rows = list(csv.DictReader(io.StringIO(response.text)))
        for row in rows:
            for key in ("instrument_token", "exchange_token", "lot_size"):
                if row.get(key):
                    row[key] = int(row[key])
            for key in ("strike", "tick_size", "last_price"):
                if row.get(key):
                    row[key] = float(row[key])
            if row.get("expiry"):
                row["expiry"] = date.fromisoformat(row["expiry"])
        return rows

    def ltp(self, instruments):
        response = self.client.get("/quote/ltp", params=[("i", i) for i in instruments])
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "success":
            raise RuntimeError("Broker quote read failed")
        return payload["data"]


class EvidenceTicker:
    """Synchronous collector facade around the production reconnecting ticker."""

    MODE_FULL = "full"

    def __init__(self, api_key, access_token):
        self.api_key = api_key
        self.access_token = access_token
        self.on_ticks = self.on_connect = self.on_close = self.on_reconnect = None
        self._loop = None
        self._client = None
        self._thread = None
        self._ready = threading.Event()
        self._stop = None

    def connect(self, threaded=True):
        if not threaded:
            raise ValueError("Evidence collection requires an isolated thread")
        self._thread = threading.Thread(
            target=lambda: asyncio.run(self._run()),
            name="evidence-market-feed",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(5):
            raise RuntimeError("Evidence transport did not start")

    async def _run(self):
        from app.services.exchanges.kite.ticker import KiteTicker

        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()

        async def ticks(values):
            converted = []
            for value in values:
                row = dict(value)
                for key in ("exchange_timestamp", "last_trade_time"):
                    stamp = row.get(key)
                    if isinstance(stamp, (int, float)) and stamp > 0:
                        row[key] = datetime.fromtimestamp(stamp, timezone.utc)
                converted.append(row)
            if self.on_ticks:
                self.on_ticks(self, converted)

        async def status(value):
            if value.get("connected"):
                if self.on_connect:
                    self.on_connect(self, {})
            elif self.on_close:
                self.on_close(self, 0, "production transport disconnected")

        self._client = KiteTicker(
            self.api_key, self.access_token, on_ticks=ticks, on_status=status
        )
        await self._client.start()
        self._ready.set()
        try:
            await self._stop.wait()
        finally:
            await self._client.stop()

    def subscribe(self, tokens):
        asyncio.run_coroutine_threadsafe(
            self._client.subscribe(tokens, mode=self.MODE_FULL), self._loop
        )

    def set_mode(self, mode, tokens):
        if mode != self.MODE_FULL:
            raise ValueError("Evidence collection requires FULL mode")

    def abort_transport(self):
        ws = self._client._ws if self._client else None
        if ws is None or getattr(ws, "transport", None) is None:
            return False
        self._loop.call_soon_threadsafe(ws.transport.abort)
        return True

    def close(self):
        if self._loop and self._stop:
            self._loop.call_soon_threadsafe(self._stop.set)
        if self._thread:
            self._thread.join(15)
            if self._thread.is_alive():
                raise RuntimeError("Evidence transport did not stop")
