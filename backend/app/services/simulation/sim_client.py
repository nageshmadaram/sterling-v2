"""SimulatedKiteClient: the Kite client of the virtual tenant. It cannot reach a real broker write.

The engines, the canonical execution path, the protective-stop code and the journal talk to "a
Kite client". For the Simulation tenant that client is this class. Its surface is exactly the real
:class:`KiteClient`'s, and every public name is in exactly one of three sets:

* ``VIRTUAL``: implemented here against the owner's virtual ledger (orders, fills, positions,
  GTTs, funds, charges, and the identity/liveness calls). Writes go through the same broker-write
  capability guard as a live client, so a code path that is refused live is refused here.
* ``READ_ALLOWLIST``: market-data and instrument reads, delegated to the OWNER's real client.
  Nothing on this list can write.
* ``REFUSED``: everything else public on the real client (login, sessions, mutual funds, alerts,
  position conversion...). Calling one raises :class:`SimulationRefused` (a ``SimulationBoundaryError``
  and a ``PermissionError``).

Any other attribute, public or private, raises :class:`SimulationUnknownAttribute` (a ``SimulationBoundaryError``
and an ``AttributeError``, so probing with ``getattr(client, name, default)`` works as on a real client). There is
no HTTP plumbing on this class at all (``_auth_post`` and friends are refused), so a write cannot
leave the process even by accident. A test pins the three sets against the real client's public
surface: a method added to ``KiteClient`` fails the suite until someone classifies it.
"""
from __future__ import annotations

import inspect
import time
from typing import Any, Callable, List, Optional

from app.core.logging import get_logger
from app.schemas.account import (
    AccountFill, AccountOrder, AccountPosition, AssetBalance, PortfolioSnapshot,
)
from app.services.exchanges.kite import constants as K
from app.services.exchanges.kite.client import KiteClient, _parse_kite_ts
from app.services.exchanges.kite.errors import KiteInputError, KiteOrderError
from app.services.simulation.charges import compute_charges
from app.services.simulation.identity import owner_uid, sim_account_id, sim_uid
from app.services.simulation.ledger import SimLedger, get_ledger, position_margin
from app.services.simulation.quotes import MarketDataUnavailable, QuoteSource, RealClientProvider
from app.services.simulation.virtual_exchange import VirtualExchange

log = get_logger(__name__)


class SimulationBoundaryError(Exception):
    """A Simulation tried to do something only a real broker session may do (the family of refusals)."""


class SimulationRefused(SimulationBoundaryError, PermissionError):
    """A refused method was called. A ``PermissionError`` so the execution path reads it as "refused
    before anything was sent"."""


class SimulationUnknownAttribute(SimulationBoundaryError, AttributeError):
    """An attribute the simulated client does not have. An ``AttributeError`` as well, so ``getattr`` with a
    default and ``hasattr`` (which engine code uses to probe a client) behave as they do on a real client."""


#: Implemented against the virtual ledger.
VIRTUAL = frozenset({
    "place_order", "place_order_option", "place_order_future", "cancel_order", "modify_order",
    "get_orders", "get_order_history", "get_order_trades", "get_trades",
    "get_gtts", "get_gtt", "place_gtt", "modify_gtt", "delete_gtt",
    "get_positions_raw", "get_positions", "get_holdings", "get_margins", "get_balances",
    "get_open_orders", "get_fills", "get_portfolio_snapshot", "order_margins", "basket_margins",
    "order_charges", "get_profile", "ping", "test_connection", "close", "access_token",
    "get_auctions", "get_mf_holdings", "get_mf_orders", "get_mf_sips", "get_mf_order", "get_mf_sip",
    "get_alerts", "get_alert", "get_alert_history",
})
#: Delegated to the owner's real client. READS ONLY.
READ_ALLOWLIST = frozenset({
    "get_quote", "get_ltp", "get_ohlc", "get_historical", "get_candles", "get_option_chain",
    "get_index_price", "get_spot_price", "search_instruments", "search_mf_instruments",
    "instrument_lot_sizes", "instrument_expiries", "resolve_token", "get_product_id",
})
#: Public on the real client, meaningless or dangerous in a simulation: always raises.
REFUSED = frozenset({
    "generate_session", "invalidate_session", "renew_access_token", "login_url",
    "place_mf_order", "cancel_mf_order", "place_mf_sip", "modify_mf_sip", "cancel_mf_sip",
    "convert_position", "create_alert", "modify_alert", "delete_alerts", "initiate_holdings_auth",
    "cancel_replace_stop", "market_reduce_close",
})


