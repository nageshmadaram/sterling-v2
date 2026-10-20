"""The ten lanes, the origination gate, and Snapback's mode adapters."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("allow_live_execution")

from app.core.focus import parse_focus
from app.core.horizon import HorizonMode, LaneState
from app.core.lane_registry import (
    LANES,
    LIVE_EXECUTION_ENABLED,
    UnknownLane,
    all_lanes,
    dashboard_rows,
    get_lane,
    lanes_for,
    may_originate,
)
from app.engines.snapback import lanes as snapback_lanes
from app.engines.snapback.config import TRADING_MODES
from app.engines.snapback.manifest import compute_rule_hash

BOTH = parse_focus("snapback,supertrend")
RUNTIME = "b" * 40
TAG = "snapback-prospective-runtime-1.6"


# ── registry shape ────────────────────────────────────────────────────────


def test_there_are_exactly_ten_lanes():
    assert len(LANES) == 10
    assert len(lanes_for("snapback")) == 5
    assert len(lanes_for("supertrend")) == 5


def test_every_strategy_has_every_canonical_mode():
    for strategy in ("snapback", "supertrend"):
        modes = {lane.mode for lane in lanes_for(strategy)}
        assert modes == set(HorizonMode)


def test_lane_keys_are_unique():
    keys = [lane.lane_key for lane in all_lanes()]
    assert len(keys) == len(set(keys))


def test_unknown_lane_raises():
    with pytest.raises(UnknownLane):
        get_lane("gamma_move", "swing")


# ── the live floor ────────────────────────────────────────────────────────


def test_live_execution_defaults_off_without_operator_authorization():
    """Test process opt-in must not masquerade as the production import default."""
    import os
    import subprocess
    import sys
    env = dict(os.environ)
    env.pop("STERLING_LIVE_EXECUTION", None)
    result = subprocess.check_output(
        [sys.executable, "-c", "from app.core.lane_registry import LIVE_EXECUTION_ENABLED; print(int(LIVE_EXECUTION_ENABLED))"],
        env=env, text=True,
    )
    assert result.strip() == "0"


@pytest.mark.parametrize("raw", ["1", "true", "ON", "yes", "enabled"])
def test_the_kill_switch_stays_off_unless_an_operator_sets_it(raw):
    from app.core.lane_registry import parse_live_execution

    assert parse_live_execution(raw) is True


@pytest.mark.parametrize("raw", [None, "", "  ", "0", "false", "FALSE", "off", "no", "disabled", " 0 "])
def test_the_kill_switch_values_turn_new_entries_off(raw):
    from app.core.lane_registry import parse_live_execution

    assert parse_live_execution(raw) is False


@pytest.mark.parametrize("raw", ["maybe", "of", "2", "dissabled"])
def test_a_typo_in_the_kill_switch_fails_toward_safety(raw):
    """A mistyped safety control must not leave real money flowing."""
    from app.core.lane_registry import parse_live_execution

    assert parse_live_execution(raw) is False


def test_the_kill_switch_is_read_from_the_environment_at_import():
    import subprocess
    import sys
    from pathlib import Path

    backend_root = Path(__file__).resolve().parents[2]

    code = (
        "from app.core.lane_registry import LIVE_EXECUTION_ENABLED, may_originate;"
        "d = may_originate('snapback', 'swing');"
        "print(LIVE_EXECUTION_ENABLED, d.allowed, d.reason)"
    )
    off = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        env={**__import__("os").environ, "STERLING_LIVE_EXECUTION": "off"},
        cwd=backend_root,
    )
    assert off.stdout.split() == ["False", "False", "LIVE_EXECUTION_DISABLED"], off.stderr
    on = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        env={k: v for k, v in __import__("os").environ.items()
             if k != "STERLING_LIVE_EXECUTION"},
        cwd=backend_root,
    )
    assert on.stdout.split() == ["False", "False", "LIVE_EXECUTION_DISABLED"], on.stderr


def test_the_promoted_lanes_are_live_minimum():
    """The lanes the runtime really trades are LIVE_MINIMUM, nothing else is."""
    live = {
        lane.lane_key for lane in all_lanes() if lane.state is LaneState.LIVE_MINIMUM
    }
    assert live == set(LANES)
    for lane in all_lanes():
        assert lane.state is not LaneState.LIVE_SCALED
        assert lane.state not in (LaneState.PAPER, LaneState.SHADOW)
        if lane.state is LaneState.LIVE_MINIMUM:
            assert lane.rules_defined


def test_a_live_lane_stops_originating_when_the_kill_switch_is_engaged():
    lane = get_lane("snapback", "swing")
    assert lane.state is LaneState.LIVE_MINIMUM
    decision = may_originate("snapback", "swing", policy=BOTH, live_enabled=False)
    assert decision.allowed is False
    assert decision.reason == "LIVE_EXECUTION_DISABLED"
    assert "kill switch" in decision.detail
    assert may_originate(
        "snapback", "swing", policy=BOTH, live_enabled=True
    ).allowed is True


def test_the_kill_switch_is_read_when_the_decision_is_made(monkeypatch):
    from app.core import lane_registry

    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", False)
    for key in ("snapback:scalping", "snapback:swing", "supertrend:swing"):
        strategy, _, mode = key.partition(":")
        decision = may_originate(strategy, mode, policy=BOTH)
        assert decision.allowed is False
        assert decision.reason == "LIVE_EXECUTION_DISABLED"
    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", True)
    for key in ("snapback:scalping", "snapback:swing", "supertrend:swing"):
        strategy, _, mode = key.partition(":")
        assert may_originate(strategy, mode, policy=BOTH).allowed is True


def test_a_lane_in_a_non_live_state_never_originates():
    """PAPER and SHADOW survive only as vocabulary; neither opens exposure."""
    lane = get_lane("snapback", "swing")
    for state in (LaneState.PAPER, LaneState.SHADOW, LaneState.DISABLED,
                  LaneState.BLOCKED, LaneState.RESEARCH):
        with pytest.MonkeyPatch.context() as patch:
            patch.setitem(
                LANES, lane.lane_key,
                type(lane)(strategy_id=lane.strategy_id, mode=lane.mode, state=state,
                           rules_defined=True, note="state under test"),
            )
            decision = may_originate("snapback", "swing", policy=BOTH)
        assert decision.allowed is False
        assert decision.reason == "LANE_STATE_BLOCKS_ORIGINATION"


# ── origination gate ──────────────────────────────────────────────────────


def test_a_live_lane_with_defined_rules_may_originate():
    assert may_originate("snapback", "swing", policy=BOTH).allowed is True
    assert may_originate("snapback", "scalping", policy=BOTH).allowed is True
    assert may_originate("supertrend", "swing", policy=BOTH).allowed is True


def test_a_research_lane_may_not_originate(monkeypatch):
    from dataclasses import replace
    for mode in HorizonMode:
        lane = get_lane('supertrend', mode)
        monkeypatch.setitem(LANES, lane.lane_key, replace(lane, state=LaneState.RESEARCH))
        decision = may_originate('supertrend', mode, policy=BOTH)
        assert not decision.allowed
        assert decision.reason == 'LANE_STATE_BLOCKS_ORIGINATION'


def test_a_lane_without_rules_cannot_originate(monkeypatch):
    from dataclasses import replace
    for mode in HorizonMode:
        lane = get_lane('snapback', mode)
        monkeypatch.setitem(LANES, lane.lane_key, replace(lane, rules_defined=False))
        decision = may_originate('snapback', mode, policy=BOTH)
        assert not decision.allowed
        assert decision.reason == 'LANE_RULES_UNDEFINED'


def test_all_implemented_modes_have_rules_and_distinct_identity():
    for strategy in ('snapback', 'supertrend'):
        assert all(get_lane(strategy, mode).rules_defined for mode in HorizonMode)
    assert snapback_lanes.lane_rule_hash('intraday') != snapback_lanes.lane_rule_hash('scalping')


def test_focus_refusal_outranks_a_lane_refusal():
    """The operator needs the reason they can act on."""
    only_supertrend = parse_focus("supertrend")
    decision = may_originate("snapback", "overnight", policy=only_supertrend)
    assert decision.reason == "STRATEGY_NOT_FOCUSED"


def test_dashboard_lists_every_lane_with_a_reason():
    rows = dashboard_rows()
    assert len(rows) == 10
    for row in rows:
        assert row["note"]
        if not row["may_originate"]:
            assert row["reason"]
    assert {r['lane_key'] for r in rows if r['may_originate']} == set(LANES)


def test_gamma_unwind_is_live_capable_through_the_release_scope_allowlist():
    """Gamma has its own runtime contract; it is not squeezed into a five-mode lane."""
    from app.core.lane_registry import originator_rows
    from app.core.release_scope import RELEASE_ORIGINATORS

    rows = {r["originator_id"]: r for r in originator_rows()}
    assert set(rows) == set(RELEASE_ORIGINATORS)
    assert rows["gamma_move"]["live_capable"] is True
    assert rows["gamma_move"]["lane_backed"] is False
    assert set(rows["snapback"]["live_lanes"]) == {lane.lane_key for lane in lanes_for("snapback")}
    assert set(rows["sterling-kite"]["live_lanes"]) == {lane.lane_key for lane in lanes_for("supertrend")}
    assert all(r["live_capable"] for r in rows.values())
    assert len(LANES) == 10, "no horizon mode is invented for Gamma-Unwind"


def test_originator_rows_follow_the_kill_switch(monkeypatch):
    from app.core import lane_registry

    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", False)
    rows = {r["originator_id"]: r for r in lane_registry.originator_rows()}
    assert not any(r["live_capable"] for r in rows.values())
    assert rows["gamma_move"]["reason"] == "LIVE_EXECUTION_DISABLED"


# ── snapback adapters ─────────────────────────────────────────────────────


def test_every_legacy_engine_mode_maps_to_a_canonical_lane():
    assert set(TRADING_MODES) == set(snapback_lanes.LEGACY_MODE_MAP)
    for legacy, canonical in snapback_lanes.LEGACY_MODE_MAP.items():
        assert snapback_lanes.canonical_mode_of_engine(legacy) is canonical


def test_scalp_is_the_only_rename():
    assert snapback_lanes.engine_mode_of("scalping") == "scalp"
    assert snapback_lanes.engine_mode_of("swing") == "swing"
    assert snapback_lanes.engine_mode_of("intraday") == "intraday"


def test_new_modes_have_explicit_engine_counterparts():
    assert snapback_lanes.engine_mode_of('ultra_scalping') == 'ultra_scalping'
    assert snapback_lanes.engine_mode_of('overnight') == 'overnight'


def test_the_legacy_spelling_is_recorded_on_the_identity():
    identity = snapback_lanes.identity_for(
        "scalp", runtime_sha=RUNTIME, release_tag=TAG
    )
    assert identity.mode is HorizonMode.SCALPING
    assert identity.legacy_mode == "scalp"
    assert identity.lane_key == "snapback:scalping"


def test_the_manifest_rule_hash_cannot_tell_two_modes_apart():
    """The defect the lane hash exists to correct.

    compute_rule_hash covers only the daily swing rules, so it is blind to
    trading_mode and to every scalp_* knob.
    """
    from app.engines.snapback.config import SnapbackConfig

    swing = SnapbackConfig(trading_mode="swing")
    scalp = SnapbackConfig(trading_mode="scalp", scalp_target_points=9.0)
    assert compute_rule_hash(swing) == compute_rule_hash(scalp)


def test_the_lane_rule_hash_separates_all_five_modes():
    hashes = {
        mode: snapback_lanes.lane_rule_hash(mode) for mode in HorizonMode
    }
    assert len(set(hashes.values())) == 5


def test_a_changed_scalp_knob_changes_only_the_scalping_lane_hash():
    from app.engines.snapback.config import SnapbackConfig

    base = SnapbackConfig()
    tweaked = SnapbackConfig(scalp_target_points=9.0)
    assert snapback_lanes.lane_rule_hash("scalping", tweaked) != (
        snapback_lanes.lane_rule_hash("scalping", base)
    )
    assert snapback_lanes.lane_rule_hash("swing", tweaked) == (
        snapback_lanes.lane_rule_hash("swing", base)
    )


def test_identities_for_the_five_modes_are_ten_distinct_things_with_supertrend():
    snap = {
        snapback_lanes.identity_for(
            mode, runtime_sha=RUNTIME, release_tag=TAG
        ).identity_hash
        for mode in HorizonMode
    }
    assert len(snap) == 5


def test_identity_is_stable_across_calls():
    a = snapback_lanes.identity_for("swing", runtime_sha=RUNTIME, release_tag=TAG)
    b = snapback_lanes.identity_for("swing", runtime_sha=RUNTIME, release_tag=TAG)
    assert a.identity_hash == b.identity_hash


def test_a_different_release_is_a_different_identity():
    a = snapback_lanes.identity_for("swing", runtime_sha=RUNTIME, release_tag=TAG)
    b = snapback_lanes.identity_for(
        "swing", runtime_sha=RUNTIME, release_tag="runtime-1.7"
    )
    assert a.identity_hash != b.identity_hash



def test_entry_transport_floor_allows_labelled_clients_and_refuses_unknown():
    from types import SimpleNamespace
    from app.core.lane_registry import entry_transport_allowed, entry_transport_refusal

    assert entry_transport_allowed(SimpleNamespace(_is_paper=False)) is True
    assert entry_transport_allowed(SimpleNamespace(_is_paper=True)) is True
    # A client whose broker mode is missing or not an explicit bool is UNKNOWN.
    for unknown in (SimpleNamespace(), SimpleNamespace(_is_paper=None),
                    SimpleNamespace(_is_paper="false"), SimpleNamespace(_is_paper=0)):
        assert entry_transport_allowed(unknown) is False
        assert entry_transport_refusal(unknown) == "BROKER_MODE_UNKNOWN"


def test_kill_switch_refuses_entries_on_every_transport(monkeypatch):
    from types import SimpleNamespace
    from app.core import lane_registry

    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", False)
    for mode in (False, True, None):
        client = SimpleNamespace(_is_paper=mode)
        assert lane_registry.entry_transport_allowed(client) is False
        assert lane_registry.entry_transport_refusal(client) == "KILL_SWITCH"


def test_noncanonical_entry_floor_never_admits_a_live_transport(monkeypatch):
    from types import SimpleNamespace
    from app.core import lane_registry

    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", True)

    assert lane_registry.noncanonical_entry_transport_allowed(
        SimpleNamespace(_is_paper=True)
    ) is True
    assert lane_registry.noncanonical_entry_transport_allowed(
        SimpleNamespace(_is_paper=False)
    ) is False
    assert lane_registry.noncanonical_entry_transport_allowed(SimpleNamespace()) is False


def test_noncanonical_broker_write_floor_does_not_trust_reduce_or_cancel_labels():
    from types import SimpleNamespace
    from app.core.lane_registry import noncanonical_broker_write_allowed

    assert noncanonical_broker_write_allowed(SimpleNamespace(_is_paper=True)) is True
    assert noncanonical_broker_write_allowed(SimpleNamespace(_is_paper=False)) is False
    assert noncanonical_broker_write_allowed(SimpleNamespace()) is False
