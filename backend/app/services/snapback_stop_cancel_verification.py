"""Pure identity verification for a triggered Snapback stop after cancellation.

Kite's order book can be incomplete, duplicated or temporarily stale. A count
of rows is not proof that every expected broker order is terminal. Missing,
duplicated or still-working stop child IDs must block a replacement market SELL.
"""
from __future__ import annotations

from typing import Any

_TERMINAL = frozenset({"COMPLETE", "CANCELLED", "REJECTED"})


def verified_terminal_stop_children(
    expected_order_ids: set[str],
    order_book: Any,
) -> list[dict[str, Any]] | None:
    """Return the exact owned terminal children, or None when evidence is weak.

    Non-owned orders are ignored; each expected owned ID must occur exactly
    once with a terminal broker state. This does not itself prove fill quantity,
    which must still be independently reconciled before placing a market exit.
    """
    if (not isinstance(expected_order_ids, (set, frozenset))
            or not expected_order_ids
            or any(type(oid) is not str or not oid.strip()
                   or oid != oid.strip() for oid in expected_order_ids)
            or not isinstance(order_book, (list, tuple))):
        return None

    found: dict[str, dict[str, Any]] = {}
    for order in order_book:
        if not isinstance(order, dict):
            continue
        oid = order.get("order_id")
        if type(oid) is not str or oid not in expected_order_ids:
            continue
        if (oid in found
                or str(order.get("status") or "").upper() not in _TERMINAL):
            return None
        found[oid] = order
    if len(found) != len(expected_order_ids):
        return None
    return list(found.values())
