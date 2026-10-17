"""Read-only preflight for promoting EMA Trend to broker execution.

This module performs no order or GTT writes. A healthy Kite login is necessary,
not sufficient, for permission to take real exposure. Never derive live approval
from the existence of the paper simulation engine.
"""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from app.engines.ema_trend.config import PROFILES
from app.services.exchanges.kite import accounts, auth
from app.services.kite_engine import order_journal
from . import config_store, repository as repo


def _positive_finite(value) -> bool:
    try:
        amount = Decimal(str(value))
    except (TypeError, InvalidOperation, ValueError):
        return False
    return amount.is_finite() and amount > 0


def _order_exposure_unresolved(order: dict, selected: set[str]) -> bool:
    """Whether broker order facts can still change, or conceal, EMA exposure.

    A terminal order can be PARTIALLY FILLED. A complete BUY may not have
    appeared in a later portfolio read yet. Without an attributed live EMA
    ledger, *any* confirmed fill on the selected symbol requires reconciliation.
    """
    exchange = str(order.get("exchange") or "").strip().upper()
    symbol = str(order.get("tradingsymbol") or "").strip().upper()
    if not exchange or not symbol:
        return True  # cannot prove which configured symbol the order affects
    if f"{exchange}:{symbol}" not in selected:
        return False
    try:
        quantity = Decimal(str(order["quantity"]))
        filled = Decimal(str(order["filled_quantity"]))
        if (not quantity.is_finite() or not filled.is_finite()
                or quantity != int(quantity) or filled != int(filled)
                or quantity <= 0 or filled < 0 or filled > quantity):
            return True
    except (KeyError, ValueError, TypeError, InvalidOperation):
        return True
    if filled:
        return True  # even cancelled/rejected orders may have confirmed fills
    # A COMPLETE status with zero fills is contradictory broker evidence.
    # A pending/unknown/AMO order may execute later.
    return str(order.get("status") or "").strip().upper() not in {
        "CANCELLED", "CANCELED", "REJECTED", "EXPIRED"
    }


def _gtt_exposure_unresolved(trigger: dict, selected: set[str]) -> bool:
    """Inspect a GTT's exact symbol; malformed payloads never imply safety."""
    cond = trigger.get("condition")
    if isinstance(cond, str):
        try:
            cond = json.loads(cond)
        except (ValueError, TypeError):
            return True
    if not isinstance(cond, dict):
        return True
    exchange = str(cond.get("exchange") or "").strip().upper()
    symbol = str(cond.get("tradingsymbol") or "").strip().upper()
    if not exchange or not symbol:
        return True
    if f"{exchange}:{symbol}" not in selected:
        return False
    # A triggered GTT can have a resting/unfilled child even if the trigger
    # itself is no longer active. Explicit inert status is necessary.
    return str(trigger.get("status") or "").strip().lower() not in {
        "cancelled", "canceled", "disabled", "deleted", "expired", "rejected"
    }


def _mode_blockers(mode: str) -> list[str]:
    product = PROFILES[mode].product
    # A Kite GTT sends a LIMIT order on trigger; it is never a guarantee that
    # the position exits. Existing EMA code has no canonical LIVE fill/protect
    # projector for either product. The shared GTT helper also rejects MIS.
    issues = ["EMA_LIVE_CAMPAIGN_LEDGER_AND_ORDER_RECOVERY_NOT_WIRED",
              "EMA_LIVE_ENTRY_PROTECTION_AND_TSL_NOT_CERTIFIED",
              "BROKER_EXIT_FILLABILITY_NOT_GUARANTEED"]
    if product == "MIS":
        issues.append("MIS_BROKER_PROTECTIVE_ORDER_PATH_NOT_WIRED")
    else:
        issues.append("CNC_HOLDINGS_AUTHORIZATION_NOT_VERIFIED")
    return issues


