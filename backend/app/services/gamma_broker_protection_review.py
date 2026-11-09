"""Passive, offline Gamma remainder broker-book / protective-GTT evidence review.

Accepts only caller-supplied immutable snapshots. It NEVER calls a broker,
modifies GTTs, reserves an order or returns trading authority. Even a positive
protected state cannot prove the external broker is unchanged at submission.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any


@dataclass(frozen=True)
class ProtectionSnapshotReview:
    protected_for_review: bool
    reason: str
    quantity: int = 0
    trigger_id: int = 0
    execution_authorized: bool = False


def assess_protected_remainder_snapshot(
    *,
    bound_account_id: str,
    expected_account_id: str,
    exchange: str,
    symbol: str,
    product: str,
    remainder_quantity: int,
    recorded_trigger_id: int,
    projected_parent_order_id: str,
    projected_parent_filled_quantity: int,
    broker_positions: Any,
    broker_gtts: Any,
    broker_orders: Any,
    snapshot_completed_ms: int,
    now_ms: int,
    stop_placement_uncertain: bool = False,
    max_age_ms: int = 5000,
) -> ProtectionSnapshotReview:
    """Require exact positive held quantity and active *known* stop evidence.

    The positive result is an informational observation that a stop appears to
    be protecting the remainder; it explicitly does NOT authorize its removal
    or an additional SELL. Broker connection authenticity, timestamp origin,
    completeness of snapshots, order-book races and tenant ownership cannot be
    independently verified from supplied data, hence they are not certified.
    """
    def refuse(reason: str) -> ProtectionSnapshotReview:
        return ProtectionSnapshotReview(False, reason)

    identity = (bound_account_id, expected_account_id, exchange, symbol, product)
    if any(type(x) is not str or not x or x != x.strip() for x in identity):
        return refuse("snapshot_identity_invalid")
    if bound_account_id != expected_account_id:
        return refuse("broker_account_binding_mismatch")
    if (exchange != exchange.upper() or symbol != symbol.upper()
            or product != product.upper() or product not in {"NRML", "CNC", "MTF"}):
        return refuse("broker_bucket_invalid")
    if (type(remainder_quantity) is not int or remainder_quantity <= 0
            or type(recorded_trigger_id) is not int or recorded_trigger_id <= 0
            or type(projected_parent_order_id) is not str
            or not projected_parent_order_id or projected_parent_order_id.strip() != projected_parent_order_id
            or type(projected_parent_filled_quantity) is not int
            or projected_parent_filled_quantity <= 0
            or type(stop_placement_uncertain) is not bool or stop_placement_uncertain):
        return refuse("protection_identity_or_state_unverified")
    if (type(snapshot_completed_ms) is not int or type(now_ms) is not int
            or type(max_age_ms) is not int or not 0 < max_age_ms <= 5000
            or snapshot_completed_ms <= 0 or now_ms <= 0
            or snapshot_completed_ms > now_ms
            or now_ms - snapshot_completed_ms > max_age_ms):
        return refuse("broker_snapshot_stale_or_unverifiable")
    if (not isinstance(broker_positions, dict)
            or type(broker_positions.get("net")) is not list
            or type(broker_gtts) is not list
            or type(broker_orders) is not list):
        return refuse("broker_snapshot_incomplete")

    net_qty = 0
    matching_position = False
    for row in broker_positions["net"]:
        if type(row) is not dict:
            return refuse("broker_position_row_invalid")
        row_symbol = row.get("tradingsymbol")
        row_exchange = row.get("exchange")
        row_product = row.get("product")
        # Ambiguous or missing identity is not a reliable broker net book.
        if any(type(x) is not str or not x for x in
               (row_symbol, row_exchange, row_product)):
            return refuse("broker_position_identity_incomplete")
        if row_exchange != exchange or row_symbol != symbol:
            continue
        if row_product != product:
            quantity = row.get("quantity")
            if type(quantity) is not int:
                return refuse("broker_other_product_quantity_invalid")
            if quantity != 0:
                return refuse("broker_other_product_exposure")
            continue
        qty = row.get("quantity")
        if type(qty) is not int:
            return refuse("broker_position_quantity_invalid")
        net_qty += qty
        matching_position = True
    if not matching_position or net_qty != remainder_quantity:
        return refuse("broker_remainder_not_exact")

    own_trigger = None
    observed_ids: set[int] = set()
    for row in broker_gtts:
        if type(row) is not dict:
            return refuse("broker_gtt_row_invalid")
        gid = row.get("id", row.get("trigger_id"))
        if type(gid) is not int or gid <= 0 or gid in observed_ids:
            return refuse("broker_gtt_identity_invalid")
        observed_ids.add(gid)
        condition = row.get("condition")
        if type(condition) is not dict:
            return refuse("broker_gtt_condition_missing")
        cond_exchange, cond_symbol = condition.get("exchange"), condition.get("tradingsymbol")
        if type(cond_exchange) is not str or type(cond_symbol) is not str:
            return refuse("broker_gtt_contract_unverified")
        if cond_exchange != exchange or cond_symbol != symbol:
            if gid == recorded_trigger_id:
                return refuse("recorded_trigger_contract_mismatch")
            # Broker trigger metadata may be malformed or incorrectly bound.
            # Any order leg that could sell the target contract is a conflict,
            # even when the condition advertises another instrument.
            other_legs = row.get("orders")
            if type(other_legs) is not list:
                return refuse("other_trigger_order_legs_unverified")
            if any(type(leg) is not dict
                   or (leg.get("exchange") == exchange
                       and leg.get("tradingsymbol") == symbol)
                   for leg in other_legs):
                return refuse("other_trigger_could_sell_target_contract")
            continue
        if gid != recorded_trigger_id:
            # Another strategy/user may own this GTT. Do not infer whose stop
            # can later place a SELL, even if status appears terminal.
            return refuse("other_contract_trigger_unverified")
        if str(row.get("status") or "").lower() != "active":
            return refuse("recorded_stop_not_active")
        if row.get("type") != "single":
            # OCO and triggered rows require leg-specific, terminal-order proofs.
            return refuse("recorded_stop_shape_unverified")
        trigger_values = condition.get("trigger_values")
        if (type(trigger_values) is not list or len(trigger_values) != 1
                or type(trigger_values[0]) not in (int, float)
                or not isfinite(float(trigger_values[0])) or trigger_values[0] <= 0):
            return refuse("recorded_stop_trigger_price_unverified")
        legs = row.get("orders")
        if type(legs) is not list or len(legs) != 1 or type(legs[0]) is not dict:
            return refuse("recorded_stop_legs_unverified")
        leg = legs[0]
        if (leg.get("exchange") != exchange or leg.get("tradingsymbol") != symbol
                or leg.get("product") != product
                or leg.get("transaction_type") != "SELL"
                or leg.get("order_type") != "LIMIT"
                or type(leg.get("quantity")) is not int
                or leg["quantity"] != remainder_quantity
                or type(leg.get("price")) not in (int, float)
                or not isfinite(float(leg.get("price", 0)))
                or not 0 < leg["price"] <= trigger_values[0]):
            return refuse("recorded_stop_does_not_protect_remainder")
        own_trigger = row
    if own_trigger is None:
        return refuse("recorded_protective_stop_missing")

    # The supplied order book cannot prove broker order completeness, so even
    # a clean snapshot is only a passive protected-state observation.
    parent_order_seen = False
    for row in broker_orders:
        if type(row) is not dict:
            return refuse("broker_order_row_invalid")
        order_symbol, order_exchange = row.get("tradingsymbol"), row.get("exchange")
        if type(order_symbol) is not str or type(order_exchange) is not str:
            return refuse("broker_order_contract_unverified")
        if order_symbol != symbol or order_exchange != exchange:
            continue
        if row.get("transaction_type") == "SELL":
            if (row.get("order_id") != projected_parent_order_id
                    or row.get("product") != product
                    or row.get("status") != "CANCELLED"
                    or type(row.get("quantity")) is not int
                    or row["quantity"] != projected_parent_filled_quantity + remainder_quantity
                    or type(row.get("filled_quantity")) is not int
                    or row["filled_quantity"] != projected_parent_filled_quantity
                    or parent_order_seen):
                return refuse("contract_sell_order_history_requires_reconciliation")
            parent_order_seen = True
            continue
        if row.get("transaction_type") != "BUY":
            return refuse("contract_order_side_unverified")
        if row.get("status") not in {"COMPLETE", "CANCELLED", "REJECTED"}:
            return refuse("contract_nonterminal_order_present")
    if not parent_order_seen:
        return refuse("projected_parent_order_not_confirmed_in_broker_book")
    return ProtectionSnapshotReview(
        True, "active_protection_observed_snapshot_only",
        remainder_quantity, recorded_trigger_id,
    )
