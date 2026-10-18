"""Read existing position stores before admission; never call the broker here.

An operator halt can be lifted, or its write can fail. Mechanical protection
integrity must still refuse new risk on the same account. Management remains
available through the supervisor's non-increasing operation classification.
"""
from __future__ import annotations

import json
from math import isfinite, floor
from typing import Any


def position_fault(strategy: str, position: Any) -> str:
    if bool(getattr(position, "protection_pending", False)) or bool(
        getattr(position, "gtt_pending", False)
    ):
        return "protection outcome pending"
    status = str(getattr(position, "status", "") or "").upper()
    if strategy == "snapback":
        # ENTERING has a confirmed option leg but may still be establishing its
        # hedge/protection; EXITING can already have released the broker stop;
        # RECONCILING means persisted exposure and broker inventory disagree.
        # Having an exit order ID is not proof of a settled exit fill. All three
        # require operator/recovery completion before ANY new account risk.
        if status in {"ENTERING", "EXITING", "RECONCILING"}:
            return f"Snapback {status.lower()} position has unresolved broker exposure"
        return ""  # OPEN is checked via LivePosition.protection_pending.
    if strategy in {"gamma_move", "sterling-kite"} and status == "PENDING":
        # A pending broker BUY can fill after admission. No confirmed filled
        # quantity and protective trigger exist yet: do not originate another
        # entry against this unresolved risk, including across strategies.
        return "broker entry pending fill and stop protection"
    if status != "OPEN":
        return ""
    stop_mode = str(getattr(position, "stop_mode", "") or "").lower()
    if stop_mode not in {"broker", "both"}:
        # For the three release-scoped strategies, monitor-only and unknown
        # modes do not establish broker-held protection. Reconciliation and
        # reduction must still be available; this only blocks NEW entries.
        if strategy in {"gamma_move", "sterling-kite"}:
            return f"live {strategy} position lacks broker-held stop protection"
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


def _proven_zero_fill_retry(store, plan, request, quantity):
    """Only a revision-owned retry whose previous sends ended with zero fill."""
    from app.services.kite_engine import order_journal
    from app.services.snapback_live_executor import HEDGE_ZERO_FILL_RETRIES
    events=list(store.events(plan["plan_id"]) or [])
    attempts=[e for e in events if e.get("event")=="RECOVERY_ATTEMPT"]
    base=str(plan.get("opportunity_id") or "")
    context=next((e for e in reversed(events) if e.get("event")=="RECONCILING"),None)
    if not context:
        return False
    detail=json.loads(context.get("detail_json") or "{}")
    reason=str(detail.get("reason") or "")
    if not attempts:
        # The executor records this refusal before _submit. A strict missing
        # canonical reservation corroborates that no broker send was possible.
        if request.signal_id!=base or not reason.startswith("hedge_risk_denied:"):
            return False
        key=order_journal.make_key(uid=request.uid,account_id=request.account_id,
            strategy_id="snapback",generation_id=request.generation_id,
            signal_id=base,exchange=request.exchange,symbol=request.symbol,side=request.side)
        return order_journal.get(key) is None
    if len(attempts)>HEDGE_ZERO_FILL_RETRIES:
        return False
    revision=int(attempts[-1].get("revision") or 0)
    if (revision!=int(plan.get("revision") or 0) or revision<=0
            or request.signal_id!=f"{base}:hedge-recovery:{revision}"):
        return False
    if not reason.startswith("hedge_rejected_zero_fill:"):
        return False
    # Allocating an attempt is not proof of rejection. Re-read each earlier
    # canonical broker outcome, including at the final submission boundary.
    signals=[base]+[f"{base}:hedge-recovery:{int(e['revision'])}" for e in attempts[:-1]]
    for signal_id in signals:
        key=order_journal.make_key(uid=request.uid,account_id=request.account_id,
            strategy_id="snapback",generation_id=request.generation_id,
            signal_id=signal_id,exchange=request.exchange,symbol=request.symbol,side=request.side)
        previous=order_journal.get(key)
        if (previous is None or previous.state.upper() not in {"CANCELLED","REJECTED"}
                or previous.filled_quantity!=0 or previous.reconciliation_required
                or previous.quantity!=quantity or not previous.order_id):
            return False
    return True


