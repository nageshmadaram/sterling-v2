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
    ("services/adaptive_edge_runner.py", "place_order"): 2,
    ("services/execution/order_router.py", "place_order"): 1,
    ("services/execution/order_router.py", "place_order_option"): 1,
    ("services/gamma_move_runner.py", "place_order"): 2,
    ("services/intraday_runner.py", "place_order"): 2,
    ("services/opening_volume_execution.py", "place_order_option"): 2,
}


# Grandfathered strategy entry functions that still use the transport directly
# must enforce the immutable Production Shadow floor before their BUY call.
DIRECT_ENTRY_FUNCTIONS: dict[str, tuple[str, str]] = {
    # Noncanonical/unpromoted entry paths are converted to the permanent
    # paper-only floor one at a time. Remaining entries still on
    # entry_transport_allowed require canonical migration before live promotion.
    "agents/broker_agent.py": ("place_order", "noncanonical_broker_write_allowed"),
    "services/adaptive_edge_runner.py": (
        "arm",
        "noncanonical_entry_transport_allowed",
    ),
    "services/execution/order_router.py": (
        "_submit_live",
        "noncanonical_broker_write_allowed",
    ),
    "services/gamma_move_runner.py": (
        "arm",
        "noncanonical_entry_transport_allowed",
    ),
    "services/opening_volume_execution.py": (
        "execute_opening_scan",
        "noncanonical_entry_transport_allowed",
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
