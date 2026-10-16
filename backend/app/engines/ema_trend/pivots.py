"""Pivots are emitted at confirmation, never at their historic occurrence."""
from collections.abc import Sequence
from .contracts import Bar, Pivot


def confirmed_pivots(bars: Sequence[Bar], left: int = 2, right: int = 2) -> list[Pivot]:
    if left < 1 or right < 1:
        raise ValueError("pivot windows must be positive")
    result = []
    for i in range(left, len(bars)-right):
        before, after = bars[i-left:i], bars[i+1:i+right+1]
        for side in ("HIGH", "LOW"):
            price = bars[i].high if side == "HIGH" else bars[i].low
            qualifies = (all(price > x.high for x in before) and all(price >= x.high for x in after)
                         if side == "HIGH" else
                         all(price < x.low for x in before) and all(price <= x.low for x in after))
            if qualifies:
                prior = next((p for p in reversed(result) if p.side == side and p.confirmed_index <= i), None)
                result.append(Pivot(side=side, price=price, occurred_index=i,
                                    confirmed_index=i+right, occurred_ms=bars[i].close_ms,
                                    confirmed_ms=bars[i+right].close_ms,
                                    previous_price=prior.price if prior else None))
    return result
