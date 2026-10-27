"""Fail-closed terminal Snapback exit projection within a position-store transaction.

A broker's terminal fill belongs to a particular owner, account, contract and
working order. An old asynchronous callback must never book it against a
changed/partly closed position. This helper does no I/O; callers perform it in
the existing BEGIN IMMEDIATE registry mutation.
"""
from __future__ import annotations

import math


def project_terminal_exit(
    current, origin, *, leg: str, order_id: str, requested: int,
    filled: int, average: float,
) -> None:
    """Book one terminal order once; raise if state no longer matches its ACK.

    A zero-fill CANCELLED/REJECTED order creates no projection. The caller
    increments the attempt counter atomically when retiring the terminal ACK
    so a restart cannot double-count a cancellation.
    """
    if leg not in {"option", "hedge"}:
        raise ValueError("invalid exit leg")
    if (type(order_id) is not str or not order_id.strip()
            or order_id != order_id.strip()
            or type(requested) is not int or requested <= 0
            or type(filled) is not int or not 0 <= filled <= requested
            or type(average) not in (int, float)
            or not math.isfinite(average) or average < 0
            or (filled > 0 and average <= 0)):
        raise ValueError("invalid terminal broker fill receipt")
    if (current.position_id != origin.position_id
            or current.uid != origin.uid
            or current.account_id != origin.account_id
            or current.option_exchange != origin.option_exchange
            or current.option_symbol != origin.option_symbol
            or (leg == "hedge"
                and (current.hedge_exchange != origin.hedge_exchange
                     or current.hedge_symbol != origin.hedge_symbol
                     or current.hedge_side != origin.hedge_side))
            or current.status == "CLOSED"):
        raise ValueError("exit owner, contract or position state changed")

    prefix = "option" if leg == "option" else "hedge"
    if (getattr(current, f"{prefix}_exit_order_id") != order_id
            or getattr(current, f"{prefix}_exit_requested") != requested):
        raise ValueError("working broker exit ACK has changed")
    total = getattr(current, f"{prefix}_qty")
    exited = getattr(current, f"{prefix}_exit_qty")
    booked = getattr(current, f"{prefix}_exit_booked_ids")
    quantities = getattr(current, f"{prefix}_exit_booked_quantities", None)
    if (type(total) is not int or type(exited) is not int
            or total <= 0 or not 0 <= exited <= total
            or not isinstance(booked, list)
            or any(type(oid) is not str for oid in booked)
            or not isinstance(quantities, dict)):
        raise ValueError("durable option/hedge exit quantity is inconsistent")
    if order_id in booked:
        prior_fill = quantities.get(order_id)
        if type(prior_fill) is not int or prior_fill != filled:
            # Do not silently treat a larger newly observed cumulative
            # broker fill as previously booked. An old pre-migration row,
            # or a changed cumulative fill, requires human reconciliation.
            raise ValueError("previously booked broker order cumulative fill unverified")
        return  # Exactly the same broker fill: recovery is idempotent.
    if order_id in quantities or filled > total - exited:
        raise ValueError("broker exit fill conflicts with remaining owned position")

    if filled:
        value = getattr(current, f"{prefix}_exit_value")
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("durable option/hedge exit value is not finite")
        setattr(current, f"{prefix}_exit_qty", exited + filled)
        setattr(current, f"{prefix}_exit_value", value + filled * average)
        quantities[order_id] = filled
        booked.append(order_id)
