"""Pure, fail-closed review of signed cumulative Gamma SELL fill increments.

This module never reads a broker, writes durable state or authorizes an order.
It validates the *complete* per-order delta sequence, not only the final row.
"""
from __future__ import annotations

from hashlib import sha256
import json
from math import isclose, isfinite
from typing import Any, Iterable

_ALLOWED_EXIT_SOURCES = frozenset({"exit", "recovery", "reconciliation"})


def valid_parent_sell_increments(
    rows: Iterable[Any], *, account_id: str, uid: str, symbol: str,
    exchange: str, order_id: str, cumulative_quantity: int,
    cumulative_value: float,
) -> bool:
    """Prove monotone cumulative fills and exact signed/price deltas.

    This verifies only local ledger rows. An authenticated broker book and
    atomic parent-child reservation are independently required and are NOT
    provided by this helper.
    """
    if (type(cumulative_quantity) is not int or cumulative_quantity <= 0
            or type(cumulative_value) not in (int, float)
            or not isfinite(cumulative_value) or cumulative_value <= 0
            or any(type(v) is not str or not v for v in
                   (account_id, uid, symbol, exchange, order_id))):
        return False
    try:
        ordered = sorted(rows, key=lambda row: row["cumulative_quantity"])
        if not ordered:
            return False
        prior_quantity = 0
        prior_value = 0.0
        for row in ordered:
            if (row["account_id"] != account_id or row["uid"] != uid
                    or row["symbol"] != symbol or row["exchange"] != exchange
                    or row["order_id"] != order_id or row["side"] != "SELL"
                    or row["source"] not in _ALLOWED_EXIT_SOURCES):
                return False
            qty = row["cumulative_quantity"]
            signed = row["signed_quantity"]
            value = row["cumulative_value"]
            price = row["price"]
            if (type(qty) is not int or type(signed) is not int
                    or type(value) not in (int, float)
                    or type(price) not in (int, float)
                    or qty <= prior_quantity or not isfinite(value)
                    or value <= prior_value or not isfinite(price)
                    or price <= 0):
                return False
            delta_qty = qty - prior_quantity
            delta_value = value - prior_value
            if (signed != -delta_qty or
                    not isclose(price, delta_value / delta_qty,
                                rel_tol=1e-9, abs_tol=1e-6)):
                return False
            prior_quantity = qty
            prior_value = value
        return (prior_quantity == cumulative_quantity
                and isclose(prior_value, cumulative_value,
                            rel_tol=1e-9, abs_tol=1e-6))
    except (TypeError, ValueError, OverflowError, KeyError, IndexError):
        return False


def parent_sell_increment_fingerprint(rows: Iterable[Any]) -> str:
    """Deterministic hash of every canonical execution delta in a parent SELL.

    The hash is for replay evidence binding in a disposable offline review
    record; it is not an authentication signature or broker authorization.
    Validity must be checked independently with valid_parent_sell_increments.
    """
    observed = sorted(rows, key=lambda row: row["cumulative_quantity"])
    if not observed:
        raise ValueError("parent_increment_history_missing")
    fields = (
        "id", "account_id", "uid", "exchange", "symbol", "order_id",
        "source", "side", "cumulative_quantity", "cumulative_value",
        "signed_quantity", "price",
    )
    payload = [[row[field] for field in fields] for row in observed]
    encoded = json.dumps(["gamma-parent-executions-v1", payload],
                         allow_nan=False, ensure_ascii=True,
                         separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()
