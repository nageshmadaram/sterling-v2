"""No exposure increase may reach the broker except through the canonical service.

This is a static test on purpose. A runtime test proves that the paths we
thought to exercise behave; this one proves that no *new* path was written. The
failure mode it guards against is not a bug in an order — it is someone adding
`await client.place_order_option(...)` to a new engine file next year, with all
the care in the world and none of the admission checks.

The rule has three parts:

  * production code may not call a broker order-placing method directly;
  * the exception is the canonical execution service itself, which is where the
    single permitted send lives;
  * exits, cancels and protective orders are NOT covered — refusing to close a
    position is how a safety mechanism becomes the thing that loses the money.
"""
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"

#: Methods that can only ever increase exposure at the broker.
ENTRY_METHODS = {"place_order", "place_order_option", "place_order_future"}

#: The one module allowed to call them, plus the transport that implements them.
ALLOWED_FILES = {
    "services/execution_service.py",          # the canonical authority itself
    "services/exchanges/kite/client.py",      # the transport being called
}

#: Paths that are not production order flow.
SKIPPED_PREFIXES = ("tests/", "study/")

#: The direct calls that existed when this rule was written, counted per file and
#: method. None of these engines is family-visible — `FAMILY_VISIBLE_STRATEGIES`
#: is `{"snapback"}` — and in Family Mode `guard_broker_write` refuses every one
#: of them at the transport, so they cannot reach a family account today. They
#: are recorded rather than converted because converting six engines at once, in
#: one release, is how a safety refactor becomes the outage.
#:
#: The contract is that this baseline only ever shrinks. A new direct call, or an
#: extra one in a file that already has some, fails the test. When an engine is
#: converted, delete its line — leaving a stale entry quietly re-permits a call.
KNOWN_DIRECT_CALLS: dict[tuple[str, str], int] = {
    ("agents/broker_agent.py", "place_order"): 1,
    ("services/adaptive_edge_runner.py", "place_order"): 1,
    ("services/execution/order_router.py", "place_order"): 1,
    ("services/execution/order_router.py", "place_order_option"): 1,
    ("services/gamma_move_runner.py", "place_order"): 1,
    ("services/intraday_runner.py", "place_order"): 2,
    ("services/opening_volume_execution.py", "place_order_option"): 1,
}


# Grandfathered strategy entry functions that still use the transport directly
# must enforce the immutable Production Shadow floor before their BUY call.
DIRECT_ENTRY_FUNCTIONS: dict[str, tuple[str, str]] = {
    # Noncanonical/unpromoted entry paths are converted to the permanent
    # paper-only floor one at a time. Remaining entries still on
    # entry_transport_allowed require canonical migration before live promotion.
    "agents/broker_agent.py": ("place_order", "noncanonical_broker_write_allowed"),
    "services/execution/order_router.py": (
        "_submit_live",
        "noncanonical_broker_write_allowed",
    ),
}


def _relative(path: Path) -> str:
    return str(path.relative_to(APP))


def _direct_entry_calls(path: Path) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:  # pragma: no cover - a syntax error is another test's job
        return []

    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in ENTRY_METHODS:
            continue
        # `self.place_order(...)` inside the transport is the implementation, not
        # a bypass; ALLOWED_FILES covers that. Everything else is a caller.
        found.append((node.lineno, func.attr))
    return found


def _production_files() -> list[Path]:
    files = []
    for path in APP.rglob("*.py"):
        rel = _relative(path)
        if rel.startswith(SKIPPED_PREFIXES) or rel in ALLOWED_FILES:
            continue
        files.append(path)
    return files


def _observed_direct_calls() -> dict[tuple[str, str], int]:
    seen: Counter[tuple[str, str]] = Counter()
    for path in _production_files():
        rel = _relative(path)
        for _lineno, attr in _direct_entry_calls(path):
            seen[(rel, attr)] += 1
    return dict(seen)


def test_no_new_direct_broker_entry_call_is_introduced():
    """The guard itself: a new bypass fails this test the day it is written."""
    observed = _observed_direct_calls()

    new_calls = []
    for key, count in sorted(observed.items()):
        allowed = KNOWN_DIRECT_CALLS.get(key, 0)
        if count > allowed:
            where, attr = key
            new_calls.append(
                f"{where}: {count} call(s) to {attr}(), baseline allows {allowed}"
            )

    assert not new_calls, (
        "An exposure-increasing broker call was added outside "
        "CanonicalExecutionService.submit_order. Route it through the canonical "
        "service (app/services/kite_engine/canonical_entry.py is the SuperTrend "
        "door); do not widen the baseline:\n  " + "\n  ".join(new_calls)
    )


