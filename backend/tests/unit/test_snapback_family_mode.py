"""Family Mode makes accidental broker writes impossible.

Hiding buttons is not a control. In Family Mode every exposure-increasing broker write
must pass through the canonical execution authority, and a direct client.place_order()
from any other engine must be refused.
"""

from __future__ import annotations

import pytest

from app.services.snapback_family_mode import (
    canonical_broker_capability,
    family_mode_enabled,
    guard_broker_write,
)


@pytest.fixture(autouse=True)
def _family_mode(monkeypatch):
    monkeypatch.setenv("STERLING_FAMILY_MODE", "true")
    yield
    monkeypatch.delenv("STERLING_FAMILY_MODE", raising=False)


def test_family_mode_reads_the_environment():
    assert family_mode_enabled() is True


def test_direct_broker_write_is_refused_without_capability():
    with pytest.raises(PermissionError) as excinfo:
        guard_broker_write("place_order")

    assert "canonical execution" in str(excinfo.value).lower()


def test_broker_write_inside_the_capability_is_allowed():
    with canonical_broker_capability("INTENT-1"):
        guard_broker_write("place_order")  # must not raise


def test_capability_does_not_leak_outside_its_block():
    with canonical_broker_capability("INTENT-1"):
        guard_broker_write("place_order")

    with pytest.raises(PermissionError):
        guard_broker_write("place_order")


def test_capability_is_released_even_on_failure():
    with pytest.raises(RuntimeError):
        with canonical_broker_capability("INTENT-1"):
            raise RuntimeError("broker rejected")

    with pytest.raises(PermissionError):
        guard_broker_write("place_order")


def test_outside_family_mode_the_guard_is_inert(monkeypatch):
    monkeypatch.setenv("STERLING_FAMILY_MODE", "false")

    # Other deployments keep their existing behaviour.
    guard_broker_write("place_order")


@pytest.mark.parametrize("operation", ["place_order", "modify_order", "cancel_order_unsafe"])
def test_every_mutating_operation_is_guarded(operation):
    with pytest.raises(PermissionError):
        guard_broker_write(operation)


def test_reduce_capability_authorises_verified_exit_order_only():
    from app.services.snapback_family_mode import INTENT_REDUCE

    with canonical_broker_capability("EXIT-1", intent=INTENT_REDUCE):
        guard_broker_write("place_order")
        with pytest.raises(PermissionError):
            guard_broker_write("modify_order")


def test_protection_capability_authorises_gtt_but_not_entry():
    from app.services.snapback_family_mode import INTENT_PROTECT

    with canonical_broker_capability("PROTECT-1", intent=INTENT_PROTECT):
        guard_broker_write("place_gtt")
        guard_broker_write("modify_gtt")
        with pytest.raises(PermissionError):
            guard_broker_write("place_order")


def test_protective_operations_require_their_matching_intent():
    from app.services.snapback_family_mode import (
        INTENT_CANCEL,
        INTENT_PROTECT,
        INTENT_REDUCE,
    )

    # Entry authority must never double as permission to remove broker protection.
    with canonical_broker_capability("ENTRY-1"):
        for operation in ("cancel_order", "cancel_gtt", "exit_order"):
            with pytest.raises(PermissionError):
                guard_broker_write(operation)

    # Protection replacement may cancel its own old GTT, but cannot cancel generic
    # orders or submit an exit.
    with canonical_broker_capability("PROTECT-2", intent=INTENT_PROTECT):
        guard_broker_write("cancel_gtt")
        with pytest.raises(PermissionError):
            guard_broker_write("cancel_order")
        with pytest.raises(PermissionError):
            guard_broker_write("exit_order")

    # A verified reduction may remove the protective GTT that would otherwise race
    # the exit and may send the exit itself, but it is not a generic cancel token.
    with canonical_broker_capability("EXIT-2", intent=INTENT_REDUCE):
        guard_broker_write("cancel_gtt")
        guard_broker_write("exit_order")
        with pytest.raises(PermissionError):
            guard_broker_write("cancel_order")

    # Explicit cancellation can cancel broker orders / GTTs but cannot place entries
    # or use the dedicated exit transport.
    with canonical_broker_capability("CANCEL-2", intent=INTENT_CANCEL):
        guard_broker_write("cancel_order")
        guard_broker_write("cancel_gtt")
        with pytest.raises(PermissionError):
            guard_broker_write("place_order")
        with pytest.raises(PermissionError):
            guard_broker_write("exit_order")


def test_other_strategy_runners_do_not_start_in_family_mode():
    import inspect

    import main

    source = inspect.getsource(main)

    assert "family_mode_enabled" in source


def test_canonical_execution_wraps_the_broker_call_in_the_capability():
    """The only path that may write to the broker holds the capability while it does."""
    import inspect

    from app.services.execution_service import CanonicalExecutionService

    source = inspect.getsource(CanonicalExecutionService.submit_order)

    assert "canonical_broker_capability" in source
    # The capability must wrap the call itself, not merely be imported.
    assert "with canonical_broker_capability" in source
    wrapped = source.split("with canonical_broker_capability", 1)[1]
    assert "place_fn(" in wrapped.split("except", 1)[0]


def test_broker_transport_consults_the_guard():
    import inspect

    from app.services.exchanges.kite.client import KiteClient

    for name in ("place_order", "delete_gtt"):
        source = inspect.getsource(getattr(KiteClient, name))
        assert "guard_broker_write" in source, name


def test_durable_protection_lifecycle_holds_protection_capability():
    import inspect

    from app.services.kite_engine import execution_lifecycle

    source = inspect.getsource(execution_lifecycle._place_protection)
    assert "INTENT_PROTECT" in source
    assert "with canonical_broker_capability" in source
    wrapped = source.split("with canonical_broker_capability", 1)[1]
    assert "stops.place_stop" in wrapped
    assert "stops.move_stop" in wrapped
