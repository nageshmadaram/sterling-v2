"""Test-only: a broker double and board rows for the Snapback live path.

The double behaves like a LIVE Kite transport as far as the execution path can
tell: it states its mode and account, fills (or refuses, or goes silent) on
command, keeps a net position book, and holds GTT stops. Nothing here is imported
by production code.
"""
from __future__ import annotations

import itertools
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

IST = timezone(timedelta(hours=5, minutes=30))

#: A weekday session with the holiday calendar verified for it.
SWING_NOW = datetime(2026, 10, 7, 9, 40, tzinfo=IST)
SWING_SIGNAL_SESSION = datetime(2026, 10, 6, 0, 0, tzinfo=IST)
SCALP_NOW = datetime(2026, 10, 7, 11, 0, tzinfo=IST)

OPTION_SYMBOL = "NIFTY26NOV25000PE"
OPTION = f"NFO:{OPTION_SYMBOL}"
FUTURE_SYMBOL = "NIFTY26NOVFUT"
SMALL_OPTION_SYMBOL = "RELIANCE26NOV1500PE"


class LiveBroker:
    """A live-mode Kite double. ``mode`` decides what an order does."""

    _is_paper = False

    def __init__(self, *, account_id: str = "KITE-LIVE-1", capital: float = 1_000_000.0,
                 option_bid: float = 299.0, option_ask: float = 300.0, mode: str = "fill",
                 hedge_mode: Optional[str] = None, margin: float = 120_000.0,
                 spot: float = 25000.0, fut_bid: float = 25000.0, fut_ask: float = 25001.0):
        self._account_id = account_id
        self.capital = capital
        self.mode = mode
        self.hedge_mode = hedge_mode or mode
        self.margin = margin
        self.quotes: Dict[str, Tuple[float, float, float]] = {
            f"NFO:{OPTION_SYMBOL}": (option_bid, option_ask, (option_bid + option_ask) / 2),
            f"NFO:{FUTURE_SYMBOL}": (fut_bid, fut_ask, fut_ask),
            "NSE:NIFTY 50": (spot, spot, spot),
            f"NFO:{SMALL_OPTION_SYMBOL}": (99.0, 100.0, 99.5),
            "NSE:RELIANCE": (1500.0, 1500.0, 1500.0),
        }
        self.orders: Dict[str, Dict[str, Any]] = {}
        self.placed: List[Dict[str, Any]] = []
        self.net: Dict[Tuple[str, str, str], int] = {}
        self.gtts: Dict[int, Dict[str, Any]] = {}
        self.cancelled_orders: List[str] = []
        self.deleted_gtts: List[int] = []
        self._oid = itertools.count(1000)
        self._gid = itertools.count(7000)
        self.place_calls = 0

    # ------------------------------------------------------------- account

    async def get_margins(self, segment: Optional[str] = None):
        return {"available": {"live_balance": self.capital, "cash": self.capital}}

    async def order_margins(self, orders):
        return [{"total": self.margin}]

    # --------------------------------------------------------------- market

    def set_quote(self, key: str, bid: float, ask: float, ltp: Optional[float] = None) -> None:
        self.quotes[key] = (bid, ask, ltp if ltp is not None else (bid + ask) / 2)

    async def get_quote(self, keys):
        now = datetime.now(timezone.utc)
        out = {}
        for key in keys:
            bid, ask, ltp = self.quotes.get(key, (0.0, 0.0, 0.0))
            out[key] = {
                "last_price": ltp,
                "depth": {"buy": [{"price": bid, "quantity": 5000}],
                          "sell": [{"price": ask, "quantity": 5000}]},
                "oi": 250_000.0, "timestamp": now, "last_trade_time": now,
            }
        return out

    async def get_ltp(self, keys):
        return {k: {"last_price": self.quotes.get(k, (0, 0, 0))[2]} for k in keys}

    async def search_instruments(self, query="", exchange="", limit=0, *a, **k):
        if exchange == "NFO":
            return [
                {"tradingsymbol": FUTURE_SYMBOL, "name": "NIFTY", "instrument_type": "FUT",
                 "expiry": "2026-11-26", "lot_size": 75, "instrument_token": 900001,
                 "exchange": "NFO", "tick_size": 0.05},
                {"tradingsymbol": "NIFTY26OCTFUT", "name": "NIFTY", "instrument_type": "FUT",
                 "expiry": "2026-10-29", "lot_size": 75, "instrument_token": 900000,
                 "exchange": "NFO", "tick_size": 0.05},
            ]
        return []

    # ---------------------------------------------------------------- orders

    async def place_order(self, symbol=None, side=None, size=None, order_type="market_order",
                          limit_price=None, **kwargs):
        self.place_calls += 1
        symbol = symbol or kwargs.get("tradingsymbol")
        side = str(side or kwargs.get("transaction_type") or "").upper()
        qty = int(size if size is not None else kwargs.get("quantity"))
        exchange = str(kwargs.get("exchange") or "NFO")
        product = str(kwargs.get("product") or "NRML")
        is_future = str(symbol).endswith("FUT")
        mode = self.hedge_mode if is_future else self.mode
        self.placed.append({"symbol": symbol, "side": side, "qty": qty, "exchange": exchange,
                            "product": product, "order_type": order_type,
                            "limit_price": limit_price, "tag": kwargs.get("tag")})
        if mode == "reject":
            from app.services.exchanges.kite.errors import KiteOrderError

            raise KiteOrderError("order rejected by the exchange", error_type="OrderException")
        if mode == "unknown":
            raise TimeoutError("broker call timed out; outcome unknown")
        oid = f"ORD{next(self._oid)}"
        bid, ask, _ = self.quotes.get(f"{exchange}:{symbol}", (100.0, 100.0, 100.0))
        price = ask if side == "BUY" else bid
        order = {"order_id": oid, "tradingsymbol": symbol, "exchange": exchange,
                 "transaction_type": side, "product": product, "quantity": qty,
                 "tag": kwargs.get("tag"), "filled_quantity": 0, "average_price": 0.0,
                 "status": "OPEN"}
        if mode == "fill":
            self._fill(order, qty, price)
        elif mode == "partial":
            self._fill(order, max(1, qty // 2), price, status="OPEN")
        # "hang": stays OPEN with nothing filled
        self.orders[oid] = order
        return {"order_id": oid}

    def _fill(self, order, qty, price, status="COMPLETE"):
        order.update(filled_quantity=qty, average_price=price, status=status)
        key = (order["exchange"], order["product"], order["tradingsymbol"])
        signed = qty if order["transaction_type"] == "BUY" else -qty
        self.net[key] = self.net.get(key, 0) + signed

    async def cancel_order(self, order_id, *a, **k):
        order = self.orders.get(order_id)
        if order is not None and order["status"] == "OPEN":
            order["status"] = "CANCELLED"
        self.cancelled_orders.append(order_id)
        return {"order_id": order_id}

    async def get_order_history(self, order_id):
        order = self.orders.get(order_id)
        if order is None:
            return []
        return [{"status": order["status"], "filled_quantity": order["filled_quantity"],
                 "average_price": order["average_price"]}]

    async def get_order_trades(self, order_id):
        return []

    async def get_orders(self):
        return [dict(o) for o in self.orders.values()]

    async def get_trades(self):
        return []

    async def get_positions_raw(self):
        return {"net": [{"exchange": e, "product": p, "tradingsymbol": s, "quantity": q}
                        for (e, p, s), q in self.net.items() if q != 0], "day": []}

    # ------------------------------------------------------------------ GTT

    async def place_gtt(self, **kw):
        gid = next(self._gid)
        self.gtts[gid] = {**kw, "status": "active"}
        return {"trigger_id": gid}

    async def modify_gtt(self, trigger_id, **kw):
        self.gtts[int(trigger_id)] = {**kw, "status": "active"}
        return {"trigger_id": trigger_id}

    async def delete_gtt(self, trigger_id):
        self.deleted_gtts.append(int(trigger_id))
        self.gtts.pop(int(trigger_id), None)
        return {"trigger_id": trigger_id}

    async def get_gtt(self, trigger_id):
        gtt = self.gtts.get(int(trigger_id))
        if gtt is None:
            raise KeyError("no such trigger")
        return {"id": int(trigger_id), "status": gtt["status"]}

    async def get_gtts(self):
        return [{"id": g, "status": v["status"]} for g, v in self.gtts.items()]

    # ---------------------------------------------------------- test helpers

    def fire_gtt(self, trigger_id: int, price: float) -> None:
        """The broker's stop fired: a SELL filled and the trigger is consumed."""
        gtt = self.gtts[int(trigger_id)]
        gtt["status"] = "triggered"
        leg = gtt["orders"][0]
        oid = f"GTTORD{next(self._oid)}"
        order = {"order_id": oid, "tradingsymbol": gtt["tradingsymbol"],
                 "exchange": gtt["exchange"], "transaction_type": "SELL",
                 "product": leg["product"], "quantity": leg["quantity"], "tag": None,
                 "filled_quantity": 0, "average_price": 0.0, "status": "OPEN"}
        self._fill(order, int(leg["quantity"]), price)
        self.orders[oid] = order


def swing_row(*, signal_id: str = "snapback:fade_up:NIFTY:1791244800000",
              option_type: str = "PE", side: str = "fade_up", lot_size: int = 75,
              delta: float = -0.7, ask: float = 300.0, symbol: str = "NIFTY",
              option_symbol: str = OPTION_SYMBOL, spot: float = 25000.0,
              cash_tradingsymbol: str = "NIFTY 50", **over) -> Dict[str, Any]:
    """A board row as ``snapshot`` serves it for a swing signal on the 6th."""
    row = {
        "signal_id": signal_id, "strategy": "snapback", "side": side, "symbol": symbol,
        "state": "armed", "reason": None, "origin": "live_quote", "direction": "BEARISH",
        "opt_type": option_type, "timestamp_ms": int(SWING_SIGNAL_SESSION.timestamp() * 1000),
        "spot": spot, "mean_target": spot * 0.97, "distance_pct": 3.3, "stretch": 2.1,
        "strength": "STRONG", "underlying_token": 738561, "cash_exchange": "NSE",
        "cash_tradingsymbol": cash_tradingsymbol, "assumed_iv_pct": 22.0,
        "contract": {"symbol": option_symbol, "exchange": "NFO", "strike": 25000.0,
                     "option_type": option_type, "expiry": "2026-11-26", "dte": 50,
                     "lot_size": lot_size, "token": 4321, "delta": delta},
        "premium": ask, "premium_is_modelled": False,
        "quote": {"premium": ask, "bid": ask - 1, "ask": ask, "blockers": []},
        "stop_premium": round(ask * 0.65, 2), "target_premium": round(ask * 1.4, 2),
        "lots": 1, "quantity": lot_size, "hold_days": 15,
    }
    row.update(over)
    return row


def scalp_row(*, signal_id: str = "snapback:fade_up:NIFTY:1791250000000",
              close_ms: Optional[int] = None, **over) -> Dict[str, Any]:
    row = swing_row(signal_id=signal_id)
    row.update({
        "trading_mode": "scalp", "signal_close_ms": close_ms
        if close_ms is not None else int((SCALP_NOW - timedelta(seconds=30)).timestamp() * 1000),
        "timestamp_ms": int((SCALP_NOW - timedelta(minutes=1)).timestamp() * 1000),
    })
    row.update(over)
    return row


def stub_beta(value: float = 1.0):
    """A beta provider that needs no daily candles."""
    from app.services.snapback_beta_provenance import canonical_index_beta

    async def beta(row, client):
        snap = canonical_index_beta(symbol="NIFTY", signal_session="2026-10-06")
        if value == 1.0:
            return snap
        from dataclasses import replace

        return replace(snap, clamped_beta=value, raw_beta=value)

    return beta
