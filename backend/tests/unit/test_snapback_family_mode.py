"""The broker-write capability guard makes accidental broker writes impossible.

Hiding buttons is not a control. On a live account every exposure-increasing broker
write must pass through the canonical execution authority, and a direct
client.place_order() from any other engine must be refused. Entry and exposure
operations are guarded unconditionally; GTT, cancel and exit operations are
additionally guarded under the retired STERLING_FAMILY_MODE switch, which only ever
tightens the guard and never suppresses a strategy.
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


def test_entry_and_exposure_writes_are_guarded_without_the_retired_switch(monkeypatch):
    monkeypatch.setenv("STERLING_FAMILY_MODE", "false")

    # Retiring Family Mode must not open the transport: a direct entry or a changed
    # live order is refused with or without the variable.
    for operation in ("place_order", "modify_order", "place_mf_order",
                      "place_mf_sip", "modify_mf_sip", "convert_position"):
        with pytest.raises(PermissionError):
            guard_broker_write(operation)
    with canonical_broker_capability("INTENT-1"):
        guard_broker_write("place_order")


def test_protection_and_cancel_writes_require_a_capability_even_with_family_mode_off(monkeypatch):
    monkeypatch.setenv("STERLING_FAMILY_MODE", "false")

    # Refusing to place a stop or close a position because a capability was
    # forgotten is how a safety control loses the money; only the retired strict
    # switch makes these require one.
    for operation in ("place_gtt", "modify_gtt", "cancel_gtt", "cancel_order", "exit_order"):
        with pytest.raises(PermissionError):
            guard_broker_write(operation)


@pytest.mark.parametrize("operation", ["place_order", "modify_order", "cancel_order_unsafe"])
def test_every_mutating_operation_is_guarded(operation):
    with pytest.raises(PermissionError):
        guard_broker_write(operation)


def test_reduce_capability_authorises_verified_exit_order_only():
    from app.services.snapback_family_mode import INTENT_REDUCE

    with canonical_broker_capability("EXIT-1", intent=INTENT_REDUCE):
        guard_broker_write("exit_order")
        with pytest.raises(PermissionError):
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


@pytest.mark.asyncio
async def test_snapback_stop_resize_holds_protection_capability(monkeypatch):
    from types import SimpleNamespace

    from app.services.kite_engine import monitor

    seen = {"calls": 0}

    async def guarded_move(*args, **kwargs):
        guard_broker_write("modify_gtt")
        seen["calls"] += 1
        return True

    monkeypatch.setattr(monitor.pstop, "move_stop", guarded_move)
    monkeypatch.setattr(monitor.state, "log", lambda *args, **kwargs: None)

    p = SimpleNamespace(
        account_id="acct-1",
        gtt_id=99,
        qty=5,
        stop_premium=90.0,
        symbol="NIFTYOPT",
        exchange="NFO",
        fill_price=100.0,
        entry_premium=100.0,
        direction="long",
        target_premium=120.0,
        product="NRML",
    )
    await monitor._resize_broker_stop(object(), "u1", p, 10)
    assert seen["calls"] == 1


def test_low_level_protective_stop_never_self_authorises():
    import inspect

    from app.services.kite_engine import protective_stop

    # Capability must be granted by an audited lifecycle owner.  If these helpers
    # start minting their own authority, any research runner could bypass Family
    # Mode simply by calling the helper.
    for fn in (
        protective_stop.place_stop,
        protective_stop.move_stop,
        protective_stop.cancel_stop_result,
    ):
        source = inspect.getsource(fn)
        assert "canonical_broker_capability" not in source


def test_snapback_lifecycle_declares_narrow_protection_intents():
    import inspect

    from app.services.kite_engine import monitor, protection, service

    resize = inspect.getsource(monitor._resize_broker_stop)
    assert "INTENT_PROTECT" in resize
    assert "with canonical_broker_capability" in resize

    exit_path = inspect.getsource(monitor._exit_position)
    assert "INTENT_CANCEL" in exit_path
    assert "cancel-entry-remainder:" in exit_path
    assert "INTENT_REDUCE" in exit_path
    assert "exit-disarm:" in exit_path

    arm = inspect.getsource(protection.arm_position)
    assert "INTENT_PROTECT" in arm
    assert "replace-protection:" in arm
    assert "retarget-protection:" in arm
    assert "arm-protection:" in arm

    manual_disarm = inspect.getsource(service.disarm_for_manual_exit)
    assert "INTENT_REDUCE" in manual_disarm
    assert "manual-exit-disarm:" in manual_disarm


def test_family_mode_no_longer_suppresses_any_strategy_runner():
    """Retired: main.py must not read the variable, so it cannot silence a loop."""
    import inspect

    import main

    source = inspect.getsource(main)

    assert "family_mode_enabled" not in source
    assert "_family_mode" not in source


def test_canonical_execution_wraps_the_broker_call_in_the_capability():
    """The only path that may write to the broker holds the capability while it does."""
    import inspect

    from app.services.execution_service import CanonicalExecutionService

    # submit_order may delegate to a private implementation; the capability has to
    # wrap the broker call wherever it lives.
    source = "".join(
        inspect.getsource(getattr(CanonicalExecutionService, name))
        for name in ("submit_order", "_submit_order")
        if hasattr(CanonicalExecutionService, name)
    )

    assert "canonical_broker_capability" in source
    # The capability must wrap the call itself, not merely be imported.
    assert "with canonical_broker_capability" in source
    wrapped = source.split("with canonical_broker_capability", 1)[1]
    assert "place_fn(" in wrapped.split("except", 1)[0]


@pytest.mark.asyncio
async def test_raw_kite_place_is_refused_without_capability_before_network():
    from app.services.exchanges.kite.client import KiteClient

    client = KiteClient(is_paper=False)
    with pytest.raises(PermissionError):
        await client._place(
            variety="regular",
            exchange="NFO",
            tradingsymbol="NIFTY26OCT25000CE",
            transaction_type="BUY",
            quantity=75,
            product="NRML",
            order_type="MARKET",
        )


def test_broker_transport_consults_the_guard():
    import inspect

    from app.services.exchanges.kite.client import KiteClient

    for name in ("_place", "place_order", "delete_gtt"):
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
