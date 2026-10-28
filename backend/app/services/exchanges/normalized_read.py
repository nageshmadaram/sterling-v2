"""Read-only normalized Kite facade; never exposes an order-writing method.

Wraps existing authenticated KiteClient/TradingExchangeAdapter read methods.
Existing Sterling execution authority and broker transports remain unchanged.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol, Sequence
from zoneinfo import ZoneInfo

from app.schemas.broker_read import (
    AccountInfo, BrokerHealth, DepthLevel, FillObservation, Funds, HistoricalCandle,
    Holding, MarketDepth, OptionChainObservation, OrderObservation, Position, Quote,
)

_IST = ZoneInfo("Asia/Kolkata")


class BrokerReadFailure(RuntimeError):
    """Sanitized, stable error code; never includes broker payloads or credentials."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _number(value: Any, *, required: bool = False) -> Decimal | None:
    if value is None or value == "":
        if required:
            raise BrokerReadFailure("MISSING_REQUIRED_NUMBER")
        return None
    try:
        amount = Decimal(str(value))
        if not amount.is_finite():
            raise ValueError("non-finite")
        return amount
    except (ValueError, TypeError, InvalidOperation):
        raise BrokerReadFailure("INVALID_BROKER_NUMBER") from None


def _required(data: dict, key: str) -> str:
    value = data.get(key)
    if value is None or not str(value).strip():
        raise BrokerReadFailure("MISSING_REQUIRED_FIELD")
    return str(value).strip()


def _timestamp(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, datetime):
            ts = value
        else:
            ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        # Kite sends Indian exchange-local timestamps without a UTC offset.
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_IST)
        return ts.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        raise BrokerReadFailure("INVALID_BROKER_TIMESTAMP") from None


def _object(value: Any) -> dict:
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if not isinstance(value, dict):
        raise BrokerReadFailure("INVALID_BROKER_PAYLOAD")
    return value


def _rows(value: Any) -> list[dict]:
    if not isinstance(value, list):
        raise BrokerReadFailure("INVALID_BROKER_PAYLOAD")
    return [_object(item) for item in value]


def _depth(symbol: str, raw: dict, ts: datetime | None) -> MarketDepth | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise BrokerReadFailure("INVALID_BROKER_DEPTH")

    def levels(name: str) -> tuple[DepthLevel, ...]:
        rows = _rows(raw.get(name, []))
        return tuple(DepthLevel(
            price=_number(r.get("price"), required=True),
            quantity=_number(r.get("quantity"), required=True),
            orders=int(r["orders"]) if r.get("orders") is not None else None,
        ) for r in rows)

    return MarketDepth(symbol=symbol, bids=levels("buy"), asks=levels("sell"),
                       observed_at_utc=ts)


def normalize_kite_quotes(payload: dict, symbols: Sequence[str], *,
                          fetched_at_utc: datetime, stale_after_ms: int) -> tuple[Quote, ...]:
    """No quote timestamp => stale/unknown, never a presumed current price."""
    if stale_after_ms <= 0 or fetched_at_utc.tzinfo is None:
        raise ValueError("positive freshness threshold and timezone-aware clock required")
    if not isinstance(payload, dict):
        raise BrokerReadFailure("INVALID_BROKER_PAYLOAD")
    items = []
    for symbol in symbols:
        if not isinstance(symbol, str) or ":" not in symbol:
            raise ValueError("Kite quotes need EXCHANGE:SYMBOL")
        item = _object(payload.get(symbol))
        ts = _timestamp(item.get("timestamp"))
        age_ms = (fetched_at_utc.astimezone(timezone.utc) - ts).total_seconds() * 1000 if ts else None
        # Future timestamps beyond clock-skew tolerance are not trustworthy.
        quality = "unknown" if age_ms is None or age_ms < -1000 else (
            "stale" if age_ms > stale_after_ms else "fresh"
        )
        items.append(Quote(
            symbol=symbol, last_price=_number(item.get("last_price"), required=True),
            observed_at_utc=ts, fetched_at_utc=fetched_at_utc,
            stale=quality != "fresh", quality=quality,
            depth=_depth(symbol, item.get("depth"), ts),
        ))
    return tuple(items)