def test_the_baseline_has_no_stale_entries():
    """A converted engine must be removed from the baseline, not left behind."""
    observed = _observed_direct_calls()
    stale = [
        f"{where}: {attr}() baseline {count}, actually {observed.get((where, attr), 0)}"
        for (where, attr), count in sorted(KNOWN_DIRECT_CALLS.items())
        if observed.get((where, attr), 0) < count
    ]
    assert not stale, (
        "KNOWN_DIRECT_CALLS is stale — lower or delete these entries so the rule "
        "does not silently re-permit a call that was removed:\n  " + "\n  ".join(stale)
    )


def test_grandfathered_entry_functions_enforce_the_global_live_floor():
    """A direct strategy BUY may exist temporarily, but never bypass Shadow."""
    for rel, (function_name, guard_name) in DIRECT_ENTRY_FUNCTIONS.items():
        tree = ast.parse((APP / rel).read_text(encoding="utf-8"))
        funcs = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function_name
        ]
        assert len(funcs) == 1, f"{rel}: expected exactly one {function_name}()"
        guarded = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == guard_name
            for node in ast.walk(funcs[0])
        )
        assert guarded, (
            f"{rel}:{function_name} has a direct broker entry path without "
            f"{guard_name}(); the declared transport floor could be bypassed"
        )


def test_raw_kite_order_transport_has_its_own_capability_guard():
    source = (APP / "services/exchanges/kite/client.py").read_text(encoding="utf-8")
    raw = source.split("async def _place(", 1)[1].split("async def place_order(", 1)[0]
    assert "guard_broker_write(" in raw
    assert '"exit_order" if current_intent() == INTENT_REDUCE else "place_order"' in raw


def test_the_supertrend_engine_has_no_direct_broker_entry_call():
    """The path this release converted must stay converted."""
    observed = _observed_direct_calls()
    kite_engine = {
        f"{where}:{attr}"
        for (where, attr) in observed
        if where.startswith("services/kite_engine/")
    }
    assert not kite_engine, (
        "The SuperTrend/Kite engine placed a broker order directly: "
        + ", ".join(sorted(kite_engine))
    )


def test_supertrend_engine_entry_path_uses_the_canonical_door():
    """The SuperTrend service must reach the broker through canonical_entry."""
    source = (APP / "services/kite_engine/service.py").read_text(encoding="utf-8")
    assert "canonical_entry.submit_entry(" in source, (
        "app/services/kite_engine/service.py no longer routes entries through "
        "canonical_entry.submit_entry — an entry path was rewritten to bypass "
        "canonical execution."
    )


def test_gamma_move_entry_path_uses_the_canonical_door():
    source = (APP / "services/gamma_move_runner.py").read_text(encoding="utf-8")
    assert "canonical_strategy_entry import submit_entry" in source
    assert "execution = await submit_entry(" in source


def test_adaptive_edge_entry_path_uses_the_canonical_door():
    source = (APP / "services/adaptive_edge_runner.py").read_text(encoding="utf-8")
    assert "canonical_strategy_entry import submit_entry" in source
    assert "execution = await submit_entry(" in source


def test_opening_volume_entry_path_uses_the_canonical_door():
    source = (APP / "services/opening_volume_execution.py").read_text(encoding="utf-8")
    assert "canonical_strategy_entry import submit_entry" in source
    assert "execution = await submit_entry(" in source


def test_intraday_entry_path_uses_the_canonical_door():
    """Intraday BUYs must stay behind the canonical bridge after migration."""
    source = (APP / "services/intraday_runner.py").read_text(encoding="utf-8")
    assert "intraday_canonical_entry import submit_entry" in source
    assert "result = await submit_entry(" in source


def test_intraday_direct_sells_require_broker_truth_and_reduce_authority():
    """The two remaining direct orders are exits, not an unchecked second door."""
    source = (APP / "services/intraday_runner.py").read_text(encoding="utf-8")
    assert "broker_qty = await _broker_long_quantity(client, pos)" in source
    assert "if not await _release_protection_for_exit(uid, client, pos):" in source
    assert "intent=INTENT_REDUCE" in source
    assert "refusing a second SELL" in source


