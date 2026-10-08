"""Fresh, read-only broker proof for durable account bindings.

No function here can place, modify or cancel an order. Verification requires a
fresh broker profile plus readable positions, orders and GTT/protection
inventory from the exact active Sterling Kite account.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

from app.core.broker_account_binding import BindingError
from app.core.state_paths import state_root
from app.services.account_binding_service import (
    AccountBindingStore,
    BindingVerificationEvidence,
    VERIFICATION_DIR,
)


def _stamp_for_path(value: str) -> str:
    return value.replace(":", "").replace("-", "").replace("+", "_").replace(".", "_")


async def verify_current_kite_binding(
    binding_id: str,
    *,
    store: AccountBindingStore | None = None,
    user_id: str | None = None,
    application_account_id: str | None = None,
    deployment_id: str | None = None,
):
    from app.services.exchanges.kite import accounts

    binding_store = store or AccountBindingStore()
    binding = binding_store.get(binding_id)
    if binding is None:
        raise BindingError(f"no such binding: {binding_id}")
    if binding.broker.strip().lower() != "zerodha":
        raise BindingError(
            f"automatic broker verification is not implemented for {binding.broker!r}"
        )
    if "live" not in binding.account_scope.strip().lower():
        raise BindingError(
            "this release verifies live broker bindings; account_scope must contain 'live'"
        )

    resolved_user = str(
        user_id if user_id is not None else os.environ.get("STERLING_FAMILY_USER_ID", "")
    ).strip()
    resolved_account = str(
        application_account_id
        if application_account_id is not None
        else os.environ.get("STERLING_FAMILY_ACCOUNT_ID", "")
    ).strip()
    resolved_deployment = str(
        deployment_id
        if deployment_id is not None
        else os.environ.get("STERLING_DEPLOYMENT_ID", "")
    ).strip()
    if not resolved_user:
        raise BindingError("STERLING_FAMILY_USER_ID is required for broker verification")
    if not resolved_account:
        raise BindingError("STERLING_FAMILY_ACCOUNT_ID is required for broker verification")
    if not resolved_deployment:
        raise BindingError("STERLING_DEPLOYMENT_ID is required for broker verification")
    if resolved_deployment != binding.deployment_id:
        raise BindingError(
            f"running deployment {resolved_deployment!r} does not match "
            f"binding deployment {binding.deployment_id!r}"
        )

    accounts.bootstrap()
    active = accounts.get_active(resolved_user)
    if active is None:
        raise BindingError("there is no unambiguous active Kite application account")
    if str(active.id) != resolved_account:
        raise BindingError(
            f"active Kite application account {active.id!r} does not match "
            f"configured {resolved_account!r}"
        )
    if bool(active.is_paper):
        raise BindingError("binding verification requires a real, live Kite session")
    if not active.connected:
        raise BindingError("the configured Kite account has no authenticated session")
    if active.kite_user_id and str(active.kite_user_id).strip() != binding.client_id:
        raise BindingError(
            f"stored Kite user {active.kite_user_id!r} does not match "
            f"binding client {binding.client_id!r}"
        )

    client = await accounts.acquire_client(active)
    if str(getattr(client, "_account_id", "") or "").strip() != resolved_account:
        raise BindingError("acquired broker client belongs to a different application account")
    if getattr(client, "_is_paper", None) is not False:
        raise BindingError("broker client mode is not verified real/read-only capable")

    try:
        profile = await client.get_profile()
        profile_client = str((profile or {}).get("user_id") or "").strip()
        if not profile_client:
            raise BindingError("fresh broker profile did not contain user_id")
        if profile_client != binding.client_id:
            raise BindingError(
                f"fresh broker profile client {profile_client!r} does not match "
                f"binding client {binding.client_id!r}"
            )
        positions_raw = await client.get_positions_raw()
        positions = list((positions_raw or {}).get("net") or [])
        orders = list(await client.get_orders() or [])
        gtts = list(await client.get_gtts() or [])
    except BindingError:
        raise
    except Exception as exc:
        raise BindingError(
            f"fresh broker identity/inventory read failed: {type(exc).__name__}: {exc}"
        ) from exc

    observed_at = datetime.now(timezone.utc).isoformat()
    evidence_dir = state_root() / VERIFICATION_DIR / binding.binding_id
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = evidence_dir / f"{_stamp_for_path(observed_at)}.json"
    payload = {
        "schema_version": 1,
        "binding_id": binding.binding_id,
        "broker": binding.broker,
        "profile_client_id": profile_client,
        "application_account_id": resolved_account,
        "deployment_id": resolved_deployment,
        "account_scope": binding.account_scope,
        "observed_at": observed_at,
        "positions_read": True,
        "orders_read": True,
        "protective_orders_read": True,
        "position_count": len(positions),
        "order_count": len(orders),
        "protective_order_count": len(gtts),
    }
    evidence_bytes = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    evidence_path.write_bytes(evidence_bytes)
    evidence_sha256 = hashlib.sha256(evidence_bytes).hexdigest()
    evidence = BindingVerificationEvidence(
        binding_id=binding.binding_id,
        profile_client_id=profile_client,
        application_account_id=resolved_account,
        deployment_id=resolved_deployment,
        observed_at=observed_at,
        positions_read=True,
        orders_read=True,
        protective_orders_read=True,
        position_count=len(positions),
        order_count=len(orders),
        protective_order_count=len(gtts),
        evidence_ref=str(evidence_path),
        evidence_sha256=evidence_sha256,
    )
    return binding_store.verify(binding.binding_id, evidence)
