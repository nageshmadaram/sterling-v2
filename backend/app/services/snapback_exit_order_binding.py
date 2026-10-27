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
    if type(requested) is not int or requested <= 0 or not broker_order_id:
        raise ValueError("invalid broker order identity/quantity")
    if (pos.position_id != origin.position_id or pos.uid != origin.uid
            or pos.account_id != origin.account_id
            or pos.option_symbol != origin.option_symbol
            or (leg == "hedge" and pos.hedge_symbol != origin.hedge_symbol)):
        raise ValueError("exit position ownership changed after broker ACK")

    field = "option_exit_order_id" if leg == "option" else "hedge_exit_order_id"
    req_field = ("option_exit_requested" if leg == "option"
                 else "hedge_exit_requested")
    total = int(pos.option_qty if leg == "option" else pos.hedge_qty)
    exited = int(pos.option_exit_qty if leg == "option" else pos.hedge_exit_qty)
    if total <= 0 or exited < 0 or exited >= total or requested > total - exited:
        raise ValueError("exit quantity exceeds owned remaining units")
    old_id = str(getattr(pos, field, "") or "")
    old_requested = int(getattr(pos, req_field, 0) or 0)
    if old_id and (old_id != broker_order_id or old_requested != requested):
        raise ValueError("different broker exit order already belongs to this leg")
    setattr(pos, field, broker_order_id)
    setattr(pos, req_field, requested)