def test_opening_volume_tag_recovery_never_invents_live_order_ownership():
    source = (APP / "services/opening_volume_execution.py").read_text(encoding="utf-8")
    assert "matching broker order has no canonical durable intent" in source
    assert "critical_unknown_live_order" in source
    assert "Do not cancel it" in source


def test_opening_volume_emergency_sell_is_exact_nrml_reduce_only():
    source = (APP / "services/opening_volume_execution.py").read_text(encoding="utf-8")
    assert 'want_product = str(product or "").strip().upper()' in source
    assert 'client, symbol, exchange, product="NRML"' in source
    assert 'product="NRML"' in source
    assert "intent=INTENT_REDUCE" in source


def test_kite_order_wrapper_keeps_live_option_exits_available_under_halt():
    source = (APP / "api/v1/endpoints/kite.py").read_text(encoding="utf-8")
    assert 'is_live_option_exit = (' in source
    assert 'exposure_effect=("REDUCE_EXPOSURE" if is_live_option_exit else "INCREASE_EXPOSURE")' in source
    assert 'check_daily_loss=not is_live_option_exit' in source


def test_manual_order_preflight_is_account_scoped_and_exit_aware():
    source = (APP / "services/kite_engine/service.py").read_text(encoding="utf-8")
    assert '"live" if is_live else "paper"' in source
    assert 'account_id=account_id' in source
    assert 'exposure_effect=("INCREASE_EXPOSURE" if norm == "buy" else "REDUCE_EXPOSURE")' in source
    assert 'str(getattr(held, "account_id", "") or "").strip() != account_id' in source
    assert 'str(getattr(held, "product", "") or "").strip().upper() != "NRML"' in source


def test_gtt_child_attribution_requires_exact_event_identity():
    source = (APP / "services/kite_engine/monitor.py").read_text(encoding="utf-8")
    fn = source.split("async def _attribute_trigger_fill", 1)[1].split(
        "async def _resize_broker_stop", 1
    )[0]
    assert "requested != int(p.qty)" in fn
    assert 'str(order.get("exchange") or "").strip().upper()' in fn
    assert 'str(order.get("product") or "").strip().upper()' in fn
    assert "_broker_holding" in fn
    assert "pstop.stop_status" in fn


def test_unknown_exit_postback_requires_immutable_full_order_identity():
    source = (APP / "services/kite_engine/monitor.py").read_text(encoding="utf-8")
    assert "tag_identity_matches = (" in source
    assert "reported_qty == requested_qty" in source
    assert 'str(order.get("product") or "").strip().upper()' in source
    assert 'str(order.get("exchange") or "").strip().upper()' in source


def test_unknown_exit_recovery_requires_immutable_full_order_identity():
    service_source = (APP / "services/kite_engine/service.py").read_text(encoding="utf-8")
    monitor_source = (APP / "services/kite_engine/monitor.py").read_text(encoding="utf-8")
    positions_source = (APP / "services/kite_engine/positions.py").read_text(encoding="utf-8")
    assert "exit_requested_qty: int = 0" in positions_source
    assert "p.exit_requested_qty = int(p.qty)" in monitor_source
    assert "product=product" in monitor_source
    assert 'str(order.get("product") or "").strip().upper() == wanted_product' in service_source
    assert "order_qty == requested_qty" in service_source
    assert 'str(order.get("exchange") or "").strip().upper()' in service_source


def test_canonical_monitor_exit_send_carries_reduce_authority():
    """Family Mode must never block the canonical exit at the transport boundary."""
    source = (APP / "services/kite_engine/monitor.py").read_text(encoding="utf-8")
    assert "exit-send:" in source
    assert "intent=INTENT_REDUCE" in source
    assert "result = await place(" in source