def _delegated_read(name: str) -> Callable:
    real_fn = getattr(KiteClient, name)

    async def method(self, *args, **kwargs):
        real = await self._quotes.real_client()
        result = getattr(real, name)(*args, **kwargs)
        return await result if inspect.isawaitable(result) else result

    method.__name__, method.__qualname__ = name, f"SimulatedKiteClient.{name}"
    method.__doc__ = f"Read delegated to the owner's real client: {name}."
    method.__signature__ = inspect.signature(real_fn)
    return method


def _refused(name: str) -> Callable:
    def method(self, *args, **kwargs):
        raise SimulationRefused(f"{name} is not available in Simulation (no broker session, no real orders).")

    method.__name__, method.__qualname__ = name, f"SimulatedKiteClient.{name}"
    return method


class _InstrumentsProxy:
    """Read-only view of the owner's real instrument master (for code that reaches ``_instruments``)."""

    def __init__(self, quotes: QuoteSource) -> None:
        self._quotes = quotes

    async def _cache(self):
        real = await self._quotes.real_client()
        cache = getattr(real, "_instruments", None)
        if cache is None:
            raise MarketDataUnavailable("The owner's instrument master is unavailable")
        return cache

    async def load(self, exchange: str = ""):
        return await (await self._cache()).load(exchange)

    async def search(self, query: str, exchange: str = "", limit: int = 50):
        return await (await self._cache()).search(query, exchange, limit)

    async def resolve_token(self, tradingsymbol: str, exchange: str = "NFO"):
        return await (await self._cache()).resolve_token(tradingsymbol, exchange)

    async def lot_sizes(self, symbols: list):
        return await (await self._cache()).lot_sizes(symbols)

    async def expiries(self, symbols: list):
        return await (await self._cache()).expiries(symbols)


