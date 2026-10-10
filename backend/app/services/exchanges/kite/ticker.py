"""
KiteTicker — Zerodha live market-data WebSocket (binary protocol).

Connects to ``wss://ws.kite.trade`` and decodes the big-endian binary tick stream
into dicts. Modes: ltp / quote / full (full includes 5-level market depth + OI).

The binary frame is: ``[uint16 num_packets][ (uint16 len)(len bytes) ... ]``.
Per-packet field layout depends on the byte length (8 = LTP, 28/32 = index
quote/full, 44 = quote, 184 = full). Prices are integers scaled by a
segment-dependent divisor (see :func:`constants.price_divisor`).

The pure parsing functions (:func:`split_packets`, :func:`parse_binary`) are
isolated for unit testing against captured fixtures.
"""
from __future__ import annotations

import asyncio
import json
import random
import struct
import time
from typing import Awaitable, Callable, Dict, List, Optional

from app.core.logging import get_logger

from . import constants as K

log = get_logger(__name__)

OnTicks = Callable[[List[dict]], Awaitable[None]]
OnOrderUpdate = Callable[[dict], Awaitable[None]]
OnStatus = Callable[[dict], Awaitable[None]]

# Reconnect pacing. Module-level so tests can shrink them.
RECONNECT_BASE_S = 3.0
RECONNECT_MAX_S = 60.0
RECONNECT_JITTER = 0.2   # +/-20%: many processes must not retry in lock-step


# ─── Pure binary decoding ─────────────────────────────────────────────────────
def split_packets(data: bytes) -> List[bytes]:
    if len(data) < 2:
        return []
    num = struct.unpack(">H", data[0:2])[0]
    packets: List[bytes] = []
    j = 2
    for _ in range(num):
        if j + 2 > len(data):
            break
        plen = struct.unpack(">H", data[j:j + 2])[0]
        j += 2
        packets.append(data[j:j + plen])
        j += plen
    return packets


def _u32(b: bytes, o: int) -> int:
    return struct.unpack(">I", b[o:o + 4])[0]


def _i32(b: bytes, o: int) -> int:
    return struct.unpack(">i", b[o:o + 4])[0]


def _u16(b: bytes, o: int) -> int:
    return struct.unpack(">H", b[o:o + 2])[0]


def parse_packet(packet: bytes) -> dict:
    token = _u32(packet, 0)
    div = K.price_divisor(token)
    segment = token & 0xFF
    is_index = segment == K.SEGMENT_INDICES
    plen = len(packet)
    tick: dict = {"instrument_token": token, "tradable": not is_index, "mode": K.MODE_LTP}

    if plen == K.PACKET_LTP:
        tick["last_price"] = _u32(packet, 4) / div
        return tick

    if is_index and plen in (K.PACKET_INDEX_QUOTE, K.PACKET_INDEX_FULL):
        tick["mode"] = K.MODE_FULL if plen == K.PACKET_INDEX_FULL else K.MODE_QUOTE
        tick["last_price"] = _u32(packet, 4) / div
        tick["ohlc"] = {
            "high": _u32(packet, 8) / div, "low": _u32(packet, 12) / div,
            "open": _u32(packet, 16) / div, "close": _u32(packet, 20) / div,
        }
        tick["change"] = _i32(packet, 24) / div
        if plen == K.PACKET_INDEX_FULL:
            tick["exchange_timestamp"] = _u32(packet, 28)
        return tick

    if plen in (K.PACKET_QUOTE, K.PACKET_FULL):
        tick["mode"] = K.MODE_FULL if plen == K.PACKET_FULL else K.MODE_QUOTE
        tick["last_price"] = _u32(packet, 4) / div
        tick["last_traded_quantity"] = _u32(packet, 8)
        tick["average_traded_price"] = _u32(packet, 12) / div
        tick["volume_traded"] = _u32(packet, 16)
        tick["total_buy_quantity"] = _u32(packet, 20)
        tick["total_sell_quantity"] = _u32(packet, 24)
        tick["ohlc"] = {
            "open": _u32(packet, 28) / div, "high": _u32(packet, 32) / div,
            "low": _u32(packet, 36) / div, "close": _u32(packet, 40) / div,
        }
        if plen == K.PACKET_FULL:
            tick["last_trade_time"] = _u32(packet, 44)
            tick["oi"] = _u32(packet, 48)
            tick["oi_day_high"] = _u32(packet, 52)
            tick["oi_day_low"] = _u32(packet, 56)
            tick["exchange_timestamp"] = _u32(packet, 60)
            depth: Dict[str, list] = {"buy": [], "sell": []}
            for i in range(10):
                o = 64 + i * 12
                entry = {
                    "quantity": _u32(packet, o),
                    "price": _u32(packet, o + 4) / div,
                    "orders": _u16(packet, o + 8),
                }
                depth["buy" if i < 5 else "sell"].append(entry)
            tick["depth"] = depth
        return tick

    return tick  # unknown length — at least the token


