"""The ten strategy-mode lanes, their operational state, and the origination gate.

A lane carries its own rules and its own state, and none of that may leak
sideways. This module holds the declared state of each lane and the single
function that decides whether one may open new exposure.

Three independent conditions must all hold before a lane originates:

1. the strategy is in the focused set (:mod:`app.core.focus`);
2. the lane's rules are actually defined -- a lane whose entry, exit and risk
   rules have not been written down has nothing to trade, so it must not
   originate even while its state reads RESEARCH;
3. the lane's state permits origination, and a live state additionally requires
   the master live switch to be on.

Each is checked separately and reports its own reason. Collapsing them into
one boolean is how "why did nothing trade today?" becomes unanswerable.

Sterling is a live-money product: there is one execution mode. The master live
switch (:data:`LIVE_EXECUTION_ENABLED`) is OFF by default and exists as an
operator KILL SWITCH -- ``STERLING_LIVE_EXECUTION=0`` stops every new entry
while exits, protection and reconciliation keep working.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Final, Iterator, Mapping

from app.core.focus import FOCUSABLE, FocusPolicy, focus_policy
from app.core.horizon import HorizonMode, LaneState, canonical_mode

log = logging.getLogger(__name__)

#: The operator kill switch. Unset or blank means DISABLED; enabling entries requires explicit opt-in.
#: ``0``/``false``/``off``/``no``/``disabled`` turn new-entry origination off.
LIVE_EXECUTION_ENV: Final[str] = "STERLING_LIVE_EXECUTION"

_KILL_SWITCH_VALUES: Final[frozenset[str]] = frozenset(
    {"0", "false", "off", "no", "disabled"}
)
_ENABLED_VALUES: Final[frozenset[str]] = frozenset(
    {"1", "true", "on", "yes", "enabled"}
)


def parse_live_execution(raw: str | None) -> bool:
    """Resolve the kill-switch setting. ``True`` means new entries are allowed.

    Unset or blank is the shipped default (disabled). A recognised "off" token
    engages the kill switch. Any other value is a typo in a safety control, and
    a typo must not leave real money flowing, so it also engages the kill switch
    (and is logged, so the operator can see why entries stopped).
    """
    text = (raw or "").strip().lower()
    if not text:
        return False
    if text in _ENABLED_VALUES:
        return True
    if text in _KILL_SWITCH_VALUES:
        return False
    log.error(
        "%s=%r is not a recognised value; treating it as the KILL SWITCH "
        "(new entries disabled). Use 1/true/on to enable or 0/false/off to disable.",
        LIVE_EXECUTION_ENV,
        raw,
    )
    return False


def live_execution_state(env: Mapping[str, str] | None = None) -> dict[str, object]:
    """Operator-readable account of the master switch and where it came from."""
    source = env if env is not None else os.environ
    raw = source.get(LIVE_EXECUTION_ENV)
    enabled = parse_live_execution(raw)
    if (raw or "").strip():
        origin = f"{LIVE_EXECUTION_ENV}={raw.strip()}"
    else:
        origin = f"{LIVE_EXECUTION_ENV} unset (default: disabled)"
    return {"enabled": enabled, "source": origin, "env": LIVE_EXECUTION_ENV}


#: Master live switch, resolved once at import. DISABLED by default; the operator
#: kill switch ``STERLING_LIVE_EXECUTION=0`` turns new-entry origination off for
#: the whole process. It never gates exits, protection or reconciliation: a
#: switch that stopped Sterling closing a position would be a way to lose money.
LIVE_EXECUTION_ENABLED: Final[bool] = parse_live_execution(
    os.environ.get(LIVE_EXECUTION_ENV)
)


def entry_transport_refusal(client: object) -> str | None:
    """Why an exposure-increasing order may not be sent through ``client``.

    ``None`` means it may. The client's broker mode has to be an explicit
    ``True``/``False``: a client whose mode is missing is UNKNOWN, and an
    unlabelled transport is refused rather than guessed at, because a
    construction bug must never become a real-money bypass.

    ``True`` is the in-process test double (no broker capital moves). The kill
    switch applies to every transport, so engaging it stops new entries
    everywhere without exceptions.
    """
    if not LIVE_EXECUTION_ENABLED:
        return "KILL_SWITCH"
    mode = getattr(client, "_is_paper", None)
    if mode is True or mode is False:
        return None
    return "BROKER_MODE_UNKNOWN"


def entry_transport_allowed(client: object) -> bool:
    """May a new, exposure-increasing order be sent through this broker client?"""
    return entry_transport_refusal(client) is None


def noncanonical_broker_write_allowed(client: object) -> bool:
    """Permit a noncanonical broker mutation only on an explicit test-double transport.

    A generic legacy facade cannot prove that a caller-labelled "reduce_only"
    order really reduces broker inventory, nor that a cancellation will not
    remove protection. Every real-money write therefore has to go through the
    canonical execution service; UNKNOWN and live transports fail closed here.
    """
    return getattr(client, "_is_paper", None) is True


def noncanonical_entry_transport_allowed(client: object) -> bool:
    """Permit noncanonical exposure-increasing writes only on a test double."""
    return noncanonical_broker_write_allowed(client)


#: States in which a lane may create new exposure at all. Sterling has one
#: execution mode, live; the PAPER and SHADOW states remain in the
#: :class:`LaneState` vocabulary only so old stored rows still parse, and no
#: lane in them may originate.
_ORIGINATING_STATES: Final[frozenset[LaneState]] = frozenset(
    {
        LaneState.LIVE_MINIMUM,
        LaneState.LIVE_SCALED,
    }
)

#: States that place real capital at risk.
_LIVE_STATES: Final[frozenset[LaneState]] = frozenset(
    {LaneState.LIVE_MINIMUM, LaneState.LIVE_SCALED}
)


@dataclass(frozen=True)
class LaneDefinition:
    """Declared state of one ``(strategy, mode)`` lane."""

    strategy_id: str
    mode: HorizonMode
    state: LaneState

    #: Are this lane's entry, exit, sizing and risk rules written down and
    #: hashable? ``False`` means the lane exists as a plan, not as a strategy.
    rules_defined: bool

    #: Why the lane is where it is. Shown in the dashboard and the doctor
    #: report, so an operator never has to read this file to understand a
    #: refusal.
    note: str

    @property
    def lane_key(self) -> str:
        return f"{self.strategy_id}:{self.mode.value}"


def _lane(strategy: str, mode: HorizonMode, state: LaneState, defined: bool, note: str):
    return LaneDefinition(
        strategy_id=strategy,
        mode=mode,
        state=state,
        rules_defined=defined,
        note=note,
    )


_SNAPBACK: Final[tuple[LaneDefinition, ...]] = (
    _lane(
        "snapback",
        HorizonMode.ULTRA_SCALPING,
        LaneState.RESEARCH,
        False,
        "1m reversal mechanics are sketched, not frozen; no rule hash yet",
    ),
    _lane(
        "snapback",
        HorizonMode.SCALPING,
        LaneState.LIVE_MINIMUM,
        True,
        "live: the 'scalp' path; day-one exposure caps apply (live_minimum)",
    ),
    _lane(
        "snapback",
        HorizonMode.INTRADAY,
        LaneState.RESEARCH,
        False,
        # There is no separate intraday strategy in the runtime today. The
        # engine branches on `trading_mode in ("scalp", "intraday")` and reads
        # the same scalp_* knobs for both, so "intraday" is the scalping rule
        # wearing a second label. Treating them as two lanes would demand two
        # 300-trade samples from one rule and let the same edge be counted
        # twice. The lane opens once it has rules of its own: a 5-15m signal
        # timeframe and a 45min-4h budget, as specified.
        "no distinct rules yet: shares the scalp_* path and knobs with scalping",
    ),
    _lane(
        "snapback",
        HorizonMode.OVERNIGHT,
        LaneState.RESEARCH,
        False,
        # The specification labels this lane RESEARCH and, in the dashboard
        # mock, DISABLED. Both readings agree on the only thing that matters:
        # it must not trade. It is kept RESEARCH per the lane section, and
        # blocked by rules_defined=False, because gap policy, DTE policy,
        # overnight theta, stop behaviour and hedging are all still undefined.
        "gap, DTE, theta, stop and hedge policy are undefined; cannot be hashed",
    ),
    _lane(
        "snapback",
        HorizonMode.SWING,
        LaneState.LIVE_MINIMUM,
        True,
        "live: the daily lineage; day-one exposure caps apply (live_minimum)",
    ),
)

_SUPERTREND: Final[tuple[LaneDefinition, ...]] = (
    _lane(
        "supertrend",
        HorizonMode.ULTRA_SCALPING,
        LaneState.RESEARCH,
        True,
        "1m Heikin-Ashi, same triple-ST alignment rule; no engine runs this timeframe",
    ),
    _lane(
        "supertrend",
        HorizonMode.SCALPING,
        LaneState.RESEARCH,
        True,
        "5m Heikin-Ashi, same triple-ST alignment rule; no engine runs this timeframe",
    ),
    _lane(
        "supertrend",
        HorizonMode.INTRADAY,
        LaneState.RESEARCH,
        True,
        "15m Heikin-Ashi; hard square-off 15:20 IST; no engine runs this timeframe",
    ),
    _lane(
        "supertrend",
        HorizonMode.OVERNIGHT,
        LaneState.RESEARCH,
        True,
        "60m Heikin-Ashi; shares the swing engine; gap, theta and weekend policy undefined",
    ),
    _lane(
        "supertrend",
        HorizonMode.SWING,
        LaneState.LIVE_MINIMUM,
        True,
        # The Kite engine (id 'sterling-kite') is a single 60m Heikin-Ashi
        # engine, so this is the only SuperTrend lane the runtime really
        # trades. The other four modes have rules on paper but no engine behind
        # them, so they stay RESEARCH rather than advertising capacity that
        # does not exist.
        "live: the 60m Heikin-Ashi engine; day-one exposure caps apply (live_minimum)",
    ),
)

LANES: Final[Mapping[str, LaneDefinition]] = {
    lane.lane_key: lane for lane in (*_SNAPBACK, *_SUPERTREND)
}


class UnknownLane(KeyError):
    """No such strategy-mode lane."""


def lane_key(strategy_id: str, mode: str | HorizonMode) -> str:
    return f"{strategy_id.strip().lower()}:{canonical_mode(mode).value}"


def get_lane(strategy_id: str, mode: str | HorizonMode) -> LaneDefinition:
    key = lane_key(strategy_id, mode)
    try:
        return LANES[key]
    except KeyError:
        raise UnknownLane(key) from None


def all_lanes() -> Iterator[LaneDefinition]:
    yield from LANES.values()


def lanes_for(strategy_id: str) -> tuple[LaneDefinition, ...]:
    name = strategy_id.strip().lower()
    return tuple(lane for lane in LANES.values() if lane.strategy_id == name)


@dataclass(frozen=True)
class OriginationDecision:
    """Whether a lane may open new exposure, and why not when it may not."""

    allowed: bool
    lane_key: str
    #: Stable refusal code, safe to record in evidence and assert on in tests.
    reason: str = ""
    detail: str = ""

    def __bool__(self) -> bool:  # pragma: no cover - convenience only
        return self.allowed


def may_originate(
    strategy_id: str,
    mode: str | HorizonMode,
    *,
    policy: FocusPolicy | None = None,
    live_enabled: bool | None = None,
) -> OriginationDecision:
    """May this lane open new exposure right now?

    Refusals are ordered so the most fundamental reason wins: an unfocused
    strategy is reported as unfocused even if its lane is also disabled, which
    is what the operator needs to act on.
    """
    try:
        lane = get_lane(strategy_id, mode)
    except UnknownLane as exc:
        return OriginationDecision(
            False, str(exc.args[0]), "UNKNOWN_LANE", "no such strategy-mode lane"
        )

    focus = policy if policy is not None else focus_policy()
    if not focus.may_originate(lane.strategy_id):
        return OriginationDecision(
            False,
            lane.lane_key,
            "STRATEGY_NOT_FOCUSED",
            focus.refusal_reason(lane.strategy_id) or "",
        )

    if not lane.rules_defined:
        return OriginationDecision(
            False, lane.lane_key, "LANE_RULES_UNDEFINED", lane.note
        )

    if lane.state not in _ORIGINATING_STATES:
        return OriginationDecision(
            False,
            lane.lane_key,
            "LANE_STATE_BLOCKS_ORIGINATION",
            f"state={lane.state.value}: {lane.note}",
        )

    live = LIVE_EXECUTION_ENABLED if live_enabled is None else live_enabled
    if lane.state in _LIVE_STATES and not live:
        return OriginationDecision(
            False,
            lane.lane_key,
            "LIVE_EXECUTION_DISABLED",
            f"new entries are halted by the operator kill switch ({LIVE_EXECUTION_ENV}); "
            "exits, protection and reconciliation keep running",
        )

    return OriginationDecision(True, lane.lane_key)


#: Release originator id -> the lane-registry strategy that backs it. An
#: originator mapped to ``None`` has a runtime contract of its own and is not
#: expressible as a five-mode lane without inventing a horizon mode.
_ORIGINATOR_LANE_STRATEGY: Final[Mapping[str, str | None]] = {
    "sterling-kite": "supertrend",
    "snapback": "snapback",
    "gamma_move": None,
}


def originator_rows() -> list[dict[str, object]]:
    """One row per strategy that may open new exposure in this release.

    The lane registry only models Snapback and SuperTrend. Gamma-Unwind is a
    release originator with its own runtime contract (its own entry, stop and
    sizing rules), so it appears here, derived from the release-scope allowlist
    and the master live switch, rather than as an invented fifth-or-sixth lane.
    """
    from app.core.release_scope import RELEASE_ORIGINATORS

    rows: list[dict[str, object]] = []
    for originator_id, name in sorted(RELEASE_ORIGINATORS.items()):
        backing = _ORIGINATOR_LANE_STRATEGY.get(originator_id)
        live_lanes = (
            [
                lane.lane_key
                for lane in lanes_for(backing)
                if may_originate(lane.strategy_id, lane.mode).allowed
            ]
            if backing
            else []
        )
        if backing:
            live_capable = bool(live_lanes)
            reason = "" if live_capable else "no lane of this strategy may originate"
        else:
            live_capable = LIVE_EXECUTION_ENABLED
            reason = "" if live_capable else "LIVE_EXECUTION_DISABLED"
        rows.append(
            {
                "originator_id": originator_id,
                "name": name,
                "lane_backed": backing is not None,
                "live_lanes": live_lanes,
                "live_capable": live_capable,
                "reason": reason,
            }
        )
    return rows


def dashboard_rows() -> list[dict[str, object]]:
    """One row per lane for the operator dashboard."""
    rows: list[dict[str, object]] = []
    for strategy in sorted(FOCUSABLE):
        for lane in lanes_for(strategy):
            decision = may_originate(lane.strategy_id, lane.mode)
            rows.append(
                {
                    "strategy": lane.strategy_id,
                    "mode": lane.mode.value,
                    "lane_key": lane.lane_key,
                    "state": lane.state.value,
                    "rules_defined": lane.rules_defined,
                    "may_originate": decision.allowed,
                    "reason": decision.reason,
                    "note": lane.note,
                }
            )
    return rows
