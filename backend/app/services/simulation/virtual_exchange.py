"""The virtual exchange: a matching engine over REAL prices that books into the virtual ledger.

One :class:`VirtualExchange` serves one owner. It accepts orders and GTTs the way Kite does,
fills them against the live touch, and never contacts a broker: its only inputs are real
quotes (via :class:`~app.services.simulation.quotes.QuoteSource`) and the owner's virtual books.

Matching rules
* MARKET, and a LIMIT that is marketable, fill at the best ask (buy) / best bid (sell) plus
  ``slippage_ticks`` (default 1) of the instrument's tick size; a LIMIT never fills worse than
  its limit. An empty touch means no liquidity: the order rests, nothing is invented.
* A resting LIMIT fills on the pass where the touch crosses it. SL / SL-M wait in TRIGGER PENDING
  until the last price crosses the trigger, then become a LIMIT / MARKET.
* Depth: with ``partial_fills`` (the default) an order takes only what the book DISPLAYS: it walks the shown
  levels best first (one trade per level, slippage on each, never past a LIMIT, whole lots only) and the
  rest stays OPEN and is worked again on later passes. Without a level list the touch quantity alone caps it;
  a size of zero means unknown and is not invented. ``partial_fills`` off is the explicit infinite-liquidity
  mode: the whole order fills at the touch whatever the displayed size.
* Charges are per ORDER, not per trade: the levels or passes an order fills over share one brokerage.
* GTT (single and two-leg OCO) is evaluated against the last price on every pass. On trigger the
  child LIMIT is placed and matched by the same rules, so a gap can leave it resting.
* Margin: a long option holds its premium, a future or short option ``futures_margin_pct`` of
  notional. An order that cannot be funded is refused with a Kite-like message.

Everything state-changing runs inside one ledger transaction with no ``await`` in it, so a
fill, its charges, the position and the cash can never disagree.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, time as dtime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from app.core.logging import get_logger
from app.services.exchanges.kite import constants as K
from app.services.exchanges.kite.errors import KiteInputError, KiteMarginError, KiteOrderError
from app.services.simulation.charges import Charges, compute_charges
from app.services.simulation.identity import owner_uid, sim_account_id
from app.services.simulation.ledger import (
    ACTIVE_GTT, IST, OPEN_STATUSES, SimLedger, SimSettings, position_margin, public,
)
from app.services.simulation.quotes import Book, InstrumentMeta, MarketDataUnavailable, QuoteSource

log = get_logger(__name__)

S_PUT, S_OPEN, S_TRIGGER = "PUT ORDER REQ RECEIVED", "OPEN", "TRIGGER PENDING"
S_COMPLETE, S_CANCELLED, S_REJECTED = "COMPLETE", "CANCELLED", "REJECTED"
SUPPORTED_EXCHANGES = ("NSE", "BSE", "NFO", "BFO")
SESSION_CLOSE = dtime(15, 30)        # fallback only: the real close comes from market_hours.continuous_close
GTT_LIST_DAYS = 7
_OUTBOX_MAX = 2000

PostbackSink = Callable[[dict], Awaitable[None]]
_CHARGE_FIELDS = ("turnover", "brokerage", "stt", "exchange_txn", "sebi", "stamp", "gst", "total")


# ── small pure helpers ──────────────────────────────────────────────────────────────────
def snap(price: float, tick: float, mode: str = "nearest") -> float:
    """``price`` on the tick grid: ``up`` / ``down`` / ``nearest``."""
    d, t = Decimal(str(price)), Decimal(str(tick))
    rounding = {"up": ROUND_CEILING, "down": ROUND_FLOOR}.get(mode, ROUND_HALF_UP)
    return float((d / t).to_integral_value(rounding=rounding) * t)


def slipped(touch: float, ticks: int, tick: float, buy: bool) -> float:
    """The touch moved ``ticks`` ticks against the taker, exact in decimal and on the tick grid."""
    t = Decimal(str(tick))
    raw = Decimal(str(touch)) + t * ticks if buy else Decimal(str(touch)) - t * ticks
    return float((raw / t).to_integral_value(rounding=ROUND_CEILING if buy else ROUND_FLOOR) * t)


def on_tick(price: float, tick: float) -> bool:
    return Decimal(str(price)) % Decimal(str(tick)) == 0


def _lt(a: float, b: float) -> bool:
    return round(a, 6) < round(b, 6)


def _input(message: str, status: int = 400) -> KiteInputError:
    return KiteInputError(message, error_type="InputException", status_code=status)


def _order_error(message: str, status: int = 400) -> KiteOrderError:
    return KiteOrderError(message, error_type="OrderException", status_code=status)


def _stamp(when: datetime) -> str:
    return when.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")


def _close_of(day, exchange: str) -> dtime:
    """When DAY orders of ``exchange`` lapse on ``day``: the repo's session policy (NFO 15:40 from 2026-08-03, BSE 15:30)."""
    try:
        from app.services.kite_engine import market_hours
        return market_hours.continuous_close(day, exchange)
    except Exception:  # noqa: BLE001 - an exchange the policy does not know: the legacy close
        return SESSION_CLOSE


