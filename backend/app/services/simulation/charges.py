"""Zerodha-style statutory and broker charges for a virtual fill.

The Simulation journal reports P&L both gross and net, so every virtual fill is charged the
way Zerodha would charge the real one. Rates live in one frozen :class:`ChargeSchedule` so a
rate change is one edit, and every component is rounded to paise the way a contract note is.

Option and futures rules (what Sterling trades), per executed order:

* brokerage: options a flat INR 20 (the cap is always reached); futures the lower of INR 20
  and 0.03% of turnover. Equity delivery is free; equity intraday is the lower of the two.
* STT: sell side only; 0.1% of premium for options, 0.02% for futures.
* exchange transaction charge, on turnover (premium for options): NSE options 0.03503%,
  NSE futures 0.00183%, BSE options 0.0325%, BSE futures nil.
* SEBI turnover fee: INR 10 per crore (0.0001%).
* stamp duty: buy side only; 0.003% options, 0.002% futures.
* GST: 18% of brokerage + exchange transaction charge + SEBI fee.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Optional

_PAISE = Decimal("0.01")
BUY = "BUY"
SELL = "SELL"
KIND_OPTION = "option"
KIND_FUTURE = "future"
KIND_EQUITY = "equity"


def _d(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _money(value: Decimal) -> float:
    return float(value.quantize(_PAISE, rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class ChargeSchedule:
    """Every rate as a fraction of turnover (0.001 == 0.1%)."""

    brokerage_flat: Decimal = Decimal("20")
    brokerage_pct_cap: Decimal = Decimal("0.0003")        # lower-of rule: futures, equity intraday
    stt_option_sell: Decimal = Decimal("0.001")
    stt_future_sell: Decimal = Decimal("0.0002")
    stt_equity_delivery: Decimal = Decimal("0.001")        # both sides
    stt_equity_intraday_sell: Decimal = Decimal("0.00025")
    exchange_nse_option: Decimal = Decimal("0.0003503")
    exchange_nse_future: Decimal = Decimal("0.0000183")
    exchange_bse_option: Decimal = Decimal("0.000325")
    exchange_bse_future: Decimal = Decimal("0")
    exchange_nse_equity: Decimal = Decimal("0.0000297")
    exchange_bse_equity: Decimal = Decimal("0.0000375")
    sebi: Decimal = Decimal("0.000001")
    stamp_option_buy: Decimal = Decimal("0.00003")
    stamp_future_buy: Decimal = Decimal("0.00002")
    stamp_equity_delivery_buy: Decimal = Decimal("0.00015")
    stamp_equity_intraday_buy: Decimal = Decimal("0.00003")
    gst: Decimal = Decimal("0.18")


DEFAULT_SCHEDULE = ChargeSchedule()


@dataclass(frozen=True)
class Charges:
    """Itemised charges of one fill (or a sum of fills). Every figure is in INR."""

    turnover: float = 0.0
    brokerage: float = 0.0
    stt: float = 0.0
    exchange_txn: float = 0.0
    sebi: float = 0.0
    stamp: float = 0.0
    gst: float = 0.0
    total: float = 0.0

    def as_dict(self) -> dict:
        return {"turnover": self.turnover, "brokerage": self.brokerage, "stt": self.stt,
                "exchange_txn": self.exchange_txn, "sebi": self.sebi, "stamp": self.stamp,
                "gst": self.gst, "total": self.total}

    def __add__(self, other: "Charges") -> "Charges":
        return Charges(*(round(a + b, 2) for a, b in zip(
            (self.turnover, self.brokerage, self.stt, self.exchange_txn, self.sebi,
             self.stamp, self.gst, self.total),
            (other.turnover, other.brokerage, other.stt, other.exchange_txn, other.sebi,
             other.stamp, other.gst, other.total))))


ZERO = Charges()


def total_of(items: Iterable[Charges]) -> Charges:
    out = ZERO
    for item in items:
        out = out + item
    return out


def instrument_kind(tradingsymbol: str = "", instrument_type: str = "") -> str:
    """``option`` / ``future`` / ``equity`` from the instrument master row, else the symbol."""
    kind = str(instrument_type or "").strip().upper()
    if kind in ("CE", "PE"):
        return KIND_OPTION
    if kind == "FUT":
        return KIND_FUTURE
    if kind in ("EQ", "INDEX", "BE"):
        return KIND_EQUITY
    symbol = str(tradingsymbol or "").strip().upper().rpartition(":")[2]
    if symbol.endswith("FUT"):
        return KIND_FUTURE
    if symbol.endswith(("CE", "PE")) and any(ch.isdigit() for ch in symbol):
        return KIND_OPTION
    return KIND_EQUITY


def compute_charges(
    *, exchange: str, tradingsymbol: str, side: str, quantity: int, price: float,
    product: str = "NRML", instrument_type: str = "",
    schedule: Optional[ChargeSchedule] = None,
) -> Charges:
    """Charges for one executed order of ``quantity`` at ``price``."""
    s = schedule or DEFAULT_SCHEDULE
    qty, px = int(quantity), _d(price)
    if qty <= 0 or px <= 0:
        raise ValueError("charges need a positive quantity and price")
    side = str(side).upper()
    if side not in (BUY, SELL):
        raise ValueError(f"unknown side {side!r}")
    bse = str(exchange or "").upper() in ("BSE", "BFO")
    kind = instrument_kind(tradingsymbol, instrument_type)
    delivery = str(product or "").upper() == "CNC"
    turnover = px * qty

    if kind == KIND_OPTION:
        brokerage = s.brokerage_flat
        stt = turnover * s.stt_option_sell if side == SELL else Decimal(0)
        exch = turnover * (s.exchange_bse_option if bse else s.exchange_nse_option)
        stamp = turnover * s.stamp_option_buy if side == BUY else Decimal(0)
    elif kind == KIND_FUTURE:
        brokerage = min(s.brokerage_flat, turnover * s.brokerage_pct_cap)
        stt = turnover * s.stt_future_sell if side == SELL else Decimal(0)
        exch = turnover * (s.exchange_bse_future if bse else s.exchange_nse_future)
        stamp = turnover * s.stamp_future_buy if side == BUY else Decimal(0)
    elif delivery:
        brokerage = Decimal(0)
        stt = turnover * s.stt_equity_delivery
        exch = turnover * (s.exchange_bse_equity if bse else s.exchange_nse_equity)
        stamp = turnover * s.stamp_equity_delivery_buy if side == BUY else Decimal(0)
    else:
        brokerage = min(s.brokerage_flat, turnover * s.brokerage_pct_cap)
        stt = turnover * s.stt_equity_intraday_sell if side == SELL else Decimal(0)
        exch = turnover * (s.exchange_bse_equity if bse else s.exchange_nse_equity)
        stamp = turnover * s.stamp_equity_intraday_buy if side == BUY else Decimal(0)

    sebi = turnover * s.sebi
    gst = (brokerage + exch + sebi) * s.gst
    items = {"brokerage": _money(brokerage), "stt": _money(stt), "exchange_txn": _money(exch),
             "sebi": _money(sebi), "stamp": _money(stamp), "gst": _money(gst)}
    total = round(sum(items.values()), 2)
    return Charges(turnover=_money(turnover), total=total, **items)


__all__ = [
    "BUY", "SELL", "KIND_OPTION", "KIND_FUTURE", "KIND_EQUITY", "ChargeSchedule",
    "DEFAULT_SCHEDULE", "Charges", "ZERO", "total_of", "instrument_kind", "compute_charges",
]
