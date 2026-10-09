"""Test doubles for the Simulation suite: a recording 'real' Kite client with no network.

``FakeReal`` answers reads from in-memory quotes and instruments. Every WRITE method records
itself in ``writes`` and then raises, so a test that expects the virtual exchange to stay
virtual asserts ``real.writes == []`` (and would error loudly if anything reached it).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

IST = timezone(timedelta(hours=5, minutes=30))
NIFTY_LOT = 75
OPT = "NFO:NIFTY2611325000CE"
OPT_PE = "NFO:NIFTY2611325000PE"
FUT = "NFO:NIFTY26OCTFUT"


def row(symbol: str, token: int, *, exchange="NFO", itype="CE", strike=0.0, lot=NIFTY_LOT, tick=0.05,
        name="NIFTY", expiry="2026-10-13") -> dict:
    return {"instrument_token": token, "tradingsymbol": symbol, "name": name, "tick_size": tick, "lot_size": lot,
            "instrument_type": itype, "segment": "NFO-OPT" if itype in ("CE", "PE") else "NFO-FUT",
            "exchange": exchange, "strike": strike, "expiry": expiry}


class FakeInstrumentCache:
    def __init__(self, rows: List[dict]) -> None:
        self.rows = rows
        self.loads = 0

    async def load(self, exchange: str = "") -> List[dict]:
        self.loads += 1
        return [r for r in self.rows if not exchange or r["exchange"] == exchange]


class FakeReal:
    """The owner's real Kite client, offline."""

    _is_paper = False

    def __init__(self, owner: str = "default", clock=None) -> None:
        self._owner_user_id = owner
        self._account_id = "REAL-1"
        self._clock = clock or (lambda: datetime.now(IST))
        self.quotes: Dict[str, dict] = {}
        self.calls: List[tuple] = []
        self.writes: List[tuple] = []
        self.fail: Optional[Exception] = None
        self._instruments = FakeInstrumentCache([
            row("NIFTY2611325000CE", 1001, itype="CE", strike=25000.0),
            row("NIFTY2611325000PE", 1002, itype="PE", strike=25000.0),
            row("NIFTY2611324900CE", 1003, itype="CE", strike=24900.0),
            row("NIFTY26OCTFUT", 2001, itype="FUT", expiry="2026-10-27"),
        ])
        self.set_quote(OPT, 100.0)
        self.set_quote(OPT_PE, 100.0)
        self.set_quote(FUT, 25000.0, bid=24999.9, ask=25000.1)

    # ── helpers for tests ────────────────────────────────────────────────────────────
    def set_quote(self, key: str, ltp: float, *, bid: Optional[float] = None, ask: Optional[float] = None,
                  qty: int = 100_000, age_s: float = 1.0, bids=None, asks=None) -> None:
        """``bids`` / ``asks``: optional full depth as ``[(price, quantity), ...]`` best first (else one level of ``qty``)."""
        self.quotes[key] = {"ltp": float(ltp), "bid": round(ltp - 0.05, 2) if bid is None else bid,
                            "ask": round(ltp + 0.05, 2) if ask is None else ask, "qty": qty, "age_s": age_s,
                            "bids": bids, "asks": asks}

    def token(self, key: str) -> int:
        symbol = key.partition(":")[2]
        return next((r["instrument_token"] for r in self._instruments.rows if r["tradingsymbol"] == symbol), 0)

    def payload(self, key: str) -> Optional[dict]:
        q = self.quotes.get(key)
        if q is None:
            return None
        stamp = (self._clock() - timedelta(seconds=q["age_s"])).strftime("%Y-%m-%d %H:%M:%S")
        levels = lambda price: [{"price": price, "quantity": q["qty"], "orders": 5}] if price else []   # noqa: E731
        full = lambda rows: [{"price": p, "quantity": n, "orders": 5} for p, n in rows]                 # noqa: E731
        return {"instrument_token": self.token(key), "timestamp": stamp, "last_trade_time": stamp,
                "last_price": q["ltp"], "volume": 1000, "oi": 1000.0,
                "depth": {"buy": full(q["bids"]) if q.get("bids") else levels(q["bid"]),
                          "sell": full(q["asks"]) if q.get("asks") else levels(q["ask"])}}

    # ── reads ────────────────────────────────────────────────────────────────────────
    async def get_quote(self, instruments):
        self.calls.append(("get_quote", list(instruments)))
        if self.fail is not None:
            raise self.fail
        return {k: p for k in instruments if (p := self.payload(k)) is not None}

    async def get_ltp(self, instruments):
        self.calls.append(("get_ltp", list(instruments)))
        return {k: {"instrument_token": self.token(k), "last_price": self.quotes[k]["ltp"]}
                for k in instruments if k in self.quotes}

    async def get_ohlc(self, instruments):
        self.calls.append(("get_ohlc", list(instruments)))
        return {}

    async def get_historical(self, token, interval, frm, to, continuous=False, oi=False):
        self.calls.append(("get_historical", (token, interval)))
        return {"candles": []}

    async def get_candles(self, instrument, resolution, limit=200):
        self.calls.append(("get_candles", (resolution, limit)))
        return []

    async def search_instruments(self, query, exchange="", limit=50):
        self.calls.append(("search_instruments", (query, exchange)))
        return [r for r in self._instruments.rows if query.upper() in r["tradingsymbol"]][:limit]

    async def instrument_lot_sizes(self, symbols):
        self.calls.append(("instrument_lot_sizes", list(symbols)))
        return {s: NIFTY_LOT for s in symbols}

    async def resolve_token(self, tradingsymbol, exchange="NFO"):
        self.calls.append(("resolve_token", tradingsymbol))
        return self.token(f"{exchange}:{tradingsymbol}")

    # ── writes: forbidden ────────────────────────────────────────────────────────────
    def _write(self, name):
        async def refuse(*args, **kwargs):
            self.writes.append((name, args, kwargs))
            raise AssertionError(f"a REAL broker write reached the real client: {name}")
        return refuse

    def __getattr__(self, name):
        if name in {"place_order", "place_order_option", "place_order_future", "cancel_order", "modify_order",
                    "place_gtt", "modify_gtt", "delete_gtt", "place_mf_order", "cancel_mf_order",
                    "place_mf_sip", "modify_mf_sip", "cancel_mf_sip", "convert_position", "create_alert",
                    "modify_alert", "delete_alerts", "_place", "_auth_post", "_auth_put", "_auth_delete"}:
            return self._write(name)
        raise AttributeError(name)


def ist(h: int = 10, m: int = 16, s: int = 0, *, day=(2026, 10, 12)) -> datetime:
    return datetime(*day, h, m, s, tzinfo=IST)
