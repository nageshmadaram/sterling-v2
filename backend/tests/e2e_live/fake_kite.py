"""An offline, in-memory Zerodha Kite REST backend for the end-to-end admission dry run.

The harness does NOT hand the strategies a hand-written Python client. It hands them
the production :class:`~app.services.exchanges.kite.client.KiteClient`, built by
``accounts.build_client`` from a REAL stored live account (``_is_paper = False``,
account id, ``kite_user_id``, generation), and replaces only the wire: the client's
``httpx.AsyncClient`` gets a ``MockTransport`` whose handler is this exchange.

That keeps everything between a strategy and the wire real:

* the transport-level broker-write guards (``guard_broker_write`` /
  ``guard_broker_reduction`` need the canonical capability the service holds),
* order-body construction (product, order type, validity, market protection, tag),
* GTT body validation (MIS refused, a priced LIMIT leg required),
* the instrument cache (CSV dump -> rows), quotes, LTP, margins, positions.

Orders fill the way a liquid book fills: a marketable LIMIT or a MARKET order fills
in full at the touch immediately, a non-marketable LIMIT rests OPEN. Every fill
queues the order postback Kite would push over the websocket; the test delivers it
through the production ``ticker_manager.broadcast_order_update`` so the fill ->
protection path runs exactly as it does live.

Nothing here opens a socket. ``requests`` is the complete audit log of what the
application asked the broker, and ``order_posts`` / ``entry_posts`` are what the
tests assert "an order reached the broker" against.
"""
from __future__ import annotations

import csv
import io
import itertools
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs

import httpx

IST = timezone(timedelta(hours=5, minutes=30))

NIFTY_TOKEN = 256265
NIFTY_LOT = 75
TICK = 0.05

#: Weekly expiry tomorrow (DTE 1, intraday for Gamma), a later monthly and a far one.
WEEKLY_EXPIRY = date(2026, 10, 13)
MONTHLY_EXPIRY = date(2026, 10, 27)
NEXT_MONTHLY_EXPIRY = date(2026, 11, 24)

_MONTH_CODE = {1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8",
               9: "9", 10: "O", 11: "N", 12: "D"}
_MONTH_ABBR = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT",
               "NOV", "DEC")


def weekly_symbol(name: str, expiry: date, strike: int, kind: str) -> str:
    return f"{name}{expiry:%y}{_MONTH_CODE[expiry.month]}{expiry.day:02d}{strike}{kind}"


def monthly_symbol(name: str, expiry: date, strike: int, kind: str) -> str:
    return f"{name}{expiry:%y}{_MONTH_ABBR[expiry.month - 1]}{strike}{kind}"


def future_symbol(name: str, expiry: date) -> str:
    return f"{name}{expiry:%y}{_MONTH_ABBR[expiry.month - 1]}FUT"


def _ts(now: datetime) -> str:
    return now.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")


def _error(status: int, error_type: str, message: str) -> httpx.Response:
    return httpx.Response(status, json={
        "status": "error", "message": message, "error_type": error_type, "data": None})


def _ok(data: Any) -> httpx.Response:
    return httpx.Response(200, json={"status": "success", "data": data})