async def inspect(uid: str) -> dict:
    owner = repo.owner(uid)
    cfg = config_store.config(owner)
    account = accounts.get_active(owner)
    checks = {
        "account_bound": False,
        "kite_session_validated": False,
        "live_client_identity": False,
        "broker_inventory_readable": False,
        "broker_holdings_readable": False,
        "broker_orders_readable": False,
        "broker_gtts_readable": False,
        "canonical_journal_reconciled": False,
        "configured_symbols_unencumbered": False,
        "registered_static_egress_verified": False,
        "available_capital_readable": False,
    }
    # Kite requires a registered static public egress IP for API order writes.
    # The read-only portfolio API cannot attest to developer-console IP
    # whitelisting, so never infer this gate from successful read requests.
    blockers = ["BROKER_STATIC_EGRESS_WHITELIST_UNVERIFIED"]
    account_id = None
    if account is None or account.is_paper or accounts.is_sim_account(account):
        blockers.append("ACTIVE_REAL_KITE_ACCOUNT_REQUIRED")
    else:
        account_id = account.id
        checks["account_bound"] = True
        if not account.has_credentials or not account.connected:
            blockers.append("KITE_LOGIN_REQUIRED")
        else:
            try:
                health = await auth.ensure_session(owner, account, force_validate=True)
                if not health.connected or not health.validated or health.transient:
                    blockers.append("FRESH_KITE_SESSION_NOT_VERIFIED")
                elif not health.kite_user_id:
                    blockers.append("KITE_USER_ID_UNVERIFIED")
                else:
                    checks["kite_session_validated"] = True
            except Exception:
                blockers.append("KITE_SESSION_CHECK_UNAVAILABLE")
            if checks["kite_session_validated"]:
                try:
                    # A refresh may have replaced the cached client/token.
                    refreshed = accounts.get_active(owner)
                    if refreshed is None or refreshed.id != account_id:
                        raise PermissionError("ACTIVE_KITE_BINDING_CHANGED")
                    client = await accounts.acquire_client(refreshed)
                    if getattr(client, "_is_paper", None) is not False or str(getattr(client, "_account_id", "")) != account_id:
                        blockers.append("LIVE_CLIENT_IDENTITY_MISMATCH")
                    else:
                        checks["live_client_identity"] = True
                        # Known UNKNOWN/SUBMITTING intents can exist even
                        # when Kite's current-day orderbook is empty. Read
                        # the persisted canonical order/projection authority.
                        try:
                            unknown = order_journal.unresolved(owner, account_id)
                            projecting = order_journal.pending_projection(owner, account_id)
                            if unknown or projecting:
                                blockers.append("CANONICAL_BROKER_ORDER_RECONCILIATION_PENDING")
                            else:
                                checks["canonical_journal_reconciled"] = True
                        except Exception:
                            blockers.append("CANONICAL_BROKER_ORDER_JOURNAL_UNREADABLE")
                        try:
                            positions = await client.get_positions_raw()
                            holdings = await client.get_holdings()
                            if isinstance(positions, dict) and isinstance(positions.get("net"), list):
                                checks["broker_inventory_readable"] = True
                            else:
                                blockers.append("BROKER_INVENTORY_UNREADABLE")
                            if isinstance(holdings, list):
                                checks["broker_holdings_readable"] = True
                            else:
                                blockers.append("BROKER_HOLDINGS_UNREADABLE")
                            # Portfolio inventory, outstanding orders and GTTs
                            # may be manual/another strategy's. Read all three
                            # surfaces before declaring an EMA symbol free.
                            selected = set(cfg.symbols)
                            occupied = False
                            if checks["broker_inventory_readable"] and checks["broker_holdings_readable"]:
                                for row in positions["net"] + holdings:
                                    if not isinstance(row, dict):
                                        occupied = True
                                        break
                                    key = f'{row.get("exchange", "")}:{row.get("tradingsymbol", "")}'
                                    if key not in selected:
                                        continue
                                    try:
                                        # Holdings can include undelivered T1
                                        # quantities; missing quantity is unknown.
                                        amounts = ("quantity", "t1_quantity", "collateral_quantity")
                                        if not any(k in row for k in amounts):
                                            occupied = True
                                        for field in amounts:
                                            if field in row:
                                                qty = Decimal(str(row[field]))
                                                if not qty.is_finite() or qty != 0:
                                                    occupied = True
                                    except (TypeError, ValueError, InvalidOperation):
                                        occupied = True
                            try:
                                orders = await client.get_orders()
                                if isinstance(orders, list) and all(isinstance(row, dict) for row in orders):
                                    checks["broker_orders_readable"] = True
                                    occupied |= any(_order_exposure_unresolved(row, selected) for row in orders)
                                else:
                                    blockers.append("BROKER_ORDER_BOOK_UNREADABLE")
                            except Exception:
                                blockers.append("BROKER_ORDER_BOOK_UNREADABLE")
                            try:
                                gtts = await client.get_gtts()
                                if isinstance(gtts, list) and all(isinstance(row, dict) for row in gtts):
                                    checks["broker_gtts_readable"] = True
                                    occupied |= any(_gtt_exposure_unresolved(row, selected) for row in gtts)
                                else:
                                    blockers.append("BROKER_GTT_BOOK_UNREADABLE")
                            except Exception:
                                blockers.append("BROKER_GTT_BOOK_UNREADABLE")
                            if occupied:
                                blockers.append("BROKER_SYMBOL_OWNERSHIP_RECONCILIATION_REQUIRED")
                            if (bool(selected) and not occupied and checks["broker_inventory_readable"]
                                    and checks["broker_holdings_readable"]
                                    and checks["broker_orders_readable"]
                                    and checks["broker_gtts_readable"]
                                    and checks["canonical_journal_reconciled"]):
                                checks["configured_symbols_unencumbered"] = True
                        except Exception:
                            blockers.append("BROKER_INVENTORY_OR_HOLDINGS_UNREADABLE")
                        try:
                            margins = await client.get_margins()
                            balance = margins.get("equity", {}).get("available", {}).get("live_balance")
                            if _positive_finite(balance):
                                checks["available_capital_readable"] = True
                            else:
                                blockers.append("BROKER_CAPITAL_UNAVAILABLE")
                        except Exception:
                            blockers.append("BROKER_CAPITAL_UNAVAILABLE")
                except Exception:
                    blockers.append("LIVE_KITE_CLIENT_ACQUISITION_FAILED")
    if not cfg.symbols:
        blockers.append("EMA_EQUITY_UNIVERSE_EMPTY")
    # Live activation cannot be deduced from market data, a valid login, or
    # paper results. This reports the missing architecture, not a release toggle.
    modes = {}
    for name in cfg.modes:
        reasons = list(dict.fromkeys(blockers + _mode_blockers(name)))
        modes[name] = {"product": PROFILES[name].product, "eligible": False,
                       "blockers": reasons}
    return {
        "status": "LIVE_BLOCKED",
        "live_orders_enabled": False,
        "account_id": account_id,
        "checks": checks,
        "modes": modes,
        "requires_operator_certification": True,
        "read_only": True,
        "blockers": sorted(set(blockers + ["EMA_LIVE_ORDER_PATH_NOT_IMPLEMENTED"])),
    }