def parse_binary(data: bytes) -> List[dict]:
    return [parse_packet(p) for p in split_packets(data) if len(p) >= K.PACKET_LTP]


# ─── Subscription message builders ────────────────────────────────────────────
def msg_subscribe(tokens: List[int]) -> str:
    return json.dumps({"a": "subscribe", "v": list(tokens)})


def msg_unsubscribe(tokens: List[int]) -> str:
    return json.dumps({"a": "unsubscribe", "v": list(tokens)})


def msg_mode(mode: str, tokens: List[int]) -> str:
    return json.dumps({"a": "mode", "v": [mode, list(tokens)]})


# ─── Connection manager ───────────────────────────────────────────────────────
class KiteTicker:
    def __init__(self, api_key: str, access_token: str, on_ticks: Optional[OnTicks] = None,
                 on_order_update: Optional[OnOrderUpdate] = None,
                 on_status: Optional[OnStatus] = None) -> None:
        self._api_key = api_key
        self._access_token = access_token
        self._on_ticks = on_ticks
        self._on_order_update = on_order_update
        # Called on every connected/disconnected transition so the UI learns the
        # broker feed is down without waiting for prices to age out.
        self._on_status = on_status
        # Cuts a reconnect backoff short (credential rotation). Created lazily:
        # asyncio primitives should be built inside the running loop.
        self._wake: Optional[asyncio.Event] = None
        self._reconnect_now = False
        self._subscribed: Dict[int, str] = {}   # token -> mode
        self._ticks: Dict[int, dict] = {}        # latest tick per token
        # When the last binary tick frame arrived. A connected socket that has
        # stopped delivering ticks looks identical to a healthy one without
        # this, and "connected" is not the question an operator is asking
        # before letting the system trade.
        self._last_tick_ms: int = 0
        self._active = False
        self._connected = False
        self._last_error: Optional[str] = None
        self._ws = None
        self._task: Optional[asyncio.Task] = None

    @property
    def is_active(self) -> bool:
        """Whether the stream is actually running.

        "Someone called ``start()``" is not the same as "the stream is running",
        and conflating them is how a dead feed stays dead. ``_active`` is only
        cleared by :meth:`stop` and by cancellation, so a ``_run()`` task that
        dies any other way leaves the flag set — and every caller then treats a
        ticker that will never produce another tick as healthy. Downstream, that
        showed up as every live price in the app quietly falling back to the
        30-second REST heartbeat while ``/ticker/status`` reported
        ``active: true``.
        """
        return self._active and self._task is not None and not self._task.done()

    @property
    def died(self) -> bool:
        """Started, never stopped, and yet not running. Restartable."""
        return self._active and self._task is not None and self._task.done()

    @property
    def last_error(self) -> Optional[str]:
        """Why the stream last dropped, so a caller can say more than "off"."""
        return self._last_error

    @property
    def connected(self) -> bool:
        return self._connected

    def ws_url(self) -> str:
        return f"{K.WS_URL}?api_key={self._api_key}&access_token={self._access_token}"

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._active = True
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._active = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        self._connected = False
        self._ws = None

    def credentials_match(self, api_key: str, access_token: str) -> bool:
        """Whether this ticker already holds exactly these credentials."""
        return api_key == self._api_key and access_token == self._access_token

    async def update_credentials(self, api_key: str, access_token: str) -> bool:
        """Adopt a rotated session without losing the subscription set.

        Kite access tokens die every morning and are re-issued on login. A ticker
        keeps the token it was built with, so after a re-login it would retry the
        dead token forever (HTTP 403 at the handshake) while ``ensure()`` kept
        handing it back as "active". The reconnect loop reads the URL on every
        attempt, so swapping the credentials and dropping the current socket is
        enough; ``_subscribed`` is replayed by ``_resubscribe_all`` on connect.

        Returns True when something actually changed.
        """
        if self.credentials_match(api_key, access_token):
            return False
        self._api_key = api_key
        self._access_token = access_token
        self._last_error = None
        self._reconnect_now = True
        if self._wake is not None:
            self._wake.set()          # abandon any backoff sleep in progress
        ws = self._ws
        if self._connected and ws is not None:
            try:
                await ws.close()      # ends the read loop; _run reconnects with the new URL
            except Exception as exc:  # noqa: BLE001
                log.debug("KiteTicker close during credential rotation failed: %s", exc)
        return True

    async def _send_quietly(self, *frames: str) -> None:
        """Best-effort control-frame send.

        ``_subscribed`` is the source of truth and is replayed on every connect,
        so a send that races a dying socket must not fail the caller (a strategy
        that just registered its stop token) -- the reconnect delivers it.
        """
        ws = self._ws
        if not (self._connected and ws is not None):
            return
        try:
            for frame in frames:
                await ws.send(frame)
        except Exception as exc:  # noqa: BLE001
            log.warning("KiteTicker control frame not sent (%s) -- replayed on reconnect", exc)

    async def subscribe(self, tokens: List[int], mode: str = K.MODE_QUOTE) -> None:
        # One websocket is shared by charts and multiple strategy stop monitors.
        # A later LTP request must not downgrade a token already in FULL mode:
        # the other owner may require bid/ask depth for an exit/risk decision.
        priority = {K.MODE_LTP: 0, K.MODE_QUOTE: 1, K.MODE_FULL: 2}
        if mode not in priority:
            raise ValueError(f"invalid Kite subscription mode: {mode!r}")
        by_mode: Dict[str, List[int]] = {}
        for raw in tokens:
            token = int(raw)
            prior = self._subscribed.get(token)
            effective = prior if prior in priority and priority[prior] > priority[mode] else mode
            self._subscribed[token] = effective
            by_mode.setdefault(effective, []).append(token)
        if tokens:
            await self._send_quietly(
                msg_subscribe(tokens),
                *[msg_mode(effective, group) for effective, group in by_mode.items()],
            )

    async def unsubscribe(self, tokens: List[int]) -> None:
        for t in tokens:
            self._subscribed.pop(int(t), None)
            self._ticks.pop(int(t), None)
        await self._send_quietly(msg_unsubscribe(tokens))

    def snapshot(self) -> List[dict]:
        return list(self._ticks.values())

    def status(self) -> dict:
        return {
            # `active` now means running, not merely started. `started` keeps the
            # raw flag so a dead task is visible rather than indistinguishable
            # from a healthy one.
            "active": self.is_active,
            "started": self._active,
            "died": self.died,
            "last_error": self._last_error,
            "connected": self._connected,
            "subscribed": sorted(self._subscribed.keys()),
            "tick_count": len(self._ticks),
            # 0 means no tick has ever arrived on this socket — not "just now".
            "last_tick_ms": self._last_tick_ms,
        }

    async def _resubscribe_all(self) -> None:
        # group tokens by mode and (re)subscribe after a (re)connect
        by_mode: Dict[str, List[int]] = {}
        for tok, mode in self._subscribed.items():
            by_mode.setdefault(mode, []).append(tok)
        for mode, toks in by_mode.items():
            await self._ws.send(msg_subscribe(toks))
            await self._ws.send(msg_mode(mode, toks))

    async def _emit_status(self) -> None:
        if self._on_status is None:
            return
        try:
            await self._on_status(self.status())
        except Exception as exc:  # noqa: BLE001 - UI delivery never decides stream health
            log.debug("KiteTicker on_status callback error: %s", exc)

    async def _mark_disconnected(self) -> None:
        was_connected = self._connected
        self._connected = False
        self._ws = None
        # A cached tick from before the drop is not a current price.
        self._ticks.clear()
        if was_connected:
            await self._emit_status()

    async def _sleep_before_retry(self, seconds: float) -> None:
        """Back off, but wake early when credentials rotate."""
        wake = self._wake
        if wake is None:
            await asyncio.sleep(seconds)
            return
        wake.clear()
        try:
            await asyncio.wait_for(wake.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
        self._reconnect_now = False

    async def _run(self) -> None:
        import websockets  # local import — only when WS is active
        if self._wake is None:
            self._wake = asyncio.Event()
        delay = RECONNECT_BASE_S
        while self._active:
            reason: Optional[str] = None
            try:
                async with websockets.connect(
                    self.ws_url(), ping_interval=20, ping_timeout=10,
                    close_timeout=5, open_timeout=10, max_size=None,
                ) as ws:
                    self._ws = ws
                    self._connected = True
                    delay = RECONNECT_BASE_S
                    log.info("KiteTicker connected (%d instruments)", len(self._subscribed))
                    if self._subscribed:
                        await self._resubscribe_all()
                    await self._emit_status()
                    async for raw in ws:
                        await self._on_message(raw)
                # The server ended the stream without an error (clean close).
                # That used to fall straight back into connect() with no pause
                # and with `_connected` still True: a hot reconnect loop, and
                # subscribe() sending into a closed socket.
                reason = "closed by server"
            except asyncio.CancelledError:
                self._connected = False
                self._ws = None
                self._active = False
                raise
            except Exception as exc:
                if not self._active:
                    self._connected = False
                    self._ws = None
                    break
                reason = f"{type(exc).__name__}: {exc}"[:200]
            await self._mark_disconnected()
            if not self._active:
                break
            # WARNING, not debug. Every live price in the app degrades to the
            # slow REST heartbeat while this is down, and at debug level that
            # happened invisibly.
            self._last_error = (reason or "disconnected")[:200]
            if self._reconnect_now:
                # Credentials rotated: the old socket was dropped on purpose, so
                # there is nothing to back off from.
                self._reconnect_now = False
                delay = RECONNECT_BASE_S
                log.info("KiteTicker reconnecting immediately with rotated credentials")
                continue
            pause = delay * random.uniform(1.0 - RECONNECT_JITTER, 1.0 + RECONNECT_JITTER)
            log.warning("KiteTicker disconnected (%s) — reconnecting in %.1fs", reason, pause)
            await self._sleep_before_retry(pause)
            delay = min(delay * 2, RECONNECT_MAX_S)
        self._connected = False
        self._ws = None

    async def _on_message(self, raw) -> None:
        if isinstance(raw, (bytes, bytearray)):
            if len(raw) <= 1:   # 1-byte heartbeat
                return
            ticks = parse_binary(bytes(raw))
            for t in ticks:
                self._ticks[t["instrument_token"]] = t
            if ticks:
                self._last_tick_ms = int(time.time() * 1000)
            if ticks and self._on_ticks:
                try:
                    await self._on_ticks(ticks)
                except Exception as exc:
                    log.debug("KiteTicker on_ticks callback error: %s", exc)
            return
        # Text frames carry order/trade postbacks + server messages. Kite sends
        # ``{"type": "order", "data": {...}}`` on every order-state change — fan it
        # out so the UI updates fills/cancels live (no second public socket).
        await self._handle_text_frame(raw)

    async def _handle_text_frame(self, raw) -> None:
        if not self._on_order_update:
            return
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        if isinstance(msg, dict) and msg.get("type") == "order" and isinstance(msg.get("data"), dict):
            try:
                await self._on_order_update(msg["data"])
            except Exception as exc:
                log.debug("KiteTicker on_order_update callback error: %s", exc)
