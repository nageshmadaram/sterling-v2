"""Read existing position stores before admission; never call the broker here.

An operator halt can be lifted, or its write can fail. Mechanical protection
integrity must still refuse new risk on the same account. Management remains
available through the supervisor's non-increasing operation classification.
"""
from __future__ import annotations

from math import isfinite
from typing import Any


def position_fault(strategy: str, position: Any) -> str:
    if bool(getattr(position, "protection_pending", False)) or bool(
        getattr(position, "gtt_pending", False)
    ):
        return "protection outcome pending"
    status = str(getattr(position, "status", "") or "").upper()
    if strategy == "snapback":
        # Includes partially exited/reconciling positions. EXITING means the
        # stop was intentionally released; unresolved intent gates cover it.
        if status == "EXITING":
            return "" if getattr(position, "option_exit_order_id", "") else "exit without confirmed protection or order"
        return ""  # LivePosition.protection_pending validates remaining units.
    if status != "OPEN":
        return ""
    stop_mode = str(getattr(position, "stop_mode", "") or "").lower()
    if stop_mode not in {"broker", "both"}:
        # An old Gamma position can survive a deployment/config transition.
        # Its monitor-only stop is NOT broker protection. Gamma's own recovery
        # halts this case, but admission for other strategies must independently
        # reject fresh risk on the same account if the runner has not started.
        if strategy == "gamma_move":
            return "live Gamma position lacks broker-held stop protection"
        return ""
    stop = float(getattr(position, "stop_premium" if strategy == "sterling-kite" else "stop", 0) or 0)
    trigger = int(getattr(position, "gtt_id", 0) or 0)
    if not isfinite(stop) or stop <= 0 or trigger <= 0:
        return "required broker stop missing"
    quantity = int(getattr(position, "qty" if strategy == "sterling-kite" else "quantity", 0) or 0)
    protected = int(getattr(position, "gtt_qty", 0) or 0)
    if hasattr(position, "gtt_qty") and quantity > 0 and protected != quantity:
        return "broker stop quantity differs from owned remainder"
    return ""


def admission_blocker(uid: str, account_id: str) -> tuple[str, str]:
    """Fail closed on unreadable stores, scoped to the requesting live account."""
    try:
        from app.services import gamma_move_positions, snapback_live_positions, price_rules_store
        from app.services.kite_engine import positions

        reason = positions.unreadable_reason(uid)
        if reason:
            raise RuntimeError(reason)
        from app.services.kite_engine import state
        # Legacy Gamma positions were archived without a trusted account id.
        # Until a broker operator reconciles them, they may belong to ANY
        # account of this user. Never turn unreadable/unknown exposure into a
        # permission to originate fresh risk in another strategy.
        legacy_gamma = gamma_move_positions.legacy_open_rows(uid)
        if legacy_gamma:
            return (
                "protection_integrity",
                "Unreconciled legacy Gamma broker exposure; resolve archived positions",
            )
        for position in gamma_move_positions.load(uid).values():
            if str(position.account_id or "").strip() == str(account_id).strip() and (
                position.pnl_reconciliation_required
                and (position.is_open or str(position.pnl_flagged_day or "") == state._ist_today_iso())
            ):
                return "accounting_integrity", "Gamma-Unwind realized P&L requires reconciliation"
        for strategy, registry in (
            ("sterling-kite", positions),
            ("gamma_move", gamma_move_positions),
            ("snapback", snapback_live_positions),
            ("price_rules", price_rules_store),
        ):
            for position in registry.open_positions(uid):
                owner_account = str(getattr(position, "account_id", "") or "").strip()
                if owner_account and owner_account != str(account_id).strip():
                    continue
                if not owner_account:
                    # Paper/reference positions are outside live protection.
                    continue
                fault = position_fault(strategy, position)
                if fault:
                    return "protection_integrity", f"{strategy}: {fault}"
        return "", ""
    except Exception as exc:
        return "protection_unreadable", f"Required protection cannot be established: {type(exc).__name__}"