class KiteReadContract(Protocol):
    async def get_profile(self) -> dict: ...
    async def get_margins(self, segment: str | None = None) -> dict: ...
    async def get_positions_raw(self) -> dict: ...
    async def get_holdings(self) -> list: ...
    async def get_orders(self) -> list: ...
    async def get_trades(self) -> list: ...
    async def get_quote(self, instruments: Sequence[str]) -> dict: ...
    async def get_candles(self, instrument, resolution: str, limit: int = 200): ...
    async def get_option_chain(self, instrument): ...
    async def test_connection(self) -> bool: ...


class NormalizedKiteReader:
    """Observation-only service. Does not implement place/modify/cancel/flatten."""

    __slots__ = ("_client", "_paper")

    def __init__(self, client: KiteReadContract, *, paper: bool):
        self._client = client
        self._paper = bool(paper)

    async def health(self) -> BrokerHealth:
        now = datetime.now(timezone.utc)
        if self._paper:
            return BrokerHealth(status="simulation", authenticated=False, observed_at_utc=now)
        try:
            healthy = bool(await self._client.test_connection())
        except Exception:
            healthy = False
        return BrokerHealth(status="healthy" if healthy else "unavailable",
                            authenticated=healthy, observed_at_utc=now)

    async def account(self) -> AccountInfo:
        try:
            raw = _object(await self._client.get_profile())
            return AccountInfo(account_id=_required(raw, "user_id"),
                               user_name=raw.get("user_name"), email=raw.get("email"))
        except BrokerReadFailure:
            raise
        except Exception:
            raise BrokerReadFailure("BROKER_ACCOUNT_UNAVAILABLE") from None

    async def funds(self) -> tuple[Funds, ...]:
        try:
            raw = _object(await self._client.get_margins())
            return tuple(Funds(
                segment=segment,
                available_inr=_number(_object(block.get("available", {})).get("live_balance")),
                used_inr=_number(_object(block.get("utilised", {})).get("debits")),
                net_inr=_number(block.get("net")),
            ) for segment, block in raw.items() if isinstance(block, dict))
        except BrokerReadFailure:
            raise
        except Exception:
            raise BrokerReadFailure("BROKER_FUNDS_UNAVAILABLE") from None

    async def positions(self) -> tuple[Position, ...]:
        try:
            raw = _object(await self._client.get_positions_raw())
            return tuple(Position(
                exchange=_required(r, "exchange"),
                symbol=_required(r, "tradingsymbol"),
                product=r.get("product"),
                net_quantity=_number(r.get("quantity"), required=True),
                average_price=_number(r.get("average_price")),
                last_price=_number(r.get("last_price")),
                pnl_inr=_number(r.get("pnl")),
            ) for r in _rows(raw.get("net")))
        except BrokerReadFailure:
            raise
        except Exception:
            raise BrokerReadFailure("BROKER_POSITIONS_UNAVAILABLE") from None

    async def holdings(self) -> tuple[Holding, ...]:
        try:
            return tuple(Holding(
                exchange=_required(r, "exchange"),
                symbol=_required(r, "tradingsymbol"),
                quantity=_number(r.get("quantity"), required=True),
                average_price=_number(r.get("average_price")),
                last_price=_number(r.get("last_price")),
            ) for r in _rows(await self._client.get_holdings()))
        except BrokerReadFailure:
            raise
        except Exception:
            raise BrokerReadFailure("BROKER_HOLDINGS_UNAVAILABLE") from None

    async def orders(self) -> tuple[OrderObservation, ...]:
        try:
            return tuple(OrderObservation(
                order_id=_required(r, "order_id"),
                exchange=_required(r, "exchange"),
                symbol=_required(r, "tradingsymbol"),
                status=_required(r, "status").lower(),
                side=str(r["transaction_type"]).upper() if r.get("transaction_type") else None,
                product=r.get("product"),
                quantity=_number(r.get("quantity"), required=True),
                filled_quantity=_number(r.get("filled_quantity")),
                price=_number(r.get("price")),
                observed_at_utc=_timestamp(r.get("order_timestamp")),
            ) for r in _rows(await self._client.get_orders()))
        except BrokerReadFailure:
            raise
        except Exception:
            raise BrokerReadFailure("BROKER_ORDERS_UNAVAILABLE") from None

    async def fills(self) -> tuple[FillObservation, ...]:
        try:
            return tuple(FillObservation(
                fill_id=_required(r, "trade_id"),
                order_id=_required(r, "order_id"),
                symbol=_required(r, "tradingsymbol"),
                quantity=_number(r.get("quantity"), required=True),
                price=_number(r.get("average_price", r.get("price")), required=True),
                executed_at_utc=_timestamp(r.get("fill_timestamp")),
            ) for r in _rows(await self._client.get_trades()))
        except BrokerReadFailure:
            raise
        except Exception:
            raise BrokerReadFailure("BROKER_FILLS_UNAVAILABLE") from None

    async def quotes(self, symbols: Sequence[str], *, stale_after_ms: int = 5000,
                     fetched_at_utc: datetime | None = None) -> tuple[Quote, ...]:
        symbols = tuple(symbols)
        if not symbols or len(symbols) > 50:
            raise ValueError("request between 1 and 50 symbols")
        if any(not isinstance(s, str) or ":" not in s for s in symbols):
            raise ValueError("Kite quotes need EXCHANGE:SYMBOL")
        try:
            raw = await self._client.get_quote(symbols)
        except Exception:
            raise BrokerReadFailure("BROKER_QUOTES_UNAVAILABLE") from None
        return normalize_kite_quotes(raw, symbols,
                                     fetched_at_utc=fetched_at_utc or datetime.now(timezone.utc),
                                     stale_after_ms=stale_after_ms)

    async def depth(self, symbols: Sequence[str]) -> tuple[MarketDepth | None, ...]:
        return tuple(q.depth for q in await self.quotes(symbols))

    async def candles(self, instrument, timeframe: str, *, limit: int = 200) -> tuple[HistoricalCandle, ...]:
        if not 1 <= limit <= 5000:
            raise ValueError("invalid historical bar count")
        try:
            raw = await self._client.get_candles(instrument, timeframe, limit)
            rows = [(_object(r)) for r in raw]
            return tuple(HistoricalCandle(
                symbol=instrument.underlying, timeframe=timeframe,
                timestamp_ms=r["timestamp_ms"],
                open=_number(r.get("open"), required=True),
                high=_number(r.get("high"), required=True),
                low=_number(r.get("low"), required=True),
                close=_number(r.get("close"), required=True),
                volume=_number(r.get("volume"), required=True),
            ) for r in rows)
        except BrokerReadFailure:
            raise
        except Exception:
            raise BrokerReadFailure("BROKER_HISTORY_UNAVAILABLE") from None

    async def option_chain(self, instrument) -> tuple[OptionChainObservation, ...]:
        try:
            raw = await self._client.get_option_chain(instrument)
            rows = [_object(item) for item in raw]
            return tuple(OptionChainObservation(
                underlying=_required(r, "underlying"),
                strike=_number(r.get("strike"), required=True),
                expiry_date=_required(r, "expiry_date"),
                right={"call": "CE", "put": "PE"}[str(r["option_type"]).lower()],
                bid=_number(r.get("bid")),
                ask=_number(r.get("ask")),
                iv=_number(r.get("mark_iv")),
                open_interest=_number(r.get("open_interest")),
                delta=_number(r.get("delta")),
                # Existing schema uses default zero for absent Greeks; do not
                # report them as broker-observed unless explicit enrichment evidence exists.
                gamma=_number(r.get("gamma")) if r.get("greeks_enriched") else None,
                theta=_number(r.get("theta")) if r.get("greeks_enriched") else None,
                vega=_number(r.get("vega")) if r.get("greeks_enriched") else None,
            ) for r in rows)
        except BrokerReadFailure:
            raise
        except Exception:
            raise BrokerReadFailure("BROKER_OPTIONS_UNAVAILABLE") from None


def create_normalized_kite_reader(config) -> NormalizedKiteReader:
    """Reuse Sterling's existing, single broker-adapter factory."""
    if config.name.lower() != "zerodha":
        raise ValueError("read service currently supports only Zerodha/Kite")
    from app.services.exchanges.adapter_factory import create_account_adapter
    return NormalizedKiteReader(create_account_adapter(config), paper=config.is_paper)
