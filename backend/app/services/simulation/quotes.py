"""Where a virtual exchange gets its prices: the OWNER's real Kite reads, never an invention.

Simulation trades against the real market. A price is either

* a fresh websocket tick for the instrument (full mode, with depth) from the owner's live
  ticker when that stream is healthy, or
* a REST ``/quote`` read through the owner's real client, held for a short TTL so a burst of
  evaluations asks the broker once. The real client already passes every REST call through
  the process-wide Kite rate limiter, so Simulation shares the live app's request budget.

If neither can be had, :class:`MarketDataUnavailable` is raised and the caller does nothing
(an order is refused, a trigger waits). It subclasses ``PermissionError`` on purpose: the
canonical execution path reads that as "refused before anything was sent", which is exactly
what happened.
"""
from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from app.core.logging import get_logger
from app.services.exchanges.kite.errors import KiteInputError
from app.services.simulation.identity import owner_uid

log = get_logger(__name__)
_IST = timezone(timedelta(hours=5, minutes=30))
_QUOTE_CHUNK = 200

RealClientProvider = Callable[[], Union[Any, Awaitable[Any]]]
TickReader = Callable[[str], Optional[Dict[int, dict]]]


class MarketDataUnavailable(PermissionError):
    """No real price could be obtained. Fail closed: nothing is filled, nothing is invented."""


class InstrumentNotFound(KiteInputError):
    """The contract is not in the exchange's instrument master."""

    def __init__(self, exchange: str, symbol: str) -> None:
        super().__init__(f"Invalid instrument {exchange}:{symbol}", error_type="InputException", status_code=400)


@dataclass(frozen=True)
class Book:
    """The touch of one instrument at one moment, from real data."""

    exchange: str
    symbol: str
    ltp: float
    bid: float
    ask: float
    bid_qty: int
    ask_qty: int
    fetched_at: float
    source: str                      # "rest" | "ws"
    quote_ts: Optional[float] = None
    token: int = 0

    @property
    def key(self) -> str:
        return f"{self.exchange}:{self.symbol}"


@dataclass(frozen=True)
class InstrumentMeta:
    exchange: str
    symbol: str
    token: int
    tick_size: float
    lot_size: int
    instrument_type: str
    name: str = ""
    strike: float = 0.0
    expiry: str = ""
    segment: str = ""

    def as_meta(self) -> dict:
        return {"tradingsymbol": self.symbol, "exchange": self.exchange, "instrument_type": self.instrument_type,
                "name": self.name, "strike": self.strike, "expiry": self.expiry, "tick_size": self.tick_size,
                "lot_size": self.lot_size, "segment": self.segment}


def split_key(key: str) -> Tuple[str, str]:
    exchange, _, symbol = str(key).partition(":")
    return exchange.upper(), symbol


def _num(value) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return out if out == out and out not in (float("inf"), float("-inf")) else 0.0


def _first_level(levels) -> Tuple[float, int]:
    for level in levels or []:
        price = _num((level or {}).get("price"))
        if price > 0:
            return price, int(_num((level or {}).get("quantity")))
    return 0.0, 0


def _parse_ts(raw) -> Optional[float]:
    if not raw:
        return None
    try:
        when = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw).strip())
    except ValueError:
        return None
    return (when if when.tzinfo else when.replace(tzinfo=_IST)).timestamp()


def book_from_quote(exchange: str, symbol: str, payload: Optional[dict], *, now: float, source: str = "rest") -> Optional[Book]:
    """A :class:`Book` from a Kite quote/tick payload; None when it carries no usable price."""
    if not isinstance(payload, dict):
        return None
    depth = payload.get("depth") or {}
    bid, bid_qty = _first_level(depth.get("buy"))
    ask, ask_qty = _first_level(depth.get("sell"))
    ltp = _num(payload.get("last_price"))
    if ltp <= 0 and not (bid > 0 and ask > 0):
        return None
    if ltp <= 0:
        ltp = round((bid + ask) / 2, 4)
    return Book(exchange=exchange, symbol=symbol, ltp=ltp, bid=bid, ask=ask, bid_qty=bid_qty, ask_qty=ask_qty,
                fetched_at=now, source=source,
                quote_ts=_parse_ts(payload.get("timestamp") or payload.get("last_trade_time")),
                token=int(_num(payload.get("instrument_token"))))


