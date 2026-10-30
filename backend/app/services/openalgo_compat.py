"""Read-only OpenAlgo-style response adapter over Sterling canonical broker evidence.

No endpoint is exposed by importing this module. It does not authenticate
requests, touch a broker, route orders or claim API parity. New HTTP adapters
must enforce Sterling's existing JWT/grants and the canonical execution gate,
not the OpenAlgo apikey convention, before exposing any capability.
"""
from __future__ import annotations

import math
from typing import Any

# OpenAlgo-style spellings for read responses, not a complete OpenAlgo API.
_FINAL = {"FILLED": "complete", "PARTIAL": "partially_filled",
          "REJECTED": "rejected", "CANCELLED": "cancelled"}
_ACCEPTED = {"SUBMITTED": "pending_confirmation", "ACKNOWLEDGED": "pending_confirmation"}
_UNRESOLVED = {"UNKNOWN", "SUBMITTING", "RESERVED"}
_SENTINELS = {"", "PENDING", "UNKNOWN", "SUBMITTING"}


def _error(code: str, message: str) -> dict:
    return {"status": "error", "code": code, "message": message}


def canonical_order_status(intent: Any) -> dict:
    """Render broker-evidenced order state without inventing fills or ACKs.

    All fields are from a durable journal intent. A broker ACK is a PENDING
    order, not a COMPLETE fill. UNKNOWN never produces an orderid/success.
    """
    if intent is None:
        return _error("ORDER_NOT_FOUND", "No canonical order intent was found")
    state = str(getattr(intent, "state", "") or "").strip().upper()
    if state in _UNRESOLVED:
        return _error("ORDER_OUTCOME_UNRESOLVED",
                      "Broker outcome requires reconciliation before another order")
    if state not in _FINAL and state not in _ACCEPTED:
        return _error("UNKNOWN_JOURNAL_STATE", "Unrecognized canonical order state")
    oid = str(getattr(intent, "order_id", "") or "").strip()
    if oid.upper() in _SENTINELS:
        return _error("BROKER_ACK_MISSING", "No attributable broker order identifier")
    side = str(getattr(intent, "side", "") or "").strip().upper()
    exchange = str(getattr(intent, "exchange", "") or "").strip().upper()
    symbol = str(getattr(intent, "symbol", "") or "").strip()
    payload = getattr(intent, "payload", None)
    if not isinstance(payload, dict):
        return _error("INVALID_JOURNAL_PAYLOAD", "Canonical order metadata unavailable")
    product = str(payload.get("product") or "").strip().upper()
    quantity = getattr(intent, "quantity", None)
    filled = getattr(intent, "filled_quantity", None)
    value = getattr(intent, "filled_value", None)
    if (side not in {"BUY", "SELL"} or not exchange or not symbol
            or product not in {"MIS", "NRML", "CNC"} or type(quantity) is not int
            or quantity <= 0 or type(filled) is not int or not 0 <= filled <= quantity
            or type(value) not in (int, float) or not math.isfinite(value) or value < 0):
        return _error("INVALID_JOURNAL_IDENTITY", "Order evidence has inconsistent fields")
    if ((state == "FILLED" and (filled != quantity or value <= 0))
            or (state == "PARTIAL" and not (0 < filled < quantity and value > 0))):
        return _error("CONFLICTING_BROKER_FILL", "Broker fill evidence requires reconciliation")
    if state in _ACCEPTED and filled:
        return _error("CONFLICTING_BROKER_FILL", "Broker ACK cannot imply a confirmed fill")
    if state in {"CANCELLED", "REJECTED"} and filled and value <= 0:
        return _error("CONFLICTING_BROKER_FILL", "Partial fill price requires reconciliation")
    average = value / filled if filled else 0.0
    return {
        "status": "success",
        "data": {
            "orderid": oid,
            "order_status": (_FINAL.get(state) or _ACCEPTED[state]),
            "action": side,
            "exchange": exchange,
            "symbol": symbol,
            "product": product,
            "quantity": str(quantity),
            "filled_quantity": str(filled),
            "average_price": round(average, 8),
        },
    }