def _protected_snapback_hedge(position: Any, request: Any) -> bool:
    """Recognize only the missing hedge of this durable, protected entry.

    This does not classify futures as risk-free or waive any other gate. It
    prevents an entry's own ENTERING row from deadlocking its second leg.
    Re-read the plan on every check, including the final broker boundary.
    """
    if request is None or str(getattr(position, "status", "")) != "ENTERING":
        return False
    from app.services.snapback_plan_store import shared_store
    from app.services.snapback_live_executor import _hedge_side

    if getattr(request, "strategy_id", "") != "snapback":
        return False
    plan_id = str(getattr(position, "position_id", ""))
    if not plan_id or getattr(request, "tag", "") != f"snapback:{plan_id}":
        return False
    store = shared_store()
    plan = store.get(plan_id)
    if (not plan or plan.get("plan_id") != plan_id or plan.get("status") not in {"ARMED", "RECONCILING"}
            or not plan.get("opportunity_id") or not plan.get("policy_snapshot_hash")):
        return False
    if (position.protection_pending or position.gtt_pending
            or position.hedge_qty or position.hedge_entry_order_id
            or position.option_exit_qty or position.option_exit_order_id
            or not position.option_entry_order_id):
        return False
    if not isfinite(float(position.gtt_stop)):
        return False
    if not (
        request.uid == position.uid == plan.get("owner_uid")
        and request.account_id == position.account_id == plan.get("account_id")
        and request.symbol == position.hedge_symbol == plan.get("futures_symbol")
        and request.exchange == position.hedge_exchange == plan.get("futures_exchange")
        and request.side == position.hedge_side == _hedge_side(plan)
        and position.signal_id == plan.get("opportunity_id")
        and request.generation_id == plan.get("policy_snapshot_hash")
        and position.option_symbol == plan.get("option_symbol")
    ):
        return False
    filled = int(position.option_qty)
    requested = int(plan.get("option_quantity") or 0)
    target = int(plan.get("target_futures_quantity") or 0)
    lot = int(plan.get("futures_lot_size") or 0)
    if not (0 < filled <= requested and target > 0 and lot > 0):
        return False
    expected = int(floor(target * filled / requested / lot + 0.5)) * lot
    if expected<=0 or request.quantity!=expected:
        return False
    if plan.get("status")=="ARMED":
        return not position.flags and request.signal_id==position.signal_id
    return (set(position.flags)=={"hedge_incomplete"}
            and _proven_zero_fill_retry(store,plan,request,expected))


def admission_blocker(uid: str, account_id: str, *, entry_request: Any = None) -> tuple[str, str]:
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
        # Loading the active registry may migrate old open positions into the
        # archive. Check that archive AFTER load, not before, or an un-migrated
        # open position disappears from admission checks on first access.
        gamma_positions = gamma_move_positions.load(uid)
        legacy_gamma = gamma_move_positions.legacy_open_rows(uid)
        if legacy_gamma:
            return (
                "protection_integrity",
                "Unreconciled legacy Gamma broker exposure; resolve archived positions",
            )
        for position in gamma_positions.values():
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
                    # Unbound historical rows are not automatically paper:
                    # an order or GTT identity can represent real broker risk
                    # whose account ownership was lost during migration.
                    # They may belong to ANY account for this user. Require an
                    # operator to reconcile rather than silently skipping it.
                    possible_broker_exposure = any(
                        str(getattr(position, name, "") or "").strip()
                        for name in ("order_id", "exit_order_id", "gtt_id")
                    ) or any(
                        bool(getattr(position, name, False))
                        for name in ("protection_pending", "gtt_pending")
                    )
                    if possible_broker_exposure:
                        return (
                            "protection_integrity",
                            f"{strategy}: broker exposure has no verified owning account",
                        )
                    # Reference rows without even a broker submission identity
                    # have no evidence of real exposure and remain excluded.
                    continue
                fault = position_fault(strategy, position)
                if (fault and strategy == "snapback"
                        and not position.protection_pending
                        and _protected_snapback_hedge(position, entry_request)):
                    fault = ""
                if fault:
                    return "protection_integrity", f"{strategy}: {fault}"
        # A corrupt or missing Gamma realised-P&L ledger must not look like a
        # clean daily-loss budget to Snapback or SuperTrend. load_record()
        # reads strictly on first access and caches verified data afterwards.
        # This is an entry-only check; exits and protective repairs still pass
        # through the supervisor's non-increasing operation path.
        gamma_move_positions.load_record(uid)
        return "", ""
    except Exception as exc:
        return "protection_unreadable", f"Required protection cannot be established: {type(exc).__name__}"