async def resolve_real_client(provider: RealClientProvider, *, owner: str = "") -> Any:
    """The owner's real Kite client from ``provider``, or MarketDataUnavailable.

    Refuses anything that is itself simulated and anything that belongs to another owner, so a
    miswired provider can never feed one tenant another's session (or loop back on itself)."""
    try:
        value = provider()
        if inspect.isawaitable(value):
            value = await value
    except MarketDataUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - whatever the provider says, the answer is "no data"
        raise MarketDataUnavailable(f"The owner's Kite session is unavailable: {exc}") from exc
    if value is None:
        raise MarketDataUnavailable("The owner's Kite session is unavailable: no real client")
    if getattr(value, "_is_simulated", False):
        raise MarketDataUnavailable("A simulated client cannot be the market-data source of a simulation")
    bound = str(getattr(value, "_owner_user_id", "") or "")
    if owner and bound and owner_uid(bound) != owner_uid(owner):
        raise MarketDataUnavailable("The real client belongs to a different owner")
    return value


def default_tick_reader(max_age_s: float = 3.0) -> TickReader:
    """Ticks of the owner's live websocket, only while that stream is demonstrably flowing."""
    def read(owner: str) -> Optional[Dict[int, dict]]:
        try:
            from app.services.exchanges.kite import ticker_manager
            ticker = getattr(ticker_manager, "_tickers", {}).get(owner_uid(owner))
            if ticker is None or not ticker.is_active or not ticker.connected:
                return None
            if time.time() * 1000 - int(ticker.status().get("last_tick_ms") or 0) > max_age_s * 1000:
                return None
            return {int(t.get("instrument_token") or 0): t for t in ticker.snapshot()}
        except Exception as exc:  # noqa: BLE001
            log.debug("sim quotes: websocket ticks unavailable: %s", exc)
            return None
    return read


