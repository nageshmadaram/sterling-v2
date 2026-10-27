"""Pure durable broker ACK binding for Snapback risk-reducing exits.

The broker may have placed a CLOSE order before our SQLite write fails.
Never replace an existing working order, change position ownership or expand
an exit quantity when reconnecting. Exceptions require broker reconciliation,
not a blind retry.
"""
from __future__ import annotations


def bind_acknowledged_exit(pos, origin, *,
                           leg: str, broker_order_id: str, requested: int) -> None:
    """Mutate a registry row only if this ACK belongs to the same owned leg.

    Called inside the position store's BEGIN IMMEDIATE transaction.
    """
    if leg not in {"option", "hedge"}:
        raise ValueError("unsupported exit leg")
    if (type(requested) is not int or requested <= 0
            or type(broker_order_id) is not str
            or not broker_order_id.strip()
            or broker_order_id != broker_order_id.strip()):
        raise ValueError("invalid broker order identity/quantity")
    if (pos.position_id != origin.position_id or pos.uid != origin.uid
            or pos.account_id != origin.account_id
            or pos.option_symbol != origin.option_symbol
            or (leg == "hedge" and pos.hedge_symbol != origin.hedge_symbol)):
        raise ValueError("exit position ownership changed after broker ACK")

    field = "option_exit_order_id" if leg == "option" else "hedge_exit_order_id"
    req_field = ("option_exit_requested" if leg == "option"
                 else "hedge_exit_requested")
    # Corrupt persisted quantities are not allowed to become integers by
    # truncation (including bool and fractional JSON values).
    total = pos.option_qty if leg == "option" else pos.hedge_qty
    exited = pos.option_exit_qty if leg == "option" else pos.hedge_exit_qty
    if type(total) is not int or type(exited) is not int:
        raise ValueError("exit position quantity is not an integer")
    if total <= 0 or exited < 0 or exited >= total or requested > total - exited:
        raise ValueError("exit quantity exceeds owned remaining units")
    old_id = getattr(pos, field, "")
    old_requested = getattr(pos, req_field, 0)
    if type(old_id) is not str or type(old_requested) is not int or old_requested < 0:
        raise ValueError("persisted broker exit identity/quantity is invalid")
    # Even without a stored order ID, a nonzero request means an earlier
    # submission may exist. Never overwrite that unresolved broker attempt.
    if bool(old_id) != bool(old_requested) or (
            old_id and (old_id != broker_order_id or old_requested != requested)):
        raise ValueError("different broker exit order already belongs to this leg")
    setattr(pos, field, broker_order_id)
    setattr(pos, req_field, requested)