class SimulatedKiteClient:
    """Kite-shaped client over the virtual exchange. ``_is_paper`` means "no real money"."""

    _is_paper = True
    _is_simulated = True

    def __init__(self, owner: str, real_client_provider: RealClientProvider, *, ledger: Optional[SimLedger] = None,
                 exchange: Optional[VirtualExchange] = None, quotes: Optional[QuoteSource] = None,
                 clock: Optional[Callable] = None) -> None:
        self._owner = owner_uid(owner)
        self._account_id = sim_account_id(self._owner)
        self._owner_user_id = sim_uid(self._owner)
        self._kite_user_id = self._account_id
        self._account_generation = ""
        self._access_token = ""
        self._ledger = ledger or get_ledger()
        self._quotes = quotes or QuoteSource(real_client_provider, owner=self._owner)
        self._exchange = exchange or VirtualExchange(self._owner, self._ledger, self._quotes, clock=clock)
        self._exchange.ledger.ensure_account(self._owner)

    # ── identity ──────────────────────────────────────────────────────────────────────
    @property
    def kite_user_id(self) -> str:
        return self._kite_user_id

    @property
    def account_id(self) -> str:
        # The canonical execution service probes ``getattr(client, "account_id", None)`` as the
        # fallback of its identity check; the probe must answer, not raise the boundary error.
        return self._account_id

    @property
    def access_token(self) -> str:
        return ""

    @property
    def exchange(self) -> VirtualExchange:
        return self._exchange

    @property
    def ledger(self) -> SimLedger:
        return self._ledger

    @property
    def _instruments(self) -> _InstrumentsProxy:
        return _InstrumentsProxy(self._quotes)

    def __getattr__(self, name: str):
        # Only reached for names that are not defined above: refuse, never guess.
        if name.startswith("__"):
            raise AttributeError(name)
        raise SimulationUnknownAttribute(
            f"{name!r} does not exist on a SimulatedKiteClient (Simulation has no broker session).")

    # ── guard plumbing (same capability contract as the live client) ──────────────────
    @staticmethod
    def _guard(operation: str) -> None:
        from app.services.snapback_family_mode import guard_broker_write
        guard_broker_write(operation)

    @staticmethod
    def _entry_operation() -> str:
        from app.services.snapback_family_mode import INTENT_REDUCE, current_intent
        return "exit_order" if current_intent() == INTENT_REDUCE else "place_order"

    # ── orders (write) ────────────────────────────────────────────────────────────────
    async def _place(
        self, *, variety: str, exchange: str, tradingsymbol: str, transaction_type: str, quantity: int,
        product: str, order_type: str, price: Optional[float] = None, trigger_price: Optional[float] = None,
        validity: str = K.VALIDITY_DAY, disclosed_quantity: Optional[int] = None, validity_ttl: Optional[int] = None,
        iceberg_legs: Optional[int] = None, iceberg_quantity: Optional[int] = None, tag: Optional[str] = None,
        market_protection: float = -1, allow_amo: bool = True,
    ) -> dict:
        from math import isfinite
        from app.services.snapback_family_mode import guard_broker_reduction
        self._guard(self._entry_operation())
        guard_broker_reduction(exchange=exchange, tradingsymbol=tradingsymbol,
                               transaction_type=transaction_type, quantity=quantity)
        if validity_ttl or iceberg_legs or iceberg_quantity:
            raise KiteInputError("TTL and iceberg orders are not simulated.", error_type="InputException", status_code=400)
        if order_type in (K.ORDER_TYPE_MARKET, K.ORDER_TYPE_SLM):
            protection = float(market_protection)
            if not isfinite(protection) or not (protection == -1 or 0 < protection <= 100):
                raise KiteOrderError("Market orders require valid price protection.", error_type="InputException")
        return await self._exchange.place_order(
            exchange=exchange, tradingsymbol=tradingsymbol, transaction_type=transaction_type,
            quantity=int(quantity), product=product, order_type=order_type,
            price=price if order_type in (K.ORDER_TYPE_LIMIT, K.ORDER_TYPE_SL) else None,
            trigger_price=trigger_price if order_type in (K.ORDER_TYPE_SL, K.ORDER_TYPE_SLM) else None,
            validity=validity, variety=variety, tag=str(tag)[:20] if tag else None,
            disclosed_quantity=disclosed_quantity, market_protection=market_protection)

    async def place_order(
        self, symbol: str, side: str, size: float, order_type: str = "market_order",
        limit_price: Optional[float] = None, time_in_force: str = "day", post_only: bool = False,
        reduce_only: bool = False, stop_loss: Optional[float] = None, take_profit: Optional[float] = None,
        trail_amount: Optional[float] = None, **kwargs,
    ) -> dict:
        """Same contract as ``KiteClient.place_order``: the entry leg only, under the canonical capability."""
        self._guard(self._entry_operation())
        if post_only:
            raise KiteOrderError("post_only (maker-only) is not supported by Kite.", error_type="OrderException")
        exchange, tradingsymbol = KiteClient._split_symbol(symbol, kwargs.get("exchange"))
        txn = K.TXN_BUY if str(side).lower() in ("buy", "long") else K.TXN_SELL
        kite_ot = kwargs.get("kite_order_type")
        trigger_price = kwargs.get("trigger_price", stop_loss)
        if kite_ot is None:
            ot_lower = str(order_type).lower()
            if ot_lower in ("sl", "stop_loss_limit", "stop_limit"):
                kite_ot = K.ORDER_TYPE_SL
            elif ot_lower in ("slm", "sl_m", "stop_loss", "stop_loss_market", "stop_market"):
                kite_ot = K.ORDER_TYPE_SLM
            elif ot_lower.startswith("limit"):
                kite_ot = K.ORDER_TYPE_LIMIT
            else:
                kite_ot = K.ORDER_TYPE_MARKET
        validity = K.VALIDITY_IOC if str(time_in_force).lower() == "ioc" else K.VALIDITY_DAY
        return await self._place(
            variety=kwargs.get("variety", K.VARIETY_REGULAR), exchange=exchange, tradingsymbol=tradingsymbol,
            transaction_type=txn, quantity=int(size), product=kwargs.get("product", K.PRODUCT_MIS),
            order_type=kite_ot,
            price=limit_price if kite_ot in (K.ORDER_TYPE_LIMIT, K.ORDER_TYPE_SL) else None,
            trigger_price=trigger_price if kite_ot in (K.ORDER_TYPE_SL, K.ORDER_TYPE_SLM) else None,
            validity=validity, tag=kwargs.get("tag"), market_protection=kwargs.get("market_protection", -1),
            allow_amo=bool(kwargs.get("allow_amo", True)))

    async def place_order_option(
        self, option_symbol: str, side: str, size: float, order_type: str = "market_order",
        limit_price: Optional[float] = None, stop_loss: Optional[float] = None, take_profit: Optional[float] = None,
        exchange: str = K.EXCHANGE_NFO, tag: Optional[str] = None, **kwargs,
    ) -> dict:
        product = kwargs.pop("product", K.PRODUCT_NRML)
        return await self.place_order(option_symbol, side, size, order_type=order_type, limit_price=limit_price,
                                      exchange=exchange, product=product, stop_loss=stop_loss, tag=tag,
                                      allow_amo=False, **kwargs)

    async def place_order_future(
        self, tradingsymbol: str, side: str, size: float, order_type: str = "market_order",
        limit_price: Optional[float] = None, exchange: str = K.EXCHANGE_NFO, tag: Optional[str] = None, **kwargs,
    ) -> dict:
        product = kwargs.pop("product", K.PRODUCT_NRML)
        return await self.place_order(tradingsymbol, side, size, order_type=order_type, limit_price=limit_price,
                                      exchange=exchange, product=product, tag=tag, allow_amo=False, **kwargs)

    async def cancel_order(self, order_id: str, product_id: int = 0, variety: str = K.VARIETY_REGULAR) -> dict:
        self._guard("cancel_order")
        return await self._exchange.cancel_order(str(order_id), variety=variety)

    async def modify_order(self, order_id: str, variety: str = K.VARIETY_REGULAR, **fields) -> dict:
        self._guard("modify_order")
        allowed = {k: v for k, v in fields.items()
                   if v is not None and k in ("quantity", "price", "trigger_price", "order_type", "validity",
                                              "disclosed_quantity")}
        return await self._exchange.modify_order(str(order_id), variety=variety, **allowed)

    # ── orders / trades (read) ────────────────────────────────────────────────────────
    async def get_orders(self) -> list:
        return self._exchange.orders()

    async def get_trades(self) -> list:
        return self._exchange.trades()

    async def get_order_history(self, order_id: str) -> list:
        return self._exchange.order_history(str(order_id))

    async def get_order_trades(self, order_id: str) -> list:
        return self._exchange.trades(str(order_id))

    # ── GTT ───────────────────────────────────────────────────────────────────────────
    async def get_gtts(self) -> list:
        return self._exchange.gtts()

    async def get_gtt(self, trigger_id: int) -> dict:
        return self._exchange.gtt(trigger_id)

    async def place_gtt(self, *, trigger_type, tradingsymbol, exchange, last_price, trigger_values, orders) -> dict:
        self._guard("place_gtt")
        return await self._exchange.place_gtt(trigger_type=trigger_type, tradingsymbol=tradingsymbol, exchange=exchange,
                                              last_price=last_price, trigger_values=trigger_values, orders=orders)

    async def modify_gtt(self, trigger_id, *, trigger_type, tradingsymbol, exchange, last_price, trigger_values,
                         orders) -> dict:
        self._guard("modify_gtt")
        return await self._exchange.modify_gtt(trigger_id, trigger_type=trigger_type, tradingsymbol=tradingsymbol,
                                               exchange=exchange, last_price=last_price,
                                               trigger_values=trigger_values, orders=orders)

    async def delete_gtt(self, trigger_id) -> dict:
        self._guard("cancel_gtt")
        return await self._exchange.delete_gtt(trigger_id)

    # ── positions, holdings, funds ────────────────────────────────────────────────────
    async def get_positions_raw(self) -> dict:
        """``{"net": [...], "day": [...]}`` marked at the latest real price (the last known one if none now)."""
        held = self._ledger.positions(self._owner, open_only=True)
        keys = list(dict.fromkeys(f"{p['exchange']}:{p['tradingsymbol']}" for p in held))
        books = await self._quotes.books(keys, strict=False) if keys else {}
        for p in held:
            book = books.get(f"{p['exchange']}:{p['tradingsymbol']}")
            if book is not None:
                self._ledger.set_mark(self._owner, p["exchange"], p["tradingsymbol"], p["product"], book.ltp)
        return self._exchange.positions(books)

    async def get_positions(self) -> List[AccountPosition]:
        out = []
        for p in (await self.get_positions_raw()).get("net") or []:
            qty = int(p.get("quantity") or 0)
            if qty == 0:
                continue
            out.append(AccountPosition(
                symbol=str(p.get("tradingsymbol") or ""),
                underlying=str(p.get("exchange") or "") + ":" + str(p.get("tradingsymbol", "")[:6]),
                size=float(qty), side="long" if qty > 0 else "short", entry_price=float(p.get("average_price") or 0.0),
                mark_price=float(p.get("last_price") or 0.0), unrealized_pnl=float(p.get("pnl") or 0.0),
                realized_pnl=float(p.get("realised") or 0.0), margin=float(p.get("value") or 0.0),
                position_type=str(p.get("product") or ""), created_at_ms=None))
        return out

    async def get_holdings(self) -> list:
        return []

    async def get_auctions(self) -> list:
        return []

    async def get_margins(self, segment: Optional[str] = None) -> dict:
        return self._exchange.margins(segment)

    async def get_balances(self) -> List[AssetBalance]:
        out = []
        for seg, info in (self._exchange.margins() or {}).items():
            out.append(AssetBalance(
                asset=f"INR ({seg})", available=float(info["available"]["live_balance"] or 0.0),
                locked=float(info["utilised"]["debits"] or 0.0), total=float(info["net"] or 0.0),
                inr_value=float(info["net"] or 0.0)))
        return out

    async def get_open_orders(self, underlying: Optional[str] = None) -> List[AccountOrder]:
        return [AccountOrder(
            order_id=str(o["order_id"]), symbol=str(o["tradingsymbol"]),
            side="buy" if o["transaction_type"] == "BUY" else "sell", size=float(o["quantity"]),
            price=float(o["price"] or 0.0), filled_size=float(o["filled_quantity"] or 0.0),
            status=str(o["status"]).lower(), order_type=str(o["order_type"]).lower(),
            created_at_ms=_parse_kite_ts(o.get("order_timestamp")))
            for o in self._exchange.orders() if o["status"] in K.OPEN_ORDER_STATUSES]

    async def get_fills(self, limit: int = 50) -> List[AccountFill]:
        return [AccountFill(
            fill_id=str(t["trade_id"]), order_id=str(t["order_id"]), symbol=str(t["tradingsymbol"]),
            side="buy" if t["transaction_type"] == "BUY" else "sell", size=float(t["quantity"]),
            price=float(t["average_price"]), fee=float((t.get("_sim") or {}).get("charges", {}).get("total", 0.0)),
            fee_asset="INR", pnl=float((t.get("_sim") or {}).get("realised", 0.0)),
            created_at_ms=_parse_kite_ts(t.get("fill_timestamp")))
            for t in self._ledger.trades(self._owner, day=self._exchange.today())[:limit]]

    async def get_portfolio_snapshot(self) -> PortfolioSnapshot:
        balances, positions = await self.get_balances(), await self.get_positions()
        view = self._ledger.account_view(self._owner, self._exchange.today())
        return PortfolioSnapshot(
            exchange="simulation", display_name="Simulation (virtual money)",
            total_balance_inr=round(view["equity"], 2), unrealized_pnl_inr=round(view["unrealised"], 2),
            realized_pnl_inr=round(view["realised_today"], 2), margin_used=round(view["blocked_margin"], 2),
            margin_available=max(0.0, round(view["available"], 2)), positions_count=len(positions),
            open_orders_count=view["open_orders"], balances=balances, timestamp_ms=int(time.time() * 1000))

    # ── margins and charges (computed, never asked of a broker) ───────────────────────
    async def _priced(self, leg: dict):
        exchange, symbol = str(leg.get("exchange") or "NFO").upper(), str(leg.get("tradingsymbol") or "")
        meta = await self._quotes.instrument(exchange, symbol)
        book = await self._quotes.book(exchange, symbol)
        side = str(leg.get("transaction_type") or "BUY").upper()
        price = float(leg.get("price") or leg.get("average_price") or 0.0) or (book.ask if side == "BUY" else book.bid) or book.ltp
        return meta, side, int(leg.get("quantity") or 0), price

    async def order_margins(self, orders: list) -> list:
        pct = self._ledger.settings(self._owner).futures_margin_pct
        rows = []
        for leg in orders or []:
            meta, side, qty, price = await self._priced(leg)
            total = position_margin(meta.as_meta(), qty if side == "BUY" else -qty, price, futures_pct=pct)
            premium = total if meta.instrument_type in ("CE", "PE") and side == "BUY" else 0.0
            rows.append({"type": "equity", "tradingsymbol": meta.symbol, "exchange": meta.exchange,
                         "span": round(total - premium, 2), "exposure": 0, "option_premium": premium, "additional": 0,
                         "bo": 0, "cash": 0, "var": 0, "pnl": {"realised": 0, "unrealised": 0}, "leverage": 1,
                         "charges": {}, "total": total})
        return rows

    async def basket_margins(self, orders: list, consider_positions: bool = True) -> dict:
        rows = await self.order_margins(orders)
        total = round(sum(r["total"] for r in rows), 2)
        return {"initial": {"total": total}, "final": {"total": total}, "orders": rows}

    async def order_charges(self, orders: list) -> list:
        rows = []
        for leg in orders or []:
            meta, side, qty, price = await self._priced(leg)
            c = compute_charges(exchange=meta.exchange, tradingsymbol=meta.symbol, side=side, quantity=qty, price=price,
                                product=str(leg.get("product") or "NRML"), instrument_type=meta.instrument_type)
            rows.append({**leg, "charges": {
                "transaction_tax": c.stt, "transaction_tax_type": "stt", "exchange_turnover_charge": c.exchange_txn,
                "sebi_turnover_charge": c.sebi, "brokerage": c.brokerage, "stamp_duty": c.stamp,
                "gst": {"igst": c.gst, "cgst": 0, "sgst": 0, "total": c.gst}, "total": c.total}})
        return rows

    # ── identity and liveness (virtual) ───────────────────────────────────────────────
    async def get_profile(self) -> dict:
        """The virtual account's profile. Raises MarketDataUnavailable when the owner's real session is gone,
        so a loop that checks liveness before scanning pauses instead of trading on nothing."""
        await self._quotes.real_client()
        return {"user_id": self._account_id, "user_name": "Simulation", "user_shortname": "Simulation", "email": "",
                "user_type": "virtual", "broker": "SIMULATION", "exchanges": list(("NSE", "BSE", "NFO", "BFO")),
                "products": ["MIS", "NRML", "CNC"], "order_types": list(K.ORDER_TYPES)}

    async def ping(self) -> bool:
        return True

    async def test_connection(self) -> bool:
        try:
            await self._quotes.real_client()
            return True
        except MarketDataUnavailable:
            return False

    async def close(self) -> None:
        """Nothing to close: the owner's real client is not ours to shut."""
        return None

    # ── empty by construction ─────────────────────────────────────────────────────────
    async def get_mf_holdings(self) -> list:
        return []

    async def get_mf_orders(self) -> list:
        return []

    async def get_mf_sips(self) -> list:
        return []

    async def get_mf_order(self, order_id: str) -> dict:
        return {}

    async def get_mf_sip(self, sip_id: str) -> dict:
        return {}

    async def get_alerts(self) -> list:
        return []

    async def get_alert(self, uuid: str) -> dict:
        return {}

    async def get_alert_history(self, uuid: str) -> list:
        return []


