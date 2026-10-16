"""Read-only preflight for promoting EMA Trend to broker execution.

This module performs no order or GTT writes. A healthy Kite login is necessary,
not sufficient, for permission to take real exposure. Never derive live approval
from the existence of the paper simulation engine.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from app.engines.ema_trend.config import PROFILES
from app.services.exchanges.kite import accounts, auth
from . import config_store, repository as repo


def _positive_finite(value) -> bool:
    try:
        amount = Decimal(str(value))
    except (TypeError, InvalidOperation, ValueError):
        return False
    return amount.is_finite() and amount > 0


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
        "configured_symbols_unencumbered": False,
        "available_capital_readable": False,
    }
    blockers = []
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
                            # Portfolio inventory may be external/manual or belong
                            # to another strategy. Never claim it as EMA-owned.
                            if checks["broker_inventory_readable"] and checks["broker_holdings_readable"]:
                                selected = set(cfg.symbols)
                                occupied = False
                                for row in positions["net"] + holdings:
                                    if not isinstance(row, dict):
                                        occupied = True
                                        break
                                    key = f'{row.get("exchange", "")}:{row.get("tradingsymbol", "")}'
                                    if key in selected:
                                        try:
                                            qty = Decimal(str(row.get("quantity", 0)))
                                            if not qty.is_finite() or qty != 0:
                                                occupied = True
                                        except (TypeError, ValueError, InvalidOperation):
                                            occupied = True
                                if occupied:
                                    blockers.append("BROKER_SYMBOL_OWNERSHIP_RECONCILIATION_REQUIRED")
                                else:
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