def test_legacy_order_router_auxiliary_mutations_are_paper_only():
    """Names like reduce-close/stop-replace are not broker-truth proofs."""
    source = (APP / "services/execution/order_router.py").read_text(encoding="utf-8")
    assert source.count("noncanonical_broker_write_allowed(self.adapter)") >= 3
    messages = [n.value for n in ast.walk(ast.parse(source))
                if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert any("cannot prove live broker inventory for partial close" in message for message in messages)
    assert "cannot prove live stop ownership/inventory" in source


def test_intraday_family_mutations_are_bound_to_mis_product_bucket():
    """Research runners must not authorize an MIS sell from same-symbol NRML inventory."""
    for rel in (
        "services/intraday_runner.py",
        "services/adaptive_edge_runner.py",
        "services/gamma_move_runner.py",
    ):
        source = (APP / rel).read_text(encoding="utf-8")
        if rel.endswith("gamma_move_runner.py"):
            assert 'product=_product(pos)' in source
            assert 'product=_product(pos)' in source
        else:
            assert 'want_product = "MIS"' in source
            assert 'product="MIS"' in source


def test_shared_execution_lifecycle_propagates_position_product_to_protection():
    source = (APP / "services/kite_engine/execution_lifecycle.py").read_text(encoding="utf-8")
    assert "product = positions.broker_product(p.product)" in source
    assert "product=product" in source


def test_all_shared_live_gtt_mutations_propagate_position_product():
    for rel in (
        "services/kite_engine/execution_lifecycle.py",
        "services/kite_engine/monitor.py",
        "services/kite_engine/service.py",
    ):
        source = (APP / rel).read_text(encoding="utf-8")
        assert "broker_product(p.product)" in source
        assert "product=product" in source


def test_broker_protection_proves_inventory_and_product_before_gtt_mutation():
    source = (APP / "services/kite_engine/protective_stop.py").read_text(encoding="utf-8")
    assert source.count("verify_protection_inventory(") >= 3
    assert "ambiguous broker product bucket for protection" in source
    assert '"product": str(product or K.PRODUCT_NRML).upper()' in source


def test_canonical_protection_uses_real_kite_gtt_shape_after_inventory_proof():
    source = (APP / "services/execution_service.py").read_text(encoding="utf-8")
    assert "await protective_stop.verify_protection_inventory(" in source
    assert "protective_stop.build_gtt_request(" in source
    assert "await place_gtt_fn(**gtt_request)" in source


@pytest.mark.parametrize(
    "rel, position_name",
    [
        ("services/adaptive_edge_runner.py", "position"),
        ("services/gamma_move_runner.py", "pos"),
    ],
)
def test_legacy_engine_exits_prove_broker_truth_and_gtt_release(rel, position_name):
    """Paper-only entry engines may unwind old live positions, but never blindly."""
    source = (APP / rel).read_text(encoding="utf-8")
    assert "_broker_long_quantity" in source
    assert "_cancel_protection" in source
    assert "cancel_stop_result" in source
    assert "STOP_ABSENT" in source
    assert "intent=INTENT_REDUCE" in source
    assert "broker quantity" in source


def test_legacy_live_position_management_is_bound_to_originating_account():
    model_sources = (
        (APP / "engines/intraday/position.py").read_text(encoding="utf-8"),
        (APP / "services/adaptive_edge_positions.py").read_text(encoding="utf-8"),
        (APP / "engines/gamma_move/models.py").read_text(encoding="utf-8"),
    )
    for source in model_sources:
        assert 'account_id: str = ""' in source

    for rel in (
        "services/intraday_runner.py",
        "services/adaptive_edge_runner.py",
        "services/gamma_move_runner.py",
    ):
        source = (APP / rel).read_text(encoding="utf-8")
        assert "def _position_account_matches(" in source
        assert "not _position_account_matches(client" in source


@pytest.mark.parametrize(
    "rel",
    [
        "services/intraday_runner.py",
        "services/adaptive_edge_runner.py",
        "services/gamma_move_runner.py",
    ],
)
def test_adopt_paths_prove_broker_inventory_before_resting_protection(rel):
    """An adopted phantom position must never create a naked protective SELL."""
    source = (APP / rel).read_text(encoding="utf-8")
    assert "broker_qty = await _broker_long_quantity" in source
    if rel.endswith("gamma_move_runner.py"):
        assert "broker_inventory_unverified" in source
        assert "adoption refused" in source
    else:
        assert "cannot adopt" in source
        assert "broker quantity" in source


def test_exit_paths_are_not_covered_by_this_rule():
    """Guard the guard: exits must stay callable directly.

    If someone widens ENTRY_METHODS to include cancel/exit operations, a halted
    or safe-moded system would be unable to close a position. That is a worse
    failure than the one this file prevents, so the exclusion is asserted.
    """
    forbidden_here = {"cancel_order", "place_gtt", "modify_gtt", "cancel_gtt"}
    assert not (ENTRY_METHODS & forbidden_here)


@pytest.mark.parametrize("allowed", sorted(ALLOWED_FILES))
def test_allowlist_entries_still_exist(allowed: str):
    """An allowlist entry pointing at a deleted file silently widens the rule."""
    assert (APP / allowed).exists(), f"allowlisted file {allowed} no longer exists"
