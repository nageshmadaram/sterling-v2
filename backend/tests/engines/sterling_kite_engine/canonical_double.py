"""Test doubles that speak the canonical execution service's language.

SuperTrend entries now go through
:func:`app.services.kite_engine.canonical_entry.submit_entry`, which submits via
``CanonicalExecutionService``. That service sends with ``place_order`` — the
generic transport entry — and requires the client to state which account it is
authenticated against.

The doubles in these tests were written against the old bypass: they implement
``place_order_option`` / ``place_order_future`` and nothing else. Rather than
rewrite every assertion, this mixin teaches a double the two things the
canonical service needs, and routes the canonical send back onto whichever
vehicle-specific recorder the test already asserts on.

Applying it is not a way of keeping the old path alive: the order still passes
the risk approval, the safety recheck at the broker boundary and the durable
journal. Only the last hop — which recorder captures the call — is mapped back.
"""
from __future__ import annotations

__all__ = ["CanonicalBrokerDouble"]


class CanonicalBrokerDouble:
    """Give a fake Kite client an account identity and a ``place_order``."""

    #: The canonical service refuses to send unless the client names the account
    #: it is authenticated against and that name matches the request.
    _account_id = "test-account"
    _is_paper = False

    async def get_margins(self, segment: str = "equity"):
        """A simulated account still has to be able to state its balance.

        An entry — paper or live — is refused when capital cannot be read, so a
        double that cannot answer this question can no longer place anything.
        """
        return {"available": {"live_balance": 1_000_000.0, "cash": 1_000_000.0}}

    #: Live entries are refused unless the venue answers for ONE contract by exact
    #: tradingsymbol with complete metadata, and quotes it freshly. A double that
    #: cannot do that can no longer open a position, so it gets a default answer
    #: derived from what the test already states: the lot size it declares (1 when
    #: it says nothing, which divides any quantity) and the price its own
    #: ``get_ltp`` reports. A double that defines either method itself wins.
    lot_size = None
    #: Lot sizes the suite's board rows declare, matched by underlying prefix.
    _LOTS_BY_UNDERLYING = (("BANKNIFTY", 35), ("NIFTY", 75), ("RELIANCE", 250))

    def _lot_for(self, symbol: str) -> int:
        if self.lot_size:
            return int(self.lot_size)
        for prefix, lot in self._LOTS_BY_UNDERLYING:
            if symbol.upper().startswith(prefix):
                return lot
        return 1

    async def search_instruments(self, query="", exchange=None, limit=0, *args, **kwargs):
        from datetime import date, timedelta
        symbol = str(query or "")
        if not symbol:
            return []
        upper = symbol.upper()
        kind = "FUT" if upper.endswith("FUT") else ("PE" if upper.endswith("PE") else "CE")
        return [dict(tradingsymbol=symbol, exchange=exchange or (args[0] if args else None) or "NFO",
                     lot_size=self._lot_for(symbol), tick_size=0.05, instrument_type=kind,
                     expiry=(date.today() + timedelta(days=10)).isoformat())]

    async def get_quote(self, keys):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        prices = await self.get_ltp(list(keys)) if hasattr(self, "get_ltp") else {}
        out = {}
        for key in keys:
            entry = prices.get(key) or prices.get(str(key).split(":")[-1]) or {}
            last = entry.get("last_price") if isinstance(entry, dict) else entry
            out[key] = dict(last_price=float(last or 100.0), timestamp=now, last_trade_time=now)
        return out

    async def place_order(self, symbol=None, side=None, size=None, **kwargs):
        # The canonical service passes every identity spelling it knows; take
        # whichever the double was built to read.
        symbol = symbol or kwargs.get("tradingsymbol") or kwargs.get("symbol")
        side = side or kwargs.get("transaction_type") or kwargs.get("side")
        quantity = size if size is not None else kwargs.get("quantity")

        for drop in ("tradingsymbol", "transaction_type", "quantity", "symbol",
                     "side", "size", "price"):
            kwargs.pop(drop, None)

        # A Kite futures tradingsymbol ends in FUT; an option's ends in CE/PE.
        # That is how the real contract master distinguishes them, so it is how
        # the double picks which recorder the test is watching.
        is_future = str(symbol or "").upper().endswith("FUT")
        place = getattr(self, "place_order_future" if is_future else "place_order_option", None)
        if place is None:
            place = getattr(self, "place_order_option", None) or getattr(self, "place_order_future")
        return await place(symbol, str(side or "").lower(), quantity, **kwargs)
