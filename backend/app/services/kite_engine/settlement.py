"""Conservative proof of settled CNC inventory, without assuming T+1 delivery.

Quantity is inventory evidence only. It is not proof of depository sale
authorisation or ownership by a particular strategy.
"""
from datetime import date
from decimal import Decimal
from decimal import InvalidOperation


def whole_quantity(value) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError("INVALID_BROKER_QUANTITY")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("INVALID_BROKER_QUANTITY") from exc
    if not number.is_finite() or number != number.to_integral_value():
        raise ValueError("INVALID_BROKER_QUANTITY")
    return int(number)


def settled_holding(rows: list, exchange: str, symbol: str, asof: date) -> dict:
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("INVALID_HOLDINGS_PAYLOAD")
    matches = [row for row in rows if str(row.get("exchange", "")).upper() == exchange.upper()
               and str(row.get("tradingsymbol", "")).upper() == symbol.upper()
               and str(row.get("product", "")).upper() == "CNC"]
    if len(matches) != 1:
        return {"quantity": 0, "sale_authorised": False,
                "reason": "HOLDING_ABSENT_OR_AMBIGUOUS"}
    row = matches[0]
    if row.get("discrepancy") is not False:
        return {"quantity": 0, "sale_authorised": False, "reason": "HOLDING_DISCREPANCY_UNVERIFIED"}
    quantity = whole_quantity(row.get("quantity"))
    realised = whole_quantity(row.get("realised_quantity"))
    used = whole_quantity(row.get("used_quantity"))
    collateral = whole_quantity(row.get("collateral_quantity"))
    t1 = whole_quantity(row.get("t1_quantity"))
    if min(quantity, realised, used, collateral, t1) < 0:
        raise ValueError("NEGATIVE_HOLDING_COMPONENT")
    # Do not add T+1 quantity. Both realised and total settled fields must agree
    # on coverage; pledged/used units cannot be offered as new protection.
    available = max(0, min(quantity, realised) - used - collateral)
    authorised = whole_quantity(row.get("authorised_quantity", 0))
    authorisation_day = str(row.get("authorised_date") or "")[:10]
    return {"quantity": available,
            "sale_authorised": available > 0 and authorised >= available and authorisation_day == asof.isoformat(),
            "reason": "SETTLED_INVENTORY_ONLY" if available else "NO_UNENCUMBERED_SETTLED_INVENTORY"}
