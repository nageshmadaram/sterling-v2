"""Read-only live EMA exit admission: never issues Kite order/GTT mutations.

This is an evidence barrier before a future canonical SELL adapter. It is not
an execution token: it makes NO order-write API callable and always reports
live_order_authorized=False. If any broker fact is missing or ambiguous, block.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.engines.ema_trend.live_contracts import LiveCampaign


@dataclass(frozen=True)
class ExitPreflight:
    status: str
    blockers: tuple[str, ...]
    broker_quantity: int | None
    read_only: bool = True
    live_order_authorized: bool = False


_TERMINAL = frozenset({"COMPLETE", "CANCELLED", "CANCELED", "REJECTED", "EXPIRED"})
_INERT_GTT = frozenset({"CANCELLED", "CANCELED", "DISABLED", "DELETED",
                        "EXPIRED", "REJECTED"})


def _qty(raw: Any) -> int:
    if isinstance(raw, bool):
        raise ValueError("INVALID_BROKER_QUANTITY")
    try:
        value=Decimal(str(raw))
        if not value.is_finite() or value!=int(value):
            raise ValueError()
        return int(value)
    except (ValueError,InvalidOperation,OverflowError,TypeError) as exc:
        raise ValueError("INVALID_BROKER_QUANTITY") from exc


def _matches(row: dict, campaign: LiveCampaign) -> bool:
    return (str(row.get("exchange") or "").upper() == campaign.exchange
            and str(row.get("tradingsymbol") or "").upper() == campaign.symbol)


def _inventory(campaign: LiveCampaign, positions: dict, holdings: list) -> int:
    if not isinstance(positions,dict) or not isinstance(positions.get("net"),list):
        raise ValueError("BROKER_NET_POSITIONS_UNREADABLE")
    if not isinstance(holdings,list):
        raise ValueError("BROKER_HOLDINGS_UNREADABLE")
    net=0
    for row in positions["net"]:
        if not isinstance(row,dict):
            raise ValueError("BROKER_POSITION_ROW_INVALID")
        if not _matches(row,campaign):
            continue
        if str(row.get("product") or "").upper()!=campaign.product:
            # Another product's position cannot be assumed to be covered by
            # this campaign's CNC GTT or MIS stop.
            if _qty(row.get("quantity")):
                raise ValueError("OTHER_BROKER_PRODUCT_HAS_SAME_SYMBOL_EXPOSURE")
            continue
        net += _qty(row.get("quantity"))
    settled=0
    if campaign.product=="CNC":
        for row in holdings:
            if not isinstance(row,dict):
                raise ValueError("BROKER_HOLDING_ROW_INVALID")
            if not _matches(row,campaign):
                continue
            # Holdings and same-day net positions are different broker books;
            # without settled/T1 attribution it is unsafe to add them.
            held=_qty(row.get("quantity"))
            t1=_qty(row.get("t1_quantity",0))
            collateral=_qty(row.get("collateral_quantity",0))
            if t1 or collateral:
                raise ValueError("UNSETTLED_OR_PLEDGED_HOLDINGS_NOT_AUTHORIZED")
            settled+=held
        if settled and net:
            raise ValueError("BROKER_POSITION_HOLDING_OVERLAP_UNRESOLVED")
        return net or settled
    return net


async def inspect_exit(campaign: LiveCampaign, broker_client) -> ExitPreflight:
    """Read exact broker positions/orders/GTTs and detect competing exits."""
    from app.services.exchanges.kite.client import KiteClient

    blockers=[]
    if (not isinstance(broker_client,KiteClient)
            or getattr(broker_client,"_is_paper",None) is not False
            or getattr(broker_client,"_account_id",None)!=campaign.account_id):
        return ExitPreflight("BLOCKED",("LIVE_CLIENT_ACCOUNT_BINDING_NOT_PROVEN",),None)
    if campaign.held_qty<=0:
        blockers.append("CAMPAIGN_OWNED_QUANTITY_NOT_POSITIVE")
    if campaign.phase in {"CLOSED","RECOVERY_REQUIRED","PREPARED"}:
        blockers.append("CAMPAIGN_NOT_SAFE_FOR_EXIT_OR_RECONCILIATION_REQUIRED")
    if campaign.orphan_stop_ids or campaign.protection_submission_unknown:
        blockers.append("ORPHAN_OR_UNKNOWN_BROKER_STOP")
    if any(item.status not in {"COMPLETE","CANCELLED","REJECTED"} for item in campaign.exits):
        blockers.append("EXIT_ALREADY_PENDING_OR_UNKNOWN")
    if any(item.status in {"SUBMITTING","UNKNOWN","RESERVED","ACKNOWLEDGED","PARTIAL"} for item in campaign.entries):
        blockers.append("ENTRY_MAY_FILL_AFTER_EXIT_DECISION")
    if blockers:
        return ExitPreflight("BLOCKED",tuple(sorted(set(blockers))),None)

    try:
        positions,holdings,orders,gtts=await asyncio.wait_for(
            asyncio.gather(
                broker_client.get_positions_raw(),broker_client.get_holdings(),
                broker_client.get_orders(),broker_client.get_gtts(),
            ),timeout=10)
    except Exception:
        return ExitPreflight("BLOCKED",("BROKER_EXIT_FACTS_UNAVAILABLE",),None)
    try:
        qty=_inventory(campaign,positions,holdings)
    except ValueError as exc:
        return ExitPreflight("BLOCKED",(str(exc),),None)
    if qty!=campaign.held_qty:
        blockers.append("BROKER_OWNERSHIP_QUANTITY_DISAGREES_WITH_CANONICAL_LEDGER")
    if not isinstance(orders,list) or not all(isinstance(row,dict) for row in orders):
        blockers.append("BROKER_ORDER_BOOK_UNREADABLE")
    else:
        for row in orders:
            if not _matches(row,campaign):
                continue
            if str(row.get("status") or "").upper() not in _TERMINAL:
                # A resting GTT child, manual SELL or additional BUY might
                # change held inventory between this read and the next call.
                blockers.append("UNRESOLVED_BROKER_ORDER_FOR_SYMBOL")
                break
    if not isinstance(gtts,list) or not all(isinstance(row,dict) for row in gtts):
        blockers.append("BROKER_GTT_BOOK_UNREADABLE")
    else:
        for row in gtts:
            condition=row.get("condition")
            if isinstance(condition,str):
                import json
                try:
                    condition=json.loads(condition)
                except (TypeError,ValueError):
                    blockers.append("GTT_CONDITION_UNREADABLE")
                    break
            if not isinstance(condition,dict):
                blockers.append("GTT_CONDITION_UNREADABLE")
                break
            if (str(condition.get("exchange") or "").upper()==campaign.exchange
                    and str(condition.get("tradingsymbol") or "").upper()==campaign.symbol
                    and str(row.get("status") or "").upper() not in _INERT_GTT):
                blockers.append("ACTIVE_OR_FIRED_GTT_REQUIRES_CHILD_RECONCILIATION")
                break
    # The store may still claim an old stop active while broker says it is
    # missing; do not infer the GTT was cancelled without a verified history.
    if campaign.protection is not None:
        blockers.append("PROTECTION_MUST_BE_RECONCILED_BEFORE_SELL")
    return ExitPreflight(
        "READ_ONLY_VERIFIED" if not blockers else "BLOCKED",
        tuple(sorted(set(blockers))),qty,
    )
