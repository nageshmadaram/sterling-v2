"""Release and economic permission for real exposure increases only.

Observation, paper execution and verified reductions do not need permission to
originate real risk. Every live originator does, irrespective of environment or
UI toggles. This supplements, and never replaces, the current safety authority.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import timedelta
import hashlib
import json
import math
import os
from typing import Any

from app.core.acceptance_evidence import AcceptanceRegister, canonical_bytes, utc_timestamp

OPERATIONAL_GATES = ("G0", "G1", "G2", "G3", "G4", "G5", "G6", "G8", "G9")
ECONOMIC_STRATEGIES = frozenset({"sterling-kite", "snapback", "gamma_move"})


def entry_scope(request: Any) -> dict[str, str]:
    """Resolve configuration on the server; payload claims cannot grant authority."""
    from app.core.release_manifest import runtime_sha

    strategy = str(request.strategy_id).strip().lower()
    if strategy == "sterling-kite":
        from app.services.kite_engine.state import get_effective_config
        config = get_effective_config(request.uid)
        mode = config.horizon_mode
    elif strategy == "snapback":
        from app.services.snapback import get_config
        config = get_config(request.uid)
        mode = config.trading_mode
    elif strategy == "gamma_move":
        from app.services.gamma_move import get_config
        config = get_config(request.uid)
        # Gamma has its own hold contract. Do not invent one of the five modes.
        mode = "gamma_native"
    elif strategy == "price_rules":
        # Every plan is separately server-armed by Price Rules. The release
        # acceptance covers its execution contract, not an alpha hypothesis.
        config = {"execution_contract": "individually_armed_manual_plan_v1"}
        mode = "manual_plan"
    else:
        raise ValueError("strategy is outside the production release originators")
    values = asdict(config) if is_dataclass(config) else config.model_dump(mode="json") if hasattr(config, "model_dump") else config
    from app.core.live_minimum import configured_envelope
    from app.services.shared_risk import get_config as shared_risk_config
    values = {"strategy": values, "day_one_envelope": configured_envelope().as_dict(),
              "shared_risk": shared_risk_config(request.uid).model_dump(mode="json"),
              "risk_hierarchy": os.environ.get("STERLING_RISK_LIMITS", "")}
    if strategy in {"sterling-kite", "snapback"}:
        from app.core.horizon import canonical_mode
        mode = canonical_mode(mode).value
    sha = runtime_sha()
    return {
        "source_sha": sha, "runtime_release_sha": sha,
        "deployment_id": os.environ.get("STERLING_DEPLOYMENT_ID", "").strip(),
        "account_id": str(request.account_id), "strategy_id": strategy,
        "mode": str(mode), "strategy_version": sha,
        "config_hash": hashlib.sha256(canonical_bytes(values)).hexdigest(),
    }


def _controlled_order_authorized(store: AcceptanceRegister, claims: list, scope: dict[str, str], request: Any, capital_required: float | None) -> bool:
    """One explicitly approved certification intent, never general automation.

    The canonical journal is its single-use reservation. All order-key inputs
    and quantity are bound in the reviewed owner receipt. Retrying the same grant
    therefore resolves to the same journal intent; changing any key refuses.
    No approval is created by this function or accepted from request payloads.
    """
    if request is None:
        return False
    from app.core.live_minimum import configured_envelope
    if configured_envelope().max_lots != 1:
        return False  # certification cannot silently inherit a scaled limit
    matching = [c for c in claims if c.claim_id == "CONTROLLED_LIVE_AUTHORIZATION"
                and all(getattr(c, key) == scope[key] for key in scope)]
    if not matching or store.status(matching[-1]) != "VERIFIED":
        return False
    claim = matching[-1]
    if not claim.expires_at or utc_timestamp(claim.expires_at) - utc_timestamp(claim.observed_at) > timedelta(minutes=15):
        return False
    receipt = json.loads(store.artifact(claim))
    if receipt.get("purpose") != "ONE_CONTROLLED_CERTIFICATION_ORDER" or receipt.get("owner_authorized") is not True:
        return False
    if receipt.get("schema_version") != 1:
        return False
    expected = {"uid": request.uid, "account_id": request.account_id,
                "strategy_id": request.strategy_id, "generation_id": request.generation_id,
                "signal_id": request.signal_id, "exchange": request.exchange,
                "symbol": request.symbol, "side": request.side, "quantity": request.quantity,
                "order_type": request.order_type, "price": request.price, "trigger_price": request.trigger_price,
                "product": (request.payload or {}).get("product", "NRML")}
    if any(not expected[key] for key in ("uid", "account_id", "strategy_id", "generation_id", "signal_id", "exchange", "symbol", "side")) or any(receipt.get(key) != value for key, value in expected.items()):
        return False
    if type(receipt.get("quantity")) is not int or receipt["quantity"] <= 0:
        return False
    # The first certification order has a hard price/notional bound. A market
    # quote is not such a bound, even when a caller labels it a capital budget.
    if str(request.order_type).upper() not in {"LIMIT", "LIMIT_ORDER"} or request.price <= 0:
        return False
    notional_ceiling = receipt.get("max_order_notional")
    if (type(notional_ceiling) not in (int, float) or not math.isfinite(notional_ceiling)
            or notional_ceiling <= 0 or request.price * request.quantity > notional_ceiling):
        return False
    ceiling = receipt.get("max_capital_required")
    if (type(ceiling) not in (int, float) or not math.isfinite(ceiling) or ceiling <= 0
            or type(capital_required) not in (int, float) or not math.isfinite(capital_required)
            or not 0 < capital_required <= ceiling):
        return False
    return True


def release_permission(scope: dict[str, str], *, register: AcceptanceRegister | None = None, request: Any = None, capital_required: float | None = None) -> dict[str, Any]:
    """R1, R2 and R3 stay separate; UNKNOWN and BLOCKED never grant permission."""
    try:
        if not scope.get("deployment_id") or not scope.get("account_id"):
            raise ValueError("deployment and account identity are required")
        store = register or AcceptanceRegister()
        claims = store.read()
        gates = {}
        for gate in (*OPERATIONAL_GATES, "G7"):
            matching = []
            for claim in claims:
                if claim.claim_id != gate or any(getattr(claim, key) != scope[key] for key in ("source_sha", "runtime_release_sha")):
                    continue
                # Source/CI observations are portable between hosts on the same
                # immutable SHA. All other observations belong to this account
                # and deployment and cannot be copied to a family member.
                if gate not in {"G0", "G1"} and any(getattr(claim, key) != scope[key] for key in ("deployment_id", "account_id")):
                    continue
                if gate in {"G4", "G7", "G9"} and any(getattr(claim, key) != scope[key] for key in ("strategy_id", "mode", "strategy_version", "config_hash")):
                    continue
                matching.append(claim)
            # Appending a failure/revocation supersedes an earlier approval.
            gates[gate] = store.status(matching[-1]) if matching else "UNKNOWN"
        r1 = all(gates[g] == "VERIFIED" for g in ("G0", "G1", "G2", "G3", "G5", "G6", "G8"))
        r2 = all(gates[g] == "VERIFIED" for g in OPERATIONAL_GATES)
        economic_required = scope["strategy_id"] in ECONOMIC_STRATEGIES
        r3 = r2 and economic_required and gates["G7"] == "VERIFIED"
        blocked = [g for g in OPERATIONAL_GATES if gates[g] != "VERIFIED"]
        if economic_required and gates["G7"] != "VERIFIED":
            blocked.append("G7")
        controlled = (set(blocked) <= {"G7", "G9"} and bool(blocked)
                      and _controlled_order_authorized(store, claims, scope, request, capital_required))
        return {"r1_verified": r1, "r2_verified": r2, "r3_verified": r3,
                "manual_plan": not economic_required, "new_entries_allowed": not blocked or controlled,
                "controlled_test_authorized": controlled,
                "gates": gates, "blockers": blocked}
    except Exception:
        # A damaged evidence store must not turn into the default permission.
        return {"r1_verified": False, "r2_verified": False, "r3_verified": False,
                "new_entries_allowed": False, "gates": {},
                "blockers": ["ACCEPTANCE_EVIDENCE_UNAVAILABLE"]}


def entry_refusal(request: Any, client: Any, capital_required: float | None = None) -> str | None:
    if getattr(client, "_is_paper", None) is True:
        return None
    try:
        # Completing a previously reserved, protected Snapback hedge reduces
        # existing option risk. Only the durable server-owned plan can prove
        # that exception; a caller-supplied "hedge" label cannot grant it.
        if str(getattr(request, "strategy_id", "")).lower() == "snapback":
            from app.services.snapback_live_positions import open_positions
            from app.services.protection_health import _protected_snapback_hedge
            if any(_protected_snapback_hedge(position, request) for position in open_positions(request.uid)):
                return None
        scope = entry_scope(request)
        permission = release_permission(scope, request=request, capital_required=capital_required)
    except Exception:
        return "RELEASE_AUTHORITY_UNKNOWN: server strategy identity could not be verified"
    if not permission["new_entries_allowed"]:
        return "RELEASE_NOT_AUTHORIZED: " + ", ".join(permission["blockers"])
    try:
        from app.core.release_manifest import working_tree_clean
        if not working_tree_clean():
            return "RELEASE_SOURCE_DIRTY: tested source differs from this checkout"
        from app.core.ci_certification import ci_report
        if not ci_report(scope["runtime_release_sha"]).all_passed:
            return "RELEASE_CI_UNVERIFIED: required current-SHA jobs lack execution proof"
    except Exception:
        return "RELEASE_AUTHORITY_UNKNOWN: source or CI evidence is unreadable"
    return None


def operator_release_status(uid: str, account_id: str) -> dict[str, Any]:
    """Account-scoped status only; this never constructs a broker write client."""
    from types import SimpleNamespace
    from app.services.exchanges.kite import accounts
    account = accounts.get(uid, account_id) if account_id else None
    if account is None and account_id:
        matches = [value for value in accounts.list_accounts(uid)
                   if str(getattr(value, "kite_user_id", "") or "") == account_id]
        account = matches[0] if len(matches) == 1 else None
    if account is None or account.is_paper or not account.is_active:
        return {"new_entries_allowed": False, "status": "UNKNOWN",
                "blockers": ["ACTIVE_OWNED_LIVE_ACCOUNT_REQUIRED"], "strategies": []}
    rows = []
    for strategy in ("sterling-kite", "snapback", "gamma_move", "price_rules"):
        request = SimpleNamespace(uid=uid, account_id=account.id, strategy_id=strategy,
                                  generation_id="", signal_id="")
        try:
            scope = entry_scope(request)
            permission = release_permission(scope)
            if permission["new_entries_allowed"]:
                refusal = entry_refusal(request, SimpleNamespace(_is_paper=False))
                if refusal:
                    permission["new_entries_allowed"] = False
                    permission.update(r1_verified=False, r2_verified=False, r3_verified=False)
                    permission["blockers"].append(refusal.split(":", 1)[0])
            rows.append({"strategy_id": strategy, "mode": scope["mode"], **permission})
        except Exception:
            rows.append({"strategy_id": strategy, "new_entries_allowed": False,
                         "blockers": ["SERVER_STRATEGY_IDENTITY_UNKNOWN"]})
    allowed = any(row["new_entries_allowed"] for row in rows)
    return {"new_entries_allowed": allowed, "status": "AUTHORIZED" if allowed else "OBSERVATION_ONLY",
            "blockers": sorted({code for row in rows for code in row["blockers"]}), "strategies": rows}