for _name in READ_ALLOWLIST:
    setattr(SimulatedKiteClient, _name, _delegated_read(_name))
for _name in REFUSED:
    setattr(SimulatedKiteClient, _name, _refused(_name))
del _name


def default_postback_sink(owner: str):
    """Deliver a virtual order update to the tenant's order handlers, as the broker websocket would."""
    uid = sim_uid(owner)

    async def sink(order: dict) -> None:
        from app.services.exchanges.kite import ticker_manager
        await ticker_manager.broadcast_order_update(uid, order)
    return sink


def get_sim_client(owner: str, real_client_provider: RealClientProvider, *, ledger: Optional[SimLedger] = None,
                   clock: Optional[Callable] = None, quotes: Optional[QuoteSource] = None,
                   postbacks: bool = True) -> SimulatedKiteClient:
    """A client for the owner's virtual tenant. ``real_client_provider`` returns (or awaits to) the
    owner's real Kite client, or raises when there is no live session."""
    client = SimulatedKiteClient(owner, real_client_provider, ledger=ledger, clock=clock, quotes=quotes)
    if postbacks:
        client.exchange.set_postback_sink(default_postback_sink(owner))
    return client


def is_simulated_client(obj: Any) -> bool:
    return isinstance(obj, SimulatedKiteClient)


def is_live_like(client: Any) -> bool:
    """True for a real-money client AND for a simulated one.

    Both fill asynchronously (a resting limit, a partial fill) and report fills by order postback, so engine
    code that picks between "wait for the broker's postback" and "the old paper client fills synchronously"
    on ``_is_paper is False`` should pick on this instead. ``_is_paper`` itself stays ``True`` for a simulated
    client: it means "no real money", which is what the live-only safety machinery (live-minimum gate, external
    exposure, kill switch) keys on.
    """
    return getattr(client, "_is_paper", None) is False or bool(getattr(client, "_is_simulated", False))


def public_surface(cls: type = KiteClient) -> List[str]:
    """Every public callable or property name of ``cls`` (what the boundary test classifies)."""
    return sorted(n for n, m in inspect.getmembers(cls) if not n.startswith("_") and (callable(m) or isinstance(m, property)))


__all__ = [
    "SimulationBoundaryError", "SimulationRefused", "SimulationUnknownAttribute", "SimulatedKiteClient", "VIRTUAL", "READ_ALLOWLIST", "REFUSED", "get_sim_client",
    "default_postback_sink", "is_simulated_client", "is_live_like", "public_surface",
]
