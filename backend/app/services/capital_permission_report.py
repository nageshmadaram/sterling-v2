"""Gather the permission inputs from their real owners, once.

:mod:`app.core.capital_permission` is deliberately pure: it takes facts and
returns a verdict, so it can be tested without a broker, a database or a repo.
This module is the other half — the part that goes and asks each owner. It is
separate because "what does the rule say?" and "what is true right now?" fail
in different ways, and a failure to read a fact must show up as UNKNOWN rather
than as a rule that quietly answered no for the wrong reason.

Anything that cannot be determined is passed through as ``None``, which the
gate treats as a refusal and reports as unknown. Guessing ``False`` would be
the same verdict today and a lie in the report.
"""
from __future__ import annotations

from typing import Any
from dataclasses import replace

from app.services import lane_gate_inputs
from app.core.capital_permission import (
    CapitalPermission,
    EntryPermissionInputs,
    may_send_entry,
)

__all__ = [
    "lane_permission",
    "originator_permission",
    "all_lane_permissions",
    "render_permissions",
]

#: SafetySupervisor blocker codes that are the risk hierarchy's answer (daily
#: loss circuit breaker, operator kill switch, an evaluation that failed closed).
_RISK_BLOCKER_CODES = frozenset({"daily_loss_halt", "kill_switch", "safety_unknown"})


def _is_exposure_blocker(code: str) -> bool:
    """Blocker codes that are the exposure coordinator's answer."""
    return code in ("recovery_required", "control_plane_unreadable") or code.startswith(
        "external_exposure"
    )


def _safety_reading(strategy_id: str) -> tuple[bool | None, bool | None, bool | None]:
    """``(may_increase, risk_admission, exposure_admission)`` from ONE supervisor read.

    The risk hierarchy and the exposure coordinator are not separate machines at
    the lane level: the SafetySupervisor is the single admission authority and
    names which of its blockers came from which. Reading it once keeps the three
    answers consistent with each other. An unreadable supervisor is unknown for
    all three, and unknown refuses.
    """
    try:
        from app.services.safety_supervisor import snapshot

        snap = snapshot(strategy_id=strategy_id)
        codes = tuple(snap.blockers)
        return (
            bool(snap.may_increase_exposure),
            not any(code in _RISK_BLOCKER_CODES for code in codes),
            not any(_is_exposure_blocker(code) for code in codes),
        )
    except Exception:  # noqa: BLE001 - an unreadable authority is unknown, not "yes"
        return None, None, None


def _vehicle_live_capable(lane_key: str) -> bool | None:
    from app.core.execution_vehicle import (
        VehicleError,
        is_authoritative_live_vehicle,
        lane_vehicle,
    )

    try:
        return is_authoritative_live_vehicle(lane_vehicle(lane_key))
    except VehicleError:
        return None


def _release_certified() -> bool | None:
    try:
        from app.services.release_certification import certification_report

        return bool(certification_report().release_ready)
    except Exception:  # noqa: BLE001
        return None


def _binding_live_ready() -> bool | None:
    try:
        from app.services.account_binding_service import binding_health

        health = binding_health()
        if not health.get("account_binding_readable"):
            return None
        return bool(health.get("account_binding_live_ready"))
    except Exception:  # noqa: BLE001
        return None