class QuoteSource:
    """Real prices and instrument facts for one owner's virtual exchange."""

    def __init__(self, real_client_provider: RealClientProvider, *, owner: str = "", ttl_s: float = 1.0,
                 clock: Callable[[], float] = time.monotonic, tick_reader: Optional[TickReader] = None,
                 use_websocket: bool = True, max_quote_age_s: Optional[float] = None) -> None:
        self._provider = real_client_provider
        self.owner = owner_uid(owner) if owner else ""
        self.ttl_s = float(ttl_s)
        self._clock = clock
        self._tick_reader = tick_reader if tick_reader is not None else (default_tick_reader() if use_websocket else None)
        self.max_quote_age_s = max_quote_age_s
        self._cache: Dict[str, Tuple[float, Book]] = {}
        self._tokens: Dict[str, int] = {}
        self._meta: Dict[Tuple[str, str], InstrumentMeta] = {}
        self._index: Dict[str, Tuple[int, Dict[str, dict]]] = {}
        self._lock: Optional[asyncio.Lock] = None
        self._lock_loop: Optional[int] = None

    # ── the real client ───────────────────────────────────────────────────────────────
    async def real_client(self) -> Any:
        return await resolve_real_client(self._provider, owner=self.owner)

    def _fetch_lock(self) -> asyncio.Lock:
        loop = id(asyncio.get_running_loop())
        if self._lock is None or self._lock_loop != loop:
            self._lock, self._lock_loop = asyncio.Lock(), loop
        return self._lock

    def invalidate(self) -> None:
        self._cache.clear()

    # ── instruments ───────────────────────────────────────────────────────────────────
    def peek_instrument(self, exchange: str, symbol: str) -> Optional[InstrumentMeta]:
        return self._meta.get((exchange.upper(), symbol))

    async def instrument(self, exchange: str, symbol: str) -> InstrumentMeta:
        ex = exchange.upper()
        cached = self._meta.get((ex, symbol))
        if cached is not None:
            return cached
        real = await self.real_client()
        row = None
        try:
            cache = getattr(real, "_instruments", None)
            if cache is not None and hasattr(cache, "load"):
                rows = await cache.load(ex)
                held = self._index.get(ex)
                if held is None or held[0] != id(rows):
                    held = (id(rows), {str(r.get("tradingsymbol", "")).upper(): r for r in rows})
                    self._index[ex] = held
                row = held[1].get(symbol.upper())
            else:
                found = await real.search_instruments(symbol, ex, 5)
                row = next((r for r in found or [] if str(r.get("tradingsymbol", "")).upper() == symbol.upper()), None)
        except MarketDataUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MarketDataUnavailable(f"Instrument master unavailable for {ex}:{symbol}: {exc}") from exc
        if row is None:
            raise InstrumentNotFound(ex, symbol)
        tick = _num(row.get("tick_size")) or 0.05
        meta = InstrumentMeta(
            exchange=ex, symbol=str(row.get("tradingsymbol") or symbol), token=int(_num(row.get("instrument_token"))),
            tick_size=tick, lot_size=max(1, int(_num(row.get("lot_size")) or 1)),
            instrument_type=str(row.get("instrument_type") or "").upper(), name=str(row.get("name") or ""),
            strike=_num(row.get("strike")), expiry=str(row.get("expiry") or "")[:10],
            segment=str(row.get("segment") or ""))
        self._meta[(ex, symbol)] = meta
        if meta.token:
            self._tokens[f"{ex}:{symbol}"] = meta.token
        return meta

    # ── prices ────────────────────────────────────────────────────────────────────────
    def _ws_books(self, keys: Sequence[str], now: float) -> Dict[str, Book]:
        if self._tick_reader is None:
            return {}
        wanted = {self._tokens[k]: k for k in keys if self._tokens.get(k)}
        if not wanted:
            return {}
        ticks = self._tick_reader(self.owner)
        out: Dict[str, Book] = {}
        for token, key in wanted.items():
            tick = (ticks or {}).get(token)
            if not tick or not tick.get("depth"):
                continue
            exchange, symbol = split_key(key)
            book = book_from_quote(exchange, symbol, tick, now=now, source="ws")
            if book is not None and book.bid > 0 and book.ask > 0:
                out[key] = replace(book, token=token)
        return out

    def _stale(self, book: Book, wall_now: float) -> bool:
        return bool(self.max_quote_age_s and book.quote_ts and wall_now - book.quote_ts > self.max_quote_age_s)

    async def books(self, keys: Iterable[str], *, strict: bool = True) -> Dict[str, Book]:
        """Books for ``EXCHANGE:SYMBOL`` keys. ``strict`` raises if any key cannot be priced."""
        wanted = list(dict.fromkeys(str(k) for k in keys))
        now = self._clock()
        out: Dict[str, Book] = {}
        need: List[str] = []
        for key in wanted:
            hit = self._cache.get(key)
            if hit is not None and now - hit[0] <= self.ttl_s:
                out[key] = hit[1]
            else:
                need.append(key)
        if need:
            for key, book in self._ws_books(need, now).items():
                self._cache[key] = (now, book)
                out[key] = book
            need = [k for k in need if k not in out]
        if need:
            async with self._fetch_lock():
                now = self._clock()
                still: List[str] = []
                for key in need:                    # another caller may have filled it while we waited
                    hit = self._cache.get(key)
                    if hit is not None and now - hit[0] <= self.ttl_s:
                        out[key] = hit[1]
                    else:
                        still.append(key)
                for i in range(0, len(still), _QUOTE_CHUNK):
                    chunk = still[i:i + _QUOTE_CHUNK]
                    try:
                        real = await self.real_client()
                        payload = await real.get_quote(chunk)
                    except MarketDataUnavailable:
                        if strict:
                            raise
                        break                                # no owner session: nothing more can be priced
                    except Exception as exc:  # noqa: BLE001
                        if strict:
                            raise MarketDataUnavailable(f"Quote read failed: {exc}") from exc
                        log.debug("sim quotes: quote read failed: %s", exc)
                        continue
                    for key in chunk:
                        exchange, symbol = split_key(key)
                        book = book_from_quote(exchange, symbol, (payload or {}).get(key), now=now)
                        if book is None:
                            continue
                        if book.token:
                            self._tokens[key] = book.token
                        self._cache[key] = (now, book)
                        out[key] = book
        wall = time.time()
        for key in list(out):
            if self._stale(out[key], wall):
                del out[key]
        missing = [k for k in wanted if k not in out]
        if missing and strict:
            raise MarketDataUnavailable(f"No real price available for {', '.join(missing)}")
        return {k: out[k] for k in wanted if k in out}

    async def book(self, exchange: str, symbol: str) -> Book:
        key = f"{exchange.upper()}:{symbol}"
        return (await self.books([key]))[key]


__all__ = [
    "MarketDataUnavailable", "InstrumentNotFound", "Book", "InstrumentMeta", "QuoteSource",
    "book_from_quote", "resolve_real_client", "default_tick_reader", "split_key",
]
