"""Research-only option leg mark-to-market; never a broker fill receipt.

The MARK is an observed candle close or low, not a tradable broker bid.
Futures hedge inventory, borrowing, account collateral and order-book impact
are deliberately NOT included in these option-only measurements.
"""
from __future__ import annotations

import math


def option_leg_mark_equity(
    realized_equity_inr: float, *,
    entry_price: float,
    observed_mark_price: float,
    quantity: int,
    estimated_roundtrip_cost_points: float,
    fixed_roundtrip_cost_inr: float,
) -> float:
    """Return cash equity plus one open option marked at observed premium.

    This marks both the option's price change and its estimated round-trip
    exit cost. If the position is already CLOSED, callers must use actual
    realized equity instead of marking it a second time.
    """
    values = (realized_equity_inr, entry_price, observed_mark_price,
              estimated_roundtrip_cost_points, fixed_roundtrip_cost_inr)
    if (type(quantity) is not int or quantity <= 0
            or any(type(v) not in (int, float) or not math.isfinite(v)
                   for v in values)
            or entry_price <= 0 or observed_mark_price <= 0
            or estimated_roundtrip_cost_points < 0
            or fixed_roundtrip_cost_inr < 0):
        raise ValueError("invalid option mark or round-trip cost inputs")
    return (float(realized_equity_inr)
            + (float(observed_mark_price) - float(entry_price)
               - float(estimated_roundtrip_cost_points)) * quantity
            - float(fixed_roundtrip_cost_inr))