def lane_permission(
    lane_key: str,
    *,
    release_certified: bool | None = None,
    binding_ready: bool | None = None,
) -> CapitalPermission:
    """The live-entry verdict for one lane, read from live state."""
    from app.core.lane_registry import LANES, LIVE_EXECUTION_ENABLED

    lane = LANES.get(lane_key)
    strategy = lane.strategy_id if lane else ""
    may_increase, risk_ok, exposure_ok = _safety_reading(strategy)

    decision = may_send_entry(
        EntryPermissionInputs(
            lane_key=lane_key,
            global_live_switch=LIVE_EXECUTION_ENABLED,
            account_binding_ready=(
                _binding_live_ready() if binding_ready is None else binding_ready
            ),
            safety_may_increase_exposure=may_increase,
            lane_state=lane.state.value if lane else "",
            risk_gate_passed=risk_ok,
            exposure_gate_passed=exposure_ok,
            vehicle_live_capable=_vehicle_live_capable(lane_key),
            # Waived for this release: measured so the operator can see what is
            # and is not proven, never enforced. Each still answers None when
            # the question genuinely cannot be answered.
            release_certified=(
                _release_certified() if release_certified is None else release_certified
            ),
            lane_promotion_passed=lane_gate_inputs.lane_promotion_passed(lane_key),
            lane_identity_matches_frozen=lane_gate_inputs.lane_identity_matches_frozen(lane_key),
            shadow_gate_passed=lane_gate_inputs.shadow_gate_passed(lane_key),
        )
    )
    if lane is not None and 'LANE_NOT_LIVE_STATE' in decision.blockers:
        return replace(decision, lane_state_detail=f'Mode is not implemented for live execution: {lane.note}')
    return decision


def originator_permission(
    originator_id: str,
    *,
    release_certified: bool | None = None,
    binding_ready: bool | None = None,
) -> CapitalPermission:
    """The live-entry verdict for a release originator with no five-mode lane.

    Gamma-Unwind has its own runtime contract (entry, stop and sizing rules) and
    is not a lane of the Snapback/SuperTrend registry, so it is derived from the
    release-scope allowlist rather than from an invented horizon mode.
    """
    from app.core.lane_registry import LIVE_EXECUTION_ENABLED
    from app.core.release_scope import may_originate

    in_scope = may_originate(originator_id)
    may_increase, risk_ok, exposure_ok = _safety_reading(originator_id)
    return may_send_entry(
        EntryPermissionInputs(
            lane_key=originator_id,
            global_live_switch=LIVE_EXECUTION_ENABLED,
            account_binding_ready=(
                _binding_live_ready() if binding_ready is None else binding_ready
            ),
            safety_may_increase_exposure=may_increase,
            lane_state="LIVE_MINIMUM" if in_scope else "",
            risk_gate_passed=risk_ok,
            exposure_gate_passed=exposure_ok,
            vehicle_live_capable=in_scope,
            release_certified=(
                _release_certified() if release_certified is None else release_certified
            ),
            # No frozen lane identity, forward sample or rehearsal record exists
            # for an originator outside the lane registry: reported as unproven.
            lane_promotion_passed=None,
            lane_identity_matches_frozen=None,
            shadow_gate_passed=None,
        )
    )


def _lane_less_originators() -> list[str]:
    """Release originators that are not backed by a lane of the registry."""
    from app.core.lane_registry import originator_rows

    return [str(row["originator_id"]) for row in originator_rows() if not row["lane_backed"]]


def all_lane_permissions() -> list[dict[str, Any]]:
    from app.core.lane_registry import LANES

    certified = _release_certified()
    binding = _binding_live_ready()
    rows = [
        lane_permission(key, release_certified=certified, binding_ready=binding).as_dict()
        for key in LANES
    ]
    rows.extend(
        originator_permission(
            originator, release_certified=certified, binding_ready=binding
        ).as_dict()
        for originator in _lane_less_originators()
    )
    return rows


def render_permissions() -> str:
    from app.core.lane_registry import LANES

    certified = _release_certified()
    binding = _binding_live_ready()
    blocks = [
        lane_permission(key, release_certified=certified, binding_ready=binding).render()
        for key in LANES
    ]
    blocks.extend(
        originator_permission(
            originator, release_certified=certified, binding_ready=binding
        ).render()
        for originator in _lane_less_originators()
    )
    blocks.append(
        "A lane sends live entries when no BLOCKING line applies to it.\n"
        "Waived gates (release certification, forward-sample verdict, identity\n"
        "drift, rehearsal) are advisory: reported above, never blocking."
    )
    return "\n\n".join(blocks)