def _session_open(now: datetime, exchange: str) -> bool:
    try:
        from app.services.kite_engine import market_hours
        return bool(market_hours.is_management_session(now, exchange=exchange))
    except Exception:  # noqa: BLE001 - cannot prove the market is open: closed
        return False


class VirtualExchange:
    def __init__(self, owner: str, ledger: SimLedger, quotes: QuoteSource, *,
                 clock: Optional[Callable[[], datetime]] = None, postback_delay_s: float = 0.2,
                 session_open: Optional[Callable[[datetime, str], bool]] = None) -> None:
        self.owner = owner_uid(owner)
        self.account_id = sim_account_id(self.owner)
        self.ledger, self.quotes = ledger, quotes
        self._clock = clock or (lambda: datetime.now(IST))
        self._session_open = session_open or _session_open
        self._outbox: List[dict] = []
        self._sink: Optional[PostbackSink] = None
        self._postback_delay_s = postback_delay_s
        self._tasks: set = set()

    # ── clock / bookkeeping ───────────────────────────────────────────────────────────
    def now(self) -> datetime:
        return self._clock().astimezone(IST)

    def today(self) -> str:
        return self.now().date().isoformat()

    def set_postback_sink(self, sink: Optional[PostbackSink]) -> None:
        self._sink = sink

    def drain_postbacks(self) -> List[dict]:
        out, self._outbox = self._outbox, []
        return out

    def _emit(self, order: dict) -> None:
        self._outbox.append({**public(order), "user_id": self.account_id})
        del self._outbox[:-_OUTBOX_MAX]

    def _flush(self) -> None:
        """Hand pending order updates to the sink a moment after the REST reply, as a broker does."""
        if self._sink is None or not self._outbox:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._deliver_later())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _deliver_later(self) -> None:
        await asyncio.sleep(self._postback_delay_s)
        await self.deliver_postbacks()

    async def deliver_postbacks(self) -> int:
        sink, batch = self._sink, self.drain_postbacks()
        if sink is None:
            self._outbox = batch + self._outbox
            return 0
        for order in batch:
            try:
                await sink(order)
            except Exception as exc:  # noqa: BLE001 - a listener must never break the exchange
                log.warning("sim postback for %s failed: %s", order.get("order_id"), exc)
        return len(batch)

    # ── order lifecycle ───────────────────────────────────────────────────────────────
    def _snapshot(self, order: dict, status: str, message: Optional[str] = None) -> None:
        order["status"] = status
        order["status_message"] = message
        order["status_message_raw"] = message
        order["exchange_update_timestamp"] = _stamp(self.now())
        if status != S_PUT:
            order["exchange_timestamp"] = order["exchange_update_timestamp"]
        self.ledger.put_order(self.owner, order)
        self.ledger.add_history(self.owner, order)
        if status != S_PUT:
            self._emit(order)

    def _validate(self, *, exchange: str, side: str, quantity: int, product: str, order_type: str,
                  price: Optional[float], trigger: Optional[float], validity: str, variety: str,
                  meta: InstrumentMeta, settings: SimSettings) -> None:
        if variety != K.VARIETY_REGULAR:
            raise _input("Only the regular order variety is simulated.")
        if exchange not in SUPPORTED_EXCHANGES:
            raise _input(f"Exchange {exchange} is not simulated.")
        if side not in (K.TXN_BUY, K.TXN_SELL):
            raise _input("transaction_type must be BUY or SELL.")
        if order_type not in K.ORDER_TYPES:
            raise _input(f"Unknown order_type {order_type}.")
        if validity not in (K.VALIDITY_DAY, K.VALIDITY_IOC):
            raise _input(f"Validity {validity} is not simulated.")
        if product not in K.PRODUCTS:
            raise _input(f"Unknown product {product}.")
        if exchange in ("NFO", "BFO") and product not in (K.PRODUCT_NRML, K.PRODUCT_MIS):
            raise _input("Derivatives support only the NRML and MIS products.")
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            raise _input("Quantity must be a positive whole number.")
        if meta.lot_size > 1 and quantity % meta.lot_size:
            raise _input(f"Quantity must be a multiple of the lot size ({meta.lot_size}).")
        if order_type in (K.ORDER_TYPE_LIMIT, K.ORDER_TYPE_SL) and not (price and price > 0):
            raise _input(f"{order_type} orders need a positive price.")
        if order_type in (K.ORDER_TYPE_SL, K.ORDER_TYPE_SLM) and not (trigger and trigger > 0):
            raise _input(f"{order_type} orders need a positive trigger price.")
        if order_type == K.ORDER_TYPE_SL and ((side == K.TXN_BUY and trigger > price)
                                              or (side == K.TXN_SELL and trigger < price)):
            raise _input("The trigger price is on the wrong side of the limit price.")
        if settings.enforce_tick_size:
            for label, value in (("price", price), ("trigger price", trigger)):
                if value and not on_tick(value, meta.tick_size):
                    raise _input(f"Tick size for this script is {meta.tick_size}. Enter the {label} in "
                                 "multiples of the tick size.")
        if settings.enforce_market_hours and not self._session_open(self.now(), exchange):
            raise _order_error("Markets are closed; orders are accepted only during the trading session.")

    def _check_margin(self, meta: InstrumentMeta, order: dict, price: float, settings: SimSettings) -> Optional[str]:
        """None when the order is funded, else the Kite-like refusal text."""
        side, qty = order["transaction_type"], int(order["pending_quantity"])
        held = self.ledger.get_position(self.owner, order["exchange"], order["tradingsymbol"], order["product"])
        have = int(held["quantity"]) if held else 0
        signed = qty if side == K.TXN_BUY else -qty
        increase = max(0, abs(have + signed) - abs(have))
        if increase == 0 or price <= 0:
            return None
        required = position_margin(meta.as_meta(), increase if signed > 0 else -increase, price,
                                   futures_pct=settings.futures_margin_pct)
        available = self.ledger.account_view(self.owner, self.today())["available"]
        if required > available + 1e-9:
            return (f"Insufficient funds. Required margin is {required:,.2f} but available margin is "
                    f"{max(available, 0.0):,.2f}.")
        return None

    def _expected_price(self, order: dict, book: Book, meta: InstrumentMeta, settings: SimSettings) -> float:
        buy = order["transaction_type"] == K.TXN_BUY
        eff = order["_sim"]["eff"] or order["order_type"]
        if eff in (K.ORDER_TYPE_LIMIT, K.ORDER_TYPE_SL) and order["price"]:
            return float(order["price"])
        if eff == K.ORDER_TYPE_SLM and order["trigger_price"]:
            return float(order["trigger_price"])
        touch = (book.ask if buy else book.bid) or book.ltp
        return slipped(touch, settings.slippage_ticks, meta.tick_size, True) if buy else touch

    def _create(self, *, meta: InstrumentMeta, book: Book, settings: SimSettings, side: str, quantity: int,
                product: str, order_type: str, price: Optional[float], trigger: Optional[float], validity: str,
                variety: str, tag: Optional[str], disclosed: Optional[int], market_protection: float,
                gtt_id: Optional[int] = None, reject_on_margin: bool = False) -> dict:
        now = self.now()
        oid = f"{now:%y%m%d}9{self.ledger.next_id('order'):08d}"
        stamp = _stamp(now)
        needs_trigger = order_type in (K.ORDER_TYPE_SL, K.ORDER_TYPE_SLM)
        order = {
            "order_id": oid, "exchange_order_id": f"13{oid[-13:]}", "parent_order_id": None,
            "placed_by": self.account_id, "variety": variety, "status": S_PUT, "status_message": None,
            "status_message_raw": None, "order_timestamp": stamp, "exchange_update_timestamp": stamp,
            "exchange_timestamp": stamp, "exchange": meta.exchange, "tradingsymbol": meta.symbol,
            "instrument_token": meta.token, "order_type": order_type, "transaction_type": side,
            "validity": validity, "validity_ttl": 0, "product": product, "quantity": quantity,
            "disclosed_quantity": int(disclosed or 0), "price": float(price or 0.0),
            "trigger_price": float(trigger or 0.0), "average_price": 0.0, "filled_quantity": 0,
            "pending_quantity": quantity, "cancelled_quantity": 0, "market_protection": market_protection,
            "meta": {}, "tag": tag, "tags": [tag] if tag else [], "guid": f"sim{oid}",
            "_sim": {"day": now.date().isoformat(), "placed_ms": int(now.timestamp() * 1000),
                     "meta": meta.as_meta(), "eff": None if needs_trigger else order_type, "gtt_id": gtt_id},
        }
        if tag is not None and not isinstance(tag, str):
            order["tag"], order["tags"] = str(tag), [str(tag)]
        refusal = self._check_margin(meta, order, self._expected_price(order, book, meta, settings), settings)
        if refusal and not reject_on_margin:
            raise KiteMarginError(refusal, error_type="MarginException", status_code=400)
        self.ledger.put_order(self.owner, order)
        self.ledger.add_history(self.owner, order)
        if refusal:
            self._snapshot(order, S_REJECTED, "RMS:Margin Exceeds. " + refusal)
            return order
        if needs_trigger:
            buy = side == K.TXN_BUY
            if (buy and book.ltp >= trigger) or (not buy and book.ltp <= trigger):
                side_word = "buy" if buy else "sell"
                self._snapshot(order, S_REJECTED, f"Trigger price for stoploss {side_word} orders is on the "
                                                  "wrong side of the last traded price.")
                return order
            self._snapshot(order, S_TRIGGER)
        else:
            self._snapshot(order, S_OPEN)
        self._advance(order, meta, book, settings)
        return order

    def _meta_of(self, order: dict) -> InstrumentMeta:
        m = order["_sim"]["meta"]
        return InstrumentMeta(exchange=m["exchange"], symbol=m["tradingsymbol"], token=int(order["instrument_token"]),
                              tick_size=float(m["tick_size"]), lot_size=int(m["lot_size"]),
                              instrument_type=m.get("instrument_type", ""), name=m.get("name", ""),
                              strike=float(m.get("strike") or 0), expiry=m.get("expiry", ""),
                              segment=m.get("segment", ""))

    def _match(self, order: dict, book: Book, meta: InstrumentMeta, settings: SimSettings) -> List[Tuple[float, int]]:
        """The fills the order can take right now, best level first: ``[(price, quantity), ...]``; ``[]`` = rest."""
        buy = order["transaction_type"] == K.TXN_BUY
        levels = book.asks if buy else book.bids
        if not levels:                                               # no level list: the touch alone
            touch, touch_qty = (book.ask, book.ask_qty) if buy else (book.bid, book.bid_qty)
            levels = ((touch, touch_qty),) if touch > 0 else ()
        market = (order["_sim"]["eff"] or order["order_type"]) == K.ORDER_TYPE_MARKET
        limit = float(order["price"])
        lot = max(1, meta.lot_size)
        left, fills = int(order["pending_quantity"]), []
        for level, shown in levels:
            if not market and ((buy and _lt(limit, level)) or (not buy and _lt(level, limit))):
                break                                                # this level has not reached the limit
            px = slipped(level, settings.slippage_ticks, meta.tick_size, buy)
            if not market:
                px = min(px, limit) if buy else max(px, limit)       # a limit never fills worse than itself
            px = max(px, meta.tick_size)
            take = left if not settings.partial_fills or shown <= 0 else min(left, shown // lot * lot)
            if take > 0:
                fills.append((px, take))
                left -= take
            if left <= 0 or not settings.partial_fills:
                break
        return fills

    def _advance(self, order: dict, meta: InstrumentMeta, book: Book, settings: SimSettings) -> None:
        """Move one open order as far as the current book allows."""
        if order["status"] == S_TRIGGER:
            buy = order["transaction_type"] == K.TXN_BUY
            trigger = float(order["trigger_price"])
            if not ((buy and book.ltp >= trigger) or (not buy and book.ltp <= trigger)):
                return
            order["_sim"]["eff"] = K.ORDER_TYPE_LIMIT if order["order_type"] == K.ORDER_TYPE_SL else K.ORDER_TYPE_MARKET
            self._snapshot(order, S_OPEN)
        if order["status"] != S_OPEN:
            return
        if order["_sim"].get("rested"):
            refusal = self._check_margin(meta, order, self._expected_price(order, book, meta, settings), settings)
            if refusal:
                order["cancelled_quantity"], order["pending_quantity"] = order["pending_quantity"], 0
                self._snapshot(order, S_REJECTED, "RMS:Margin Exceeds. " + refusal)
                return
        fills = self._match(order, book, meta, settings)
        if fills:
            self._fill(order, meta, book, fills)
        if order["status"] == S_OPEN:
            if order["validity"] == K.VALIDITY_IOC:
                order["cancelled_quantity"], order["pending_quantity"] = order["pending_quantity"], 0
                self._snapshot(order, S_CANCELLED, "IOC order not fully filled")
            else:
                order["_sim"]["rested"] = True
                self.ledger.put_order(self.owner, order)

    def _fill(self, order: dict, meta: InstrumentMeta, book: Book, fills: List[Tuple[float, int]]) -> None:
        """Book ``fills`` (one trade each) and move the order once. Charges follow the ORDER: each trade carries
        its share of what the cumulative order costs, so brokerage is paid once however many levels or passes."""
        sim = order["_sim"]
        done = int(order["filled_quantity"])
        turnover = float(sim.get("turnover") if sim.get("turnover") is not None else float(order["average_price"]) * done)
        paid = sim.get("charged") or {}
        order["exchange_update_timestamp"] = _stamp(self.now())
        for price, quantity in fills:
            turnover, done = turnover + price * quantity, done + quantity
            total = compute_charges(exchange=order["exchange"], tradingsymbol=order["tradingsymbol"],
                                    side=order["transaction_type"], quantity=done, price=turnover / done,
                                    product=order["product"], instrument_type=meta.instrument_type).as_dict()
            share = Charges(*(round(total[k] - paid.get(k, 0.0), 2) for k in _CHARGE_FIELDS))
            paid = total
            self.ledger.record_fill(self.owner, order=order, quantity=quantity, price=price, charges=share,
                                    meta=meta.as_meta(), day=self.today(), mark=book.ltp)
        order["average_price"] = round(turnover / done, 2)
        order["filled_quantity"] = done
        order["pending_quantity"] = int(order["pending_quantity"]) - sum(q for _, q in fills)
        sim["turnover"], sim["charged"] = turnover, paid
        self._snapshot(order, S_COMPLETE if order["pending_quantity"] == 0 else S_OPEN)

    # ── orders: public async API ──────────────────────────────────────────────────────
    async def place_order(self, *, exchange: str, tradingsymbol: str, transaction_type: str, quantity: int,
                          product: str, order_type: str, price: Optional[float] = None,
                          trigger_price: Optional[float] = None, validity: str = K.VALIDITY_DAY,
                          variety: str = K.VARIETY_REGULAR, tag: Optional[str] = None,
                          disclosed_quantity: Optional[int] = None, market_protection: float = -1,
                          gtt_id: Optional[int] = None) -> dict:
        """Place one order. Raises a Kite error for a refusal; returns ``{"order_id": ...}``."""
        ex, side = str(exchange).upper(), str(transaction_type).upper()
        meta = await self.quotes.instrument(ex, tradingsymbol)
        book = await self.quotes.book(ex, tradingsymbol, fresh=True)       # an order meets the market as it is now
        with self.ledger.transaction():
            settings = self.ledger.settings(self.owner)
            self._validate(exchange=ex, side=side, quantity=quantity, product=product, order_type=order_type,
                           price=price, trigger=trigger_price, validity=validity, variety=variety, meta=meta,
                           settings=settings)
            order = self._create(meta=meta, book=book, settings=settings, side=side, quantity=quantity,
                                 product=product, order_type=order_type, price=price, trigger=trigger_price,
                                 validity=validity, variety=variety, tag=tag, disclosed=disclosed_quantity,
                                 market_protection=market_protection, gtt_id=gtt_id)
        self._flush()
        return {"order_id": order["order_id"]}

    async def modify_order(self, order_id: str, *, variety: str = K.VARIETY_REGULAR, quantity: Optional[int] = None,
                           price: Optional[float] = None, trigger_price: Optional[float] = None,
                           order_type: Optional[str] = None, validity: Optional[str] = None,
                           disclosed_quantity: Optional[int] = None) -> dict:
        order = self.ledger.get_order(self.owner, str(order_id))
        if order is None:
            raise _input("Order not found", 404)
        if order["status"] not in OPEN_STATUSES:
            raise _order_error(f"Order cannot be modified: it is {order['status']}.")
        meta = self._meta_of(order)
        book = await self.quotes.book(meta.exchange, meta.symbol, fresh=True)
        with self.ledger.transaction():
            order = self.ledger.get_order(self.owner, str(order_id))
            if order is None or order["status"] not in OPEN_STATUSES:
                raise _order_error("Order cannot be modified: it is no longer open.")
            settings = self.ledger.settings(self.owner)
            new_qty = int(quantity) if quantity is not None else int(order["quantity"])
            new_type = order_type or order["order_type"]
            new_price = float(price) if price is not None else float(order["price"])
            new_trigger = float(trigger_price) if trigger_price is not None else float(order["trigger_price"])
            self._validate(exchange=meta.exchange, side=order["transaction_type"], quantity=new_qty,
                           product=order["product"], order_type=new_type,
                           price=new_price if new_type in (K.ORDER_TYPE_LIMIT, K.ORDER_TYPE_SL) else None,
                           trigger=new_trigger if new_type in (K.ORDER_TYPE_SL, K.ORDER_TYPE_SLM) else None,
                           validity=validity or order["validity"], variety=variety, meta=meta, settings=settings)
            if new_qty <= int(order["filled_quantity"]):
                raise _input("Quantity cannot be reduced to or below the filled quantity.")
            order.update(quantity=new_qty, pending_quantity=new_qty - int(order["filled_quantity"]),
                         order_type=new_type, price=new_price if new_type in (K.ORDER_TYPE_LIMIT, K.ORDER_TYPE_SL) else 0.0,
                         trigger_price=new_trigger if new_type in (K.ORDER_TYPE_SL, K.ORDER_TYPE_SLM) else 0.0,
                         validity=validity or order["validity"],
                         disclosed_quantity=int(disclosed_quantity if disclosed_quantity is not None
                                                else order["disclosed_quantity"]))
            if new_type in (K.ORDER_TYPE_SL, K.ORDER_TYPE_SLM):
                order["_sim"]["eff"] = None
                order["status"] = S_TRIGGER
            else:
                order["_sim"]["eff"] = new_type
                order["status"] = S_OPEN
            self._snapshot(order, order["status"])
            self._advance(order, meta, book, settings)
        self._flush()
        return {"order_id": str(order_id)}

    async def cancel_order(self, order_id: str, *, variety: str = K.VARIETY_REGULAR) -> dict:
        with self.ledger.transaction():
            order = self.ledger.get_order(self.owner, str(order_id))
            if order is None:
                raise _input("Order not found", 404)
            if order["status"] not in OPEN_STATUSES:
                raise _order_error(f"Order cannot be cancelled: it is {order['status']}.")
            order["cancelled_quantity"], order["pending_quantity"] = int(order["pending_quantity"]), 0
            self._snapshot(order, S_CANCELLED)
        self._flush()
        return {"order_id": str(order_id)}

    # ── GTT ───────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _gtt_directions(trigger_type: str, triggers: List[float], reference: float) -> List[str]:
        if trigger_type == K.GTT_TYPE_OCO:
            if not (triggers[0] < reference < triggers[1]):
                raise _input("Two-leg GTT triggers must lie on either side of the last price.")
            return ["down", "up"]
        return ["down" if triggers[0] <= reference else "up"]

    def _gtt_doc(self, trigger_id: int, body: dict, meta: InstrumentMeta, ltp: float,
                 previous: Optional[dict] = None) -> dict:
        """``ltp`` (the sim's own read) is only the fallback: like Kite, a trigger is oriented, and an OCO band
        validated, against the ``last_price`` the REQUEST carries. A stop already past the book then fires on the
        next pass instead of waiting for a rebound."""
        now = self.now()
        try:
            reference = float(body.get("last_price") or 0.0)
        except (TypeError, ValueError):
            reference = 0.0
        reference = reference if reference > 0 else float(ltp)
        triggers = [float(v) for v in body["trigger_values"]]
        legs = [{**leg, "quantity": int(leg["quantity"]), "price": float(leg["price"]), "result": None}
                for leg in body["orders"]]
        return {
            "id": trigger_id, "user_id": self.account_id, "parent_trigger": None, "type": body["trigger_type"],
            "created_at": (previous or {}).get("created_at", _stamp(now)), "updated_at": _stamp(now),
            "expires_at": _stamp(now + timedelta(days=365)), "status": ACTIVE_GTT,
            "condition": {"exchange": meta.exchange, "tradingsymbol": meta.symbol, "trigger_values": triggers,
                          "last_price": float(body["last_price"]), "instrument_token": meta.token},
            "orders": legs, "meta": {},
            "_sim": {"created_ms": ((previous or {}).get("_sim") or {}).get("created_ms") or int(now.timestamp() * 1000),
                     "dirs": self._gtt_directions(body["trigger_type"], triggers, reference), "meta": meta.as_meta()},
        }

    async def place_gtt(self, *, trigger_type: str, tradingsymbol: str, exchange: str, last_price: float,
                        trigger_values, orders) -> dict:
        from app.services.exchanges.kite.client import KiteClient
        KiteClient._gtt_body(trigger_type, tradingsymbol, exchange, last_price, trigger_values, orders)  # Kite's own checks
        ex = str(exchange).upper()
        meta = await self.quotes.instrument(ex, tradingsymbol)
        book = await self.quotes.book(ex, tradingsymbol)
        body = {"trigger_type": trigger_type, "last_price": last_price, "trigger_values": trigger_values, "orders": orders}
        with self.ledger.transaction():
            trigger_id = 9_000_000_000 + self.ledger.next_id("gtt")
            self.ledger.put_gtt(self.owner, self._gtt_doc(trigger_id, body, meta, book.ltp))
        return {"trigger_id": trigger_id}

    async def modify_gtt(self, trigger_id, *, trigger_type: str, tradingsymbol: str, exchange: str,
                         last_price: float, trigger_values, orders) -> dict:
        from app.services.exchanges.kite.client import KiteClient
        KiteClient._gtt_body(trigger_type, tradingsymbol, exchange, last_price, trigger_values, orders)
        current = self.ledger.get_gtt(self.owner, int(trigger_id))
        if current is None:
            raise _input("Trigger not found", 404)
        if current["status"] != ACTIVE_GTT:
            raise _input(f"The trigger is {current['status']} and cannot be modified.")
        ex = str(exchange).upper()
        meta = await self.quotes.instrument(ex, tradingsymbol)
        book = await self.quotes.book(ex, tradingsymbol)
        body = {"trigger_type": trigger_type, "last_price": last_price, "trigger_values": trigger_values, "orders": orders}
        with self.ledger.transaction():
            current = self.ledger.get_gtt(self.owner, int(trigger_id))
            if current is None or current["status"] != ACTIVE_GTT:
                raise _input("Trigger not found", 404)
            self.ledger.put_gtt(self.owner, self._gtt_doc(int(trigger_id), body, meta, book.ltp, previous=current))
        return {"trigger_id": int(trigger_id)}

    async def delete_gtt(self, trigger_id) -> dict:
        with self.ledger.transaction():
            current = self.ledger.get_gtt(self.owner, int(trigger_id))
            if current is None or current["status"] != ACTIVE_GTT:
                raise _input("Trigger not found", 404)        # a spent trigger cannot be deleted
            current["status"] = "cancelled"
            current["updated_at"] = _stamp(self.now())
            self.ledger.put_gtt(self.owner, current)
        return {"trigger_id": int(trigger_id)}

    def _fire_gtt(self, gtt: dict, leg_index: int, book: Book, settings: SimSettings) -> None:
        leg, m = gtt["orders"][leg_index], gtt["_sim"]["meta"]
        meta = InstrumentMeta(exchange=m["exchange"], symbol=m["tradingsymbol"], token=int(gtt["condition"]["instrument_token"]),
                              tick_size=float(m["tick_size"]), lot_size=int(m["lot_size"]),
                              instrument_type=m.get("instrument_type", ""), name=m.get("name", ""),
                              strike=float(m.get("strike") or 0), expiry=m.get("expiry", ""),
                              segment=m.get("segment", ""))
        gtt["status"], gtt["updated_at"] = "triggered", _stamp(self.now())
        result = {"account_id": self.account_id, "exchange": leg["exchange"], "tradingsymbol": leg["tradingsymbol"],
                  "validity": "DAY", "product": leg["product"], "order_type": "LIMIT",
                  "transaction_type": leg["transaction_type"], "quantity": leg["quantity"], "price": leg["price"],
                  "order_result": {"status": "success", "order_id": "", "rejection_reason": ""},
                  "timestamp": _stamp(self.now()), "triggered_at": book.ltp}
        try:
            with self.ledger.savepoint():              # a failed child leaves no half-written order behind
                order = self._create(meta=meta, book=book, settings=settings, side=leg["transaction_type"],
                                     quantity=int(leg["quantity"]), product=leg["product"],
                                     order_type=K.ORDER_TYPE_LIMIT, price=float(leg["price"]), trigger=None,
                                     validity=K.VALIDITY_DAY, variety=K.VARIETY_REGULAR, tag="gtt", disclosed=None,
                                     market_protection=-1, gtt_id=int(gtt["id"]), reject_on_margin=True)
            result["order_result"]["order_id"] = order["order_id"]
            if order["status"] == S_REJECTED:
                result["order_result"].update(status="failed", rejection_reason=order.get("status_message") or "rejected")
        except Exception as exc:  # noqa: BLE001 - the trigger fired but its child could not be placed
            log.warning("sim GTT %s fired but its child order failed: %s", gtt["id"], exc)
            result["order_result"].update(status="failed", rejection_reason=str(exc) or type(exc).__name__)
        leg["result"] = result
        self.ledger.put_gtt(self.owner, gtt)

    # ── evaluation pass ───────────────────────────────────────────────────────────────
    def _guarded(self, report: dict, what: str, fn, *args) -> bool:
        """Run one unit of a pass in its own savepoint: a failure is logged and counted, never fatal to the pass."""
        try:
            with self.ledger.savepoint():
                fn(*args)
            return True
        except Exception as exc:  # noqa: BLE001 - one bad order must not stop every other order and stop
            report["errors"] = report.get("errors", 0) + 1
            log.warning("sim evaluation of %s failed for %s: %s", what, self.owner, exc)
            return False

    def expire_day_orders(self) -> int:
        """DAY orders lapse at the close of their own exchange (and any left over from an earlier day)."""
        now, today, expired = self.now(), self.today(), 0
        with self.ledger.transaction():
            for order in self.ledger.open_orders(self.owner):
                if order["_sim"]["day"] < today or now.time() >= _close_of(now.date(), order["exchange"]):
                    order["cancelled_quantity"], order["pending_quantity"] = int(order["pending_quantity"]), 0
                    self._snapshot(order, S_CANCELLED, "Order expired at the end of the trading session")
                    expired += 1
        return expired

    async def evaluate(self, *, books: Optional[Dict[str, Book]] = None) -> dict:
        """One pass: mark positions, fire GTTs, work resting orders, expire DAY orders.

        Only instruments with a REAL price this pass are touched; the rest wait.
        """
        orders = self.ledger.open_orders(self.owner)
        gtts = self.ledger.gtts(self.owner, status=ACTIVE_GTT)
        positions = self.ledger.positions(self.owner, open_only=True)
        keys = {f"{o['exchange']}:{o['tradingsymbol']}" for o in orders}
        keys |= {f"{g['condition']['exchange']}:{g['condition']['tradingsymbol']}" for g in gtts}
        keys |= {f"{p['exchange']}:{p['tradingsymbol']}" for p in positions}
        report = {"orders_open": len(orders), "gtts_active": len(gtts), "positions": len(positions),
                  "unpriced": 0, "filled": 0, "fired": 0, "expired": 0}
        if keys:
            priced = books if books is not None else await self.quotes.books(sorted(keys), strict=False)
            report["unpriced"] = len(keys - set(priced))
            with self.ledger.transaction():
                # The first read only chose what to price; the world may have moved while the quotes
                # were fetched (a stop cancelled, an order filled), so act on what is true now.
                settings = self.ledger.settings(self.owner)
                for pos in self.ledger.positions(self.owner, open_only=True):
                    book = priced.get(f"{pos['exchange']}:{pos['tradingsymbol']}")
                    if book is not None:
                        self.ledger.set_mark(self.owner, pos["exchange"], pos["tradingsymbol"], pos["product"], book.ltp)
                for gtt in self.ledger.gtts(self.owner, status=ACTIVE_GTT):
                    cond = gtt["condition"]
                    book = priced.get(f"{cond['exchange']}:{cond['tradingsymbol']}")
                    if book is None:
                        continue
                    for i, (direction, trig) in enumerate(zip(gtt["_sim"]["dirs"], cond["trigger_values"])):
                        if (direction == "down" and book.ltp <= trig) or (direction == "up" and book.ltp >= trig):
                            if self._guarded(report, f"GTT {gtt['id']}", self._fire_gtt, gtt, i, book, settings):
                                report["fired"] += 1
                            break
                for order in self.ledger.open_orders(self.owner):
                    book = priced.get(f"{order['exchange']}:{order['tradingsymbol']}")
                    if book is None:
                        continue
                    before = int(order["filled_quantity"])
                    if self._guarded(report, f"order {order['order_id']}", self._advance, order,
                                     self._meta_of(order), book, settings):
                        report["filled"] += int(int(order["filled_quantity"]) > before)
        report["expired"] = self.expire_day_orders()
        self._flush()
        return report

    # ── read surfaces (Kite-shaped, from the ledger) ──────────────────────────────────
    def orders(self) -> List[dict]:
        today = self.today()
        seen = {o["order_id"]: o for o in self.ledger.orders(self.owner, day=today)}
        seen.update({o["order_id"]: o for o in self.ledger.open_orders(self.owner)})
        return [public(o) for o in sorted(seen.values(), key=lambda o: (o["_sim"]["placed_ms"], o["order_id"]))]

    def order_history(self, order_id: str) -> List[dict]:
        rows = self.ledger.history(self.owner, str(order_id))
        if not rows:
            raise _input("Order not found", 404)
        return [public(r) for r in rows]

    def trades(self, order_id: Optional[str] = None) -> List[dict]:
        rows = self.ledger.trades(self.owner, order_id=order_id) if order_id else self.ledger.trades(self.owner, day=self.today())
        return [public(r) for r in rows]

    def gtts(self) -> List[dict]:
        """Active triggers plus those spent in the last ``GTT_LIST_DAYS`` days (older ones are still readable by id)."""
        cutoff = (self.now() - timedelta(days=GTT_LIST_DAYS)).date().isoformat()
        return [public(g) for g in self.ledger.gtts(self.owner)
                if g["status"] == ACTIVE_GTT or str(g.get("updated_at") or "")[:10] >= cutoff]

    def gtt(self, trigger_id) -> dict:
        row = self.ledger.get_gtt(self.owner, int(trigger_id))
        if row is None:
            raise _input("Trigger not found", 404)
        return public(row)

    def positions(self, books: Optional[Dict[str, Book]] = None) -> dict:
        """``{"net": [...], "day": [...]}`` in Kite's shape. Open rows are marked to the last real price."""
        today = self.today()
        day: Dict[tuple, Dict[str, float]] = {}
        for t in self.ledger.trades(self.owner, day=today):
            k = (t["exchange"], t["tradingsymbol"], t["product"])
            d = day.setdefault(k, {"bq": 0, "bv": 0.0, "sq": 0, "sv": 0.0, "realised": 0.0})
            if t["transaction_type"] == "BUY":
                d["bq"] += t["quantity"]
                d["bv"] += t["quantity"] * t["average_price"]
            else:
                d["sq"] += t["quantity"]
                d["sv"] += t["quantity"] * t["average_price"]
            d["realised"] += t["_sim"]["realised"]
        rows = []
        for pos in self.ledger.positions(self.owner):
            k = (pos["exchange"], pos["tradingsymbol"], pos["product"])
            qty = int(pos["quantity"])
            if qty == 0 and k not in day:
                continue
            d = day.get(k, {"bq": 0, "bv": 0.0, "sq": 0, "sv": 0.0, "realised": 0.0})
            book = (books or {}).get(f"{k[0]}:{k[1]}")
            last = float(book.ltp) if book is not None else (float(pos["last_price"]) or float(pos["avg_price"]))
            avg = float(pos["avg_price"])
            unrealised = round((last - avg) * qty, 2) if qty else 0.0
            realised = round(d["realised"], 2)
            avg_buy = round(d["bv"] / d["bq"], 4) if d["bq"] else 0.0
            avg_sell = round(d["sv"] / d["sq"], 4) if d["sq"] else 0.0
            rows.append({
                "tradingsymbol": k[1], "exchange": k[0], "instrument_token": int(pos["instrument_token"]),
                "product": k[2], "quantity": qty, "overnight_quantity": qty - (d["bq"] - d["sq"]), "multiplier": 1,
                "average_price": avg, "close_price": 0, "last_price": last,
                "value": round(d["sv"] - d["bv"], 2), "pnl": round(realised + unrealised, 2),
                "m2m": round(realised + unrealised, 2), "unrealised": unrealised, "realised": realised,
                "buy_quantity": d["bq"], "buy_price": avg_buy, "buy_value": round(d["bv"], 2), "buy_m2m": round(d["bv"], 2),
                "sell_quantity": d["sq"], "sell_price": avg_sell, "sell_value": round(d["sv"], 2),
                "sell_m2m": round(d["sv"], 2), "day_buy_quantity": d["bq"], "day_buy_price": avg_buy,
                "day_buy_value": round(d["bv"], 2), "day_sell_quantity": d["sq"], "day_sell_price": avg_sell,
                "day_sell_value": round(d["sv"], 2),
            })
        return {"net": rows, "day": [dict(r) for r in rows if r["day_buy_quantity"] or r["day_sell_quantity"]]}

    def margins(self, segment: Optional[str] = None) -> dict:
        view = self.ledger.account_view(self.owner, self.today())
        premium = sum(float(p["avg_price"]) * int(p["quantity"]) for p in self.ledger.positions(self.owner, open_only=True)
                      if int(p["quantity"]) > 0 and p["meta"].get("instrument_type") in ("CE", "PE"))
        held = round(view["blocked_margin"] - premium, 2)
        seg = {
            "enabled": True, "net": view["available"],
            "available": {"adhoc_margin": 0, "cash": view["available"], "opening_balance": view["capital"],
                          "live_balance": view["available"], "collateral": 0, "intraday_payin": 0},
            "utilised": {"debits": view["blocked_margin"], "exposure": 0, "m2m_realised": view["realised_today"],
                         "m2m_unrealised": view["unrealised"], "option_premium": round(premium, 2), "payout": 0,
                         "span": held, "holding_sales": 0, "turnover": 0, "liquid_collateral": 0,
                         "stock_collateral": 0, "delivery": 0},
        }
        if segment:
            return seg
        return {"equity": seg, "commodity": {**seg, "enabled": False,
                                              "available": {**seg["available"], "cash": 0, "live_balance": 0},
                                              "net": 0}}


__all__ = [
    "VirtualExchange", "snap", "slipped", "on_tick", "S_OPEN", "S_TRIGGER", "S_COMPLETE", "S_CANCELLED", "S_REJECTED",
    "SESSION_CLOSE", "GTT_LIST_DAYS", "SUPPORTED_EXCHANGES", "MarketDataUnavailable",
]