class FakeKiteExchange:
    """One broker account's view of the market, with a REST handler."""

    def __init__(self, *, kite_user_id: str = "AB1234", user_name: str = "Test Trader",
                 available_cash: float = 1_000_000.0, clock: Optional[Callable[[], datetime]] = None,
                 futures_margin: float = 135_000.0) -> None:
        self.kite_user_id = kite_user_id
        self.user_name = user_name
        self.available_cash = float(available_cash)
        self.futures_margin = float(futures_margin)
        self._clock = clock or (lambda: datetime.now(IST))
        self.spot: Dict[str, float] = {"NIFTY 50": 25000.0}
        self.instruments: List[dict] = []
        self.quotes: Dict[str, dict] = {}
        self.orders: Dict[str, dict] = {}
        self.order_history: Dict[str, List[dict]] = {}
        self.trades: List[dict] = []
        self.gtts: Dict[int, dict] = {}
        self.alerts: List[dict] = []
        self.candles: Dict[Tuple[int, str], List[list]] = {}
        self._book: Dict[Tuple[str, str, str], dict] = {}
        self._oid = itertools.count(2_600_000_001)
        self._gid = itertools.count(91_000_001)
        self._tid = itertools.count(41_000_001)
        #: Every REST call: (METHOD, path, params, body).
        self.requests: List[tuple] = []
        #: Order postbacks Kite would push on the websocket, not yet delivered.
        self.postbacks: List[dict] = []
        #: Fill behaviour overrides keyed by tradingsymbol: "hang" | "reject".
        self.fill_mode: Dict[str, str] = {}
        #: When set, ``place_order`` raises this status (e.g. a 503) instead of filling.
        self.fail_orders_with: Optional[Tuple[int, str, str]] = None
        self.fail_gtt_with: Optional[Tuple[int, str, str]] = None
        self._build_instruments()

    # ------------------------------------------------------------------ market

    def _build_instruments(self) -> None:
        token = itertools.count(8_000_001)
        rows: List[dict] = []

        def add(**kw) -> None:
            base = dict(instrument_token=next(token), exchange_token=0, tradingsymbol="",
                        name="", last_price=0.0, expiry="", strike=0.0, tick_size=TICK,
                        lot_size=1, instrument_type="", segment="", exchange="")
            base.update(kw)
            rows.append(base)

        add(instrument_token=NIFTY_TOKEN, tradingsymbol="NIFTY 50", name="NIFTY 50",
            instrument_type="EQ", segment="INDICES", exchange="NSE")
        for expiry in (MONTHLY_EXPIRY, NEXT_MONTHLY_EXPIRY):
            add(tradingsymbol=future_symbol("NIFTY", expiry), name="NIFTY", expiry=expiry.isoformat(),
                lot_size=NIFTY_LOT, instrument_type="FUT", segment="NFO-FUT", exchange="NFO")
        for strike in range(24000, 26050, 50):
            for kind in ("CE", "PE"):
                add(tradingsymbol=weekly_symbol("NIFTY", WEEKLY_EXPIRY, strike, kind), name="NIFTY",
                    expiry=WEEKLY_EXPIRY.isoformat(), strike=float(strike), lot_size=NIFTY_LOT,
                    instrument_type=kind, segment="NFO-OPT", exchange="NFO")
                for expiry in (MONTHLY_EXPIRY, NEXT_MONTHLY_EXPIRY):
                    add(tradingsymbol=monthly_symbol("NIFTY", expiry, strike, kind), name="NIFTY",
                        expiry=expiry.isoformat(), strike=float(strike), lot_size=NIFTY_LOT,
                        instrument_type=kind, segment="NFO-OPT", exchange="NFO")
        self.instruments = rows

    def instrument(self, tradingsymbol: str, exchange: str = "NFO") -> dict:
        for row in self.instruments:
            if row["tradingsymbol"] == tradingsymbol and row["exchange"] == exchange:
                return row
        raise KeyError(f"{exchange}:{tradingsymbol}")

    def set_quote(self, key: str, ltp: float, *, bid: Optional[float] = None,
                  ask: Optional[float] = None, oi: float = 250_000.0, depth_qty: int = 20_000,
                  age_seconds: float = 2.0) -> None:
        """A live book for ``EXCH:SYMBOL``. Defaults to a tight two-tick spread around ltp."""
        bid = round(ltp - 0.25, 2) if bid is None else bid
        ask = round(ltp + 0.25, 2) if ask is None else ask
        self.quotes[key] = {"last_price": float(ltp), "bid": float(bid), "ask": float(ask),
                            "oi": float(oi), "depth_qty": int(depth_qty), "age": float(age_seconds)}

    def _quote_payload(self, key: str) -> Optional[dict]:
        quote = self.quotes.get(key)
        exchange, _, symbol = key.partition(":")
        if quote is None and exchange == "NSE" and symbol in self.spot:
            quote = {"last_price": self.spot[symbol], "bid": self.spot[symbol],
                     "ask": self.spot[symbol], "oi": 0.0, "depth_qty": 0, "age": 1.0}
        if quote is None:
            return None
        stamp = _ts(self._clock() - timedelta(seconds=quote["age"]))
        try:
            token = self.instrument(symbol, exchange)["instrument_token"]
        except KeyError:
            token = 0
        return {
            "instrument_token": token, "timestamp": stamp, "last_trade_time": stamp,
            "last_price": quote["last_price"], "last_quantity": 75, "volume": 1_000_000,
            "average_price": quote["last_price"], "buy_quantity": 400_000,
            "sell_quantity": 400_000, "oi": quote["oi"], "oi_day_high": quote["oi"],
            "oi_day_low": quote["oi"], "net_change": 0.0,
            "ohlc": {"open": quote["last_price"], "high": quote["last_price"],
                     "low": quote["last_price"], "close": quote["last_price"]},
            "depth": {
                "buy": [{"price": quote["bid"], "quantity": quote["depth_qty"], "orders": 12}],
                "sell": [{"price": quote["ask"], "quantity": quote["depth_qty"], "orders": 12}]},
        }

    # ------------------------------------------------------------------ audit

    def posts(self, path_prefix: str) -> List[tuple]:
        return [r for r in self.requests if r[0] == "POST" and r[1].startswith(path_prefix)]

    @property
    def order_posts(self) -> List[dict]:
        """Every order the application sent (bodies of POST /orders/*)."""
        return [r[3] for r in self.posts("/orders/")]

    def entry_posts(self) -> List[dict]:
        return [b for b in self.order_posts if str(b.get("transaction_type")) == "BUY"]

    def exit_posts(self) -> List[dict]:
        return [b for b in self.order_posts if str(b.get("transaction_type")) == "SELL"]

    @property
    def gtt_posts(self) -> List[dict]:
        return [r[3] for r in self.posts("/gtt/triggers")]

    def active_gtts(self) -> List[dict]:
        return [g for g in self.gtts.values() if g["status"] == "active"]

    def net_quantity(self, tradingsymbol: str, product: str = "") -> int:
        return sum(row["buy_quantity"] - row["sell_quantity"] for (_e, p, s), row in self._book.items()
                   if s == tradingsymbol and (not product or p == product))

    # ------------------------------------------------------------- order engine

    def _record_status(self, order: dict, status: str, **extra: Any) -> None:
        order.update(status=status, **extra)
        order["exchange_update_timestamp"] = _ts(self._clock())
        snap = dict(order)
        self.order_history.setdefault(order["order_id"], []).append(snap)

    def _fill(self, order: dict, price: float) -> None:
        qty = int(order["quantity"])
        order.update(filled_quantity=qty, pending_quantity=0, average_price=round(price, 2))
        self._record_status(order, "COMPLETE")
        key = (order["exchange"], order["product"], order["tradingsymbol"])
        row = self._book.setdefault(key, {"buy_quantity": 0, "sell_quantity": 0,
                                          "buy_value": 0.0, "sell_value": 0.0})
        if order["transaction_type"] == "BUY":
            row["buy_quantity"] += qty
            row["buy_value"] += qty * price
        else:
            row["sell_quantity"] += qty
            row["sell_value"] += qty * price
        self.trades.append({
            "trade_id": str(next(self._tid)), "order_id": order["order_id"],
            "exchange": order["exchange"], "tradingsymbol": order["tradingsymbol"],
            "instrument_token": order["instrument_token"], "product": order["product"],
            "average_price": round(price, 2), "quantity": qty,
            "exchange_order_id": order["exchange_order_id"],
            "transaction_type": order["transaction_type"],
            "fill_timestamp": _ts(self._clock()), "order_timestamp": order["order_timestamp"],
            "exchange_timestamp": _ts(self._clock())})
        self.postbacks.append(dict(order, user_id=self.kite_user_id))

    def _place_order(self, variety: str, body: dict) -> httpx.Response:
        if self.fail_orders_with:
            status, kind, message = self.fail_orders_with
            return _error(status, kind, message)
        symbol = str(body.get("tradingsymbol") or "")
        exchange = str(body.get("exchange") or "")
        try:
            instrument = self.instrument(symbol, exchange)
        except KeyError:
            return _error(400, "InputException", f"Invalid instrument {exchange}:{symbol}")
        side = str(body.get("transaction_type") or "")
        order_type = str(body.get("order_type") or "")
        quantity = int(body.get("quantity") or 0)
        if quantity <= 0 or quantity % int(instrument["lot_size"]):
            return _error(400, "InputException", "Quantity must be a multiple of the lot size.")
        if exchange in ("NFO", "BFO"):
            need = self.premium_required(symbol, exchange, quantity, body)
            if need and need > self.available_cash:
                return _error(400, "MarginException", "Insufficient funds.")
        oid = str(next(self._oid))
        now = self._clock()
        order = {
            "order_id": oid, "exchange_order_id": f"13{oid}", "parent_order_id": None,
            "placed_by": self.kite_user_id, "variety": variety, "status": "PUT ORDER REQ RECEIVED",
            "status_message": None, "status_message_raw": None,
            "order_timestamp": _ts(now), "exchange_update_timestamp": _ts(now),
            "exchange_timestamp": _ts(now), "exchange": exchange, "tradingsymbol": symbol,
            "instrument_token": instrument["instrument_token"], "order_type": order_type,
            "transaction_type": side, "validity": body.get("validity", "DAY"),
            "product": body.get("product"), "quantity": quantity, "disclosed_quantity": 0,
            "price": float(body.get("price") or 0.0), "trigger_price": float(body.get("trigger_price") or 0.0),
            "average_price": 0.0, "filled_quantity": 0, "pending_quantity": quantity,
            "cancelled_quantity": 0, "tag": body.get("tag"), "guid": f"g{oid}",
            "market_protection": body.get("market_protection", 0)}
        self.orders[oid] = order
        self._record_status(order, "PUT ORDER REQ RECEIVED")
        mode = self.fill_mode.get(symbol, "fill")
        quote = self.quotes.get(f"{exchange}:{symbol}") or {}
        if mode == "reject" or not quote:
            self._record_status(order, "REJECTED", status_message="RMS:Rejected")
            self.postbacks.append(dict(order, user_id=self.kite_user_id))
            return _ok({"order_id": oid})
        bid, ask = quote["bid"], quote["ask"]
        marketable = (order_type == "MARKET"
                      or (order_type == "LIMIT" and (order["price"] >= ask if side == "BUY"
                                                     else order["price"] <= bid)))
        if mode == "hang" or not marketable:
            self._record_status(order, "OPEN")
            self.postbacks.append(dict(order, user_id=self.kite_user_id))
            return _ok({"order_id": oid})
        self._fill(order, ask if side == "BUY" else bid)
        return _ok({"order_id": oid})

    def premium_required(self, symbol: str, exchange: str, quantity: int, body: dict) -> float:
        """Cash a BUY needs: the premium for an option, SPAN+exposure for a future."""
        if symbol.endswith("FUT"):
            return self.futures_margin * max(1, quantity // NIFTY_LOT)
        quote = self.quotes.get(f"{exchange}:{symbol}") or {}
        price = float(body.get("price") or 0.0) or float(quote.get("ask") or 0.0)
        return price * quantity

    def _cancel_order(self, oid: str) -> httpx.Response:
        order = self.orders.get(oid)
        if order is None:
            return _error(404, "InputException", "Order not found")
        if order["status"] in ("OPEN", "TRIGGER PENDING"):
            order["cancelled_quantity"] = order["pending_quantity"]
            order["pending_quantity"] = 0
            self._record_status(order, "CANCELLED")
            self.postbacks.append(dict(order, user_id=self.kite_user_id))
        return _ok({"order_id": oid})

    # --------------------------------------------------------------------- GTT

    def _gtt_payload(self, body: dict, trigger_id: int, *, previous: Optional[dict] = None) -> dict:
        condition = json.loads(body["condition"]) if isinstance(body.get("condition"), str) else body["condition"]
        legs = json.loads(body["orders"]) if isinstance(body.get("orders"), str) else body["orders"]
        now = self._clock()
        try:
            token = self.instrument(condition["tradingsymbol"], condition["exchange"])["instrument_token"]
        except KeyError:
            token = 0
        return {
            "id": trigger_id, "user_id": self.kite_user_id, "parent_trigger": None,
            "type": body.get("type"), "created_at": (previous or {}).get("created_at", _ts(now)),
            "updated_at": _ts(now), "expires_at": _ts(now + timedelta(days=365)),
            "status": "active",
            "condition": {**condition, "instrument_token": token},
            "orders": [{**leg, "result": None} for leg in legs], "meta": {}}

    def fire_gtt(self, trigger_id: int, price: float) -> dict:
        """The broker-side stop fires: its LIMIT sell fills and the trigger is spent."""
        gtt = self.gtts[int(trigger_id)]
        gtt["status"] = "triggered"
        leg = gtt["orders"][0]
        oid = str(next(self._oid))
        now = self._clock()
        instrument = self.instrument(leg["tradingsymbol"], leg["exchange"])
        order = {
            "order_id": oid, "exchange_order_id": f"13{oid}", "parent_order_id": None,
            "placed_by": self.kite_user_id, "variety": "regular", "status": "PUT ORDER REQ RECEIVED",
            "order_timestamp": _ts(now), "exchange_update_timestamp": _ts(now),
            "exchange_timestamp": _ts(now), "exchange": leg["exchange"],
            "tradingsymbol": leg["tradingsymbol"], "instrument_token": instrument["instrument_token"],
            "order_type": "LIMIT", "transaction_type": leg["transaction_type"],
            "validity": "DAY", "product": leg["product"], "quantity": int(leg["quantity"]),
            "price": float(leg["price"]), "trigger_price": 0.0, "average_price": 0.0,
            "filled_quantity": 0, "pending_quantity": int(leg["quantity"]), "cancelled_quantity": 0,
            "tag": "", "guid": f"g{oid}"}
        self.orders[oid] = order
        self._fill(order, price)
        return order

    # ------------------------------------------------------------- positions

    def _positions_payload(self) -> dict:
        net = []
        for (exchange, product, symbol), row in sorted(self._book.items()):
            quantity = row["buy_quantity"] - row["sell_quantity"]
            if quantity > 0:
                average = row["buy_value"] / row["buy_quantity"]
            elif quantity < 0:
                average = row["sell_value"] / row["sell_quantity"]
            else:
                average = 0.0
            last = (self.quotes.get(f"{exchange}:{symbol}") or {}).get("last_price", average)
            try:
                token = self.instrument(symbol, exchange)["instrument_token"]
            except KeyError:
                token = 0
            realised = ((row["sell_value"] / row["sell_quantity"] - row["buy_value"] / row["buy_quantity"])
                        * min(row["buy_quantity"], row["sell_quantity"])
                        if row["buy_quantity"] and row["sell_quantity"] else 0.0)
            net.append({
                "tradingsymbol": symbol, "exchange": exchange, "instrument_token": token,
                "product": product, "quantity": quantity, "overnight_quantity": 0, "multiplier": 1,
                "average_price": round(average, 4), "close_price": 0, "last_price": last,
                "value": row["sell_value"] - row["buy_value"], "pnl": realised, "m2m": realised,
                "unrealised": 0.0, "realised": realised,
                "buy_quantity": row["buy_quantity"], "buy_price": 0, "buy_value": row["buy_value"],
                "sell_quantity": row["sell_quantity"], "sell_price": 0, "sell_value": row["sell_value"],
                "day_buy_quantity": row["buy_quantity"], "day_sell_quantity": row["sell_quantity"]})
        return {"net": net, "day": [dict(r) for r in net]}

    # ------------------------------------------------------------------ router

    def _instrument_csv(self, exchange: str) -> str:
        columns = ["instrument_token", "exchange_token", "tradingsymbol", "name", "last_price",
                   "expiry", "strike", "tick_size", "lot_size", "instrument_type", "segment", "exchange"]
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in self.instruments:
            if not exchange or row["exchange"] == exchange:
                writer.writerow(row)
        return out.getvalue()

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = request.url.params
        body: Any = None
        raw = request.content or b""
        if raw:
            ctype = request.headers.get("content-type", "")
            if "json" in ctype:
                body = json.loads(raw)
            else:
                body = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
        self.requests.append((request.method, path, dict(params), body))
        method = request.method

        if path.startswith("/instruments/historical/"):
            _, _, rest = path.partition("/instruments/historical/")
            token, _, interval = rest.partition("/")
            candles = self.candles.get((int(token), interval), [])
            return _ok({"candles": candles})
        if path == "/instruments" or path.startswith("/instruments/"):
            exchange = path.rpartition("/")[2] if path != "/instruments" else ""
            return httpx.Response(200, text=self._instrument_csv(exchange))
        if path == "/user/profile":
            return _ok({"user_id": self.kite_user_id, "user_name": self.user_name,
                        "user_shortname": self.user_name.split()[0], "email": "trader@example.invalid",
                        "user_type": "individual", "broker": "ZERODHA"})
        if path.startswith("/user/margins"):
            cash = self.available_cash
            seg = {"enabled": True, "net": cash,
                   "available": {"adhoc_margin": 0, "cash": cash, "opening_balance": cash,
                                 "live_balance": cash, "collateral": 0, "intraday_payin": 0},
                   "utilised": {"debits": 0, "exposure": 0, "m2m_realised": 0, "m2m_unrealised": 0,
                                "option_premium": 0, "payout": 0, "span": 0, "holding_sales": 0,
                                "turnover": 0, "liquid_collateral": 0, "stock_collateral": 0, "delivery": 0}}
            if path.rstrip("/") == "/user/margins":
                return _ok({"equity": seg, "commodity": {**seg, "enabled": False}})
            return _ok(seg)
        if path == "/quote":
            keys = params.get_list("i")
            out = {k: p for k in keys if (p := self._quote_payload(k)) is not None}
            return _ok(out)
        if path == "/quote/ltp":
            keys = params.get_list("i")
            out = {}
            for k in keys:
                payload = self._quote_payload(k)
                if payload is not None:
                    out[k] = {"instrument_token": payload["instrument_token"],
                              "last_price": payload["last_price"]}
            return _ok(out)
        if path == "/quote/ohlc":
            keys = params.get_list("i")
            out = {}
            for k in keys:
                payload = self._quote_payload(k)
                if payload is not None:
                    out[k] = {"instrument_token": payload["instrument_token"],
                              "last_price": payload["last_price"], "ohlc": payload["ohlc"]}
            return _ok(out)
        if path == "/margins/orders":
            rows = []
            for leg in body or []:
                is_future = str(leg.get("tradingsymbol", "")).endswith("FUT")
                qty = int(leg.get("quantity") or 0)
                price = float(leg.get("price") or 0.0) or float(
                    (self.quotes.get(f"{leg.get('exchange')}:{leg.get('tradingsymbol')}") or {}).get("ask") or 0.0)
                total = self.futures_margin * max(1, qty // NIFTY_LOT) if is_future else price * qty
                rows.append({"type": "equity", "tradingsymbol": leg.get("tradingsymbol"),
                             "exchange": leg.get("exchange"), "span": total if is_future else 0,
                             "exposure": 0, "option_premium": 0 if is_future else total,
                             "additional": 0, "bo": 0, "cash": 0, "var": 0,
                             "pnl": {"realised": 0, "unrealised": 0}, "leverage": 1, "charges": {},
                             "total": total})
            return _ok(rows)
        if path == "/margins/basket":
            rows = []
            total = 0.0
            for leg in body or []:
                is_future = str(leg.get("tradingsymbol", "")).endswith("FUT")
                qty = int(leg.get("quantity") or 0)
                price = float(leg.get("price") or 0.0) or float(
                    (self.quotes.get(f"{leg.get('exchange')}:{leg.get('tradingsymbol')}") or {}).get("ask") or 0.0)
                leg_total = self.futures_margin * max(1, qty // NIFTY_LOT) if is_future else price * qty
                total += leg_total
                rows.append({"tradingsymbol": leg.get("tradingsymbol"), "exchange": leg.get("exchange"),
                             "total": leg_total})
            return _ok({"initial": {"total": total}, "final": {"total": total}, "orders": rows})
        if path == "/charges/orders":
            rows = []
            for leg in body or []:
                value = float(leg.get("average_price") or leg.get("price") or 0.0) * int(leg.get("quantity") or 0)
                fee = round(20.0 + value * 0.0005, 2)
                rows.append({**leg, "charges": {"transaction_tax": 0, "brokerage": 20.0,
                                                 "exchange_turnover_charge": round(value * 0.0003, 2),
                                                 "sebi_turnover_charge": 0.01, "stamp_duty": 0,
                                                 "gst": {"total": 0}, "total": fee}})
            return _ok(rows)
        if path.startswith("/orders"):
            parts = [p for p in path.split("/") if p]
            if method == "POST" and len(parts) == 2:
                return self._place_order(parts[1], body or {})
            if method == "DELETE" and len(parts) == 3:
                return self._cancel_order(parts[2])
            if method == "PUT" and len(parts) == 3:
                return _ok({"order_id": parts[2]})
            if method == "GET" and len(parts) == 1:
                return _ok([dict(o) for o in self.orders.values()])
            if method == "GET" and len(parts) == 2:
                if parts[1] not in self.order_history:
                    return _error(404, "InputException", "Order not found")
                return _ok([dict(h) for h in self.order_history[parts[1]]])
            if method == "GET" and len(parts) == 3 and parts[2] == "trades":
                return _ok([t for t in self.trades if t["order_id"] == parts[1]])
        if path == "/trades":
            return _ok(list(self.trades))
        if path == "/portfolio/positions":
            return _ok(self._positions_payload())
        if path == "/portfolio/holdings":
            return _ok([])
        if path == "/portfolio/holdings/auctions":
            return _ok([])
        if path == "/alerts":
            return _ok(list(self.alerts))
        if path.startswith("/gtt/triggers"):
            parts = [p for p in path.split("/") if p]
            if method == "POST" and len(parts) == 2:
                if self.fail_gtt_with:
                    status, kind, message = self.fail_gtt_with
                    return _error(status, kind, message)
                trigger_id = next(self._gid)
                self.gtts[trigger_id] = self._gtt_payload(body or {}, trigger_id)
                return _ok({"trigger_id": trigger_id})
            if method == "GET" and len(parts) == 2:
                return _ok([dict(g) for g in self.gtts.values()])
            trigger_id = int(parts[2]) if len(parts) == 3 else 0
            if method == "GET" and trigger_id:
                if trigger_id not in self.gtts:
                    return _error(404, "InputException", "Trigger not found")
                return _ok(dict(self.gtts[trigger_id]))
            if method == "PUT" and trigger_id:
                if trigger_id not in self.gtts:
                    return _error(404, "InputException", "Trigger not found")
                self.gtts[trigger_id] = self._gtt_payload(body or {}, trigger_id, previous=self.gtts[trigger_id])
                return _ok({"trigger_id": trigger_id})
            if method == "DELETE" and trigger_id:
                if trigger_id not in self.gtts:
                    return _error(404, "InputException", "Trigger not found")
                self.gtts[trigger_id]["status"] = "cancelled"
                return _ok({"trigger_id": trigger_id})
        return _error(404, "GeneralException", f"fake kite: unmocked endpoint {method} {path}")

    # ----------------------------------------------------------------- wiring

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def async_client(self, base_url: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=base_url, transport=self.transport(), timeout=10.0,
                                 headers={"X-Kite-Version": "3"})
