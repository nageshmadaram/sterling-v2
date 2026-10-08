"""Only an administrator may lift the process-wide halt or loosen a loss limit.

Engaging the halt and tightening the limit stay open to any authenticated user:
those only make the system more conservative.
"""
import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import kite as kite_ep
from app.core.auth import UserContext
from app.services import db, live_safety


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_DB_PATH", str(tmp_path / "risk_auth.db"))
    monkeypatch.setattr(db, "_available", True)
    live_safety.reset_all_for_tests()
    db.init()
    yield
    live_safety.reset_all_for_tests()


VIEWER = UserContext(user_id="viewer", username="viewer", role="user")
ADMIN = UserContext(user_id="boss", username="boss", role="admin")


@pytest.mark.asyncio
async def test_a_non_admin_cannot_lift_the_kill_switch():
    live_safety.set_kill_switch(True, "unprotected position")
    body = kite_ep.KillSwitchRequest(enabled=False, reason="all good now")

    with pytest.raises(HTTPException) as exc:
        await kite_ep.set_kill_switch(body, user=VIEWER)

    assert exc.value.status_code == 403
    assert live_safety.kill_switch_state()["enabled"] is True


@pytest.mark.asyncio
async def test_any_authenticated_user_may_engage_the_kill_switch_and_the_actor_is_recorded():
    body = kite_ep.KillSwitchRequest(enabled=True, reason="something is off")

    state = await kite_ep.set_kill_switch(body, user=VIEWER)

    assert state["enabled"] is True
    assert "viewer" in state["reason"] and "something is off" in state["reason"]


@pytest.mark.asyncio
async def test_lifting_needs_a_reason():
    live_safety.set_kill_switch(True, "halt")

    with pytest.raises(HTTPException) as exc:
        await kite_ep.set_kill_switch(kite_ep.KillSwitchRequest(enabled=False), user=ADMIN)

    assert exc.value.status_code == 400
    assert live_safety.kill_switch_state()["enabled"] is True


@pytest.mark.asyncio
async def test_lifting_is_refused_while_state_is_unresolved_unless_acknowledged(monkeypatch):
    from app.services import live_runtime

    unclean = live_runtime.CleanCheck(
        clean=False, blockers=("1 unresolved journal intent(s) for u1",), triggers=())
    monkeypatch.setattr(live_runtime, "provably_clean", lambda: unclean)
    live_safety.set_kill_switch(True, "halt")

    with pytest.raises(HTTPException) as exc:
        await kite_ep.set_kill_switch(
            kite_ep.KillSwitchRequest(enabled=False, reason="checked the broker"), user=ADMIN)
    assert exc.value.status_code == 409
    assert "unresolved journal intent" in exc.value.detail
    assert live_safety.kill_switch_state()["enabled"] is True

    state = await kite_ep.set_kill_switch(
        kite_ep.KillSwitchRequest(
            enabled=False, reason="checked the broker", acknowledge_investigation=True),
        user=ADMIN)
    assert state["enabled"] is False
    assert "boss" in state["reason"]


@pytest.mark.asyncio
async def test_an_admin_lifts_a_clean_system_with_a_reason(monkeypatch):
    from app.services import live_runtime

    monkeypatch.setattr(live_runtime, "provably_clean",
                        lambda: live_runtime.CleanCheck(clean=True))
    live_safety.set_kill_switch(True, "halt")

    state = await kite_ep.set_kill_switch(
        kite_ep.KillSwitchRequest(enabled=False, reason="drill finished"), user=ADMIN)

    assert state["enabled"] is False


@pytest.mark.asyncio
async def test_a_non_admin_cannot_disable_or_loosen_the_daily_loss_limit():
    for body in (
        kite_ep.DailyLossRequest(enabled=False, soft_warn_inr=-1000, hard_halt_inr=-1500),
        kite_ep.DailyLossRequest(enabled=True, soft_warn_inr=-50000, hard_halt_inr=-100000),
    ):
        with pytest.raises(HTTPException) as exc:
            await kite_ep.set_daily_loss(body, user=VIEWER)
        assert exc.value.status_code == 403
    assert live_safety.has_daily_loss_override("viewer") is False


@pytest.mark.asyncio
async def test_a_non_admin_may_tighten_the_daily_loss_limit():
    body = kite_ep.DailyLossRequest(enabled=True, soft_warn_inr=-400, hard_halt_inr=-800)

    payload = await kite_ep.set_daily_loss(body, user=VIEWER)

    assert payload["hard_halt_inr"] == -800
    assert payload["is_account_override"] is True


@pytest.mark.asyncio
async def test_clearing_a_tighter_override_back_to_a_looser_default_needs_an_admin():
    await kite_ep.set_daily_loss(
        kite_ep.DailyLossRequest(enabled=True, soft_warn_inr=-400, hard_halt_inr=-800),
        user=VIEWER)

    with pytest.raises(HTTPException) as exc:
        await kite_ep.clear_daily_loss(user=VIEWER)
    assert exc.value.status_code == 403
    assert live_safety.has_daily_loss_override("viewer") is True

    # An administrator acting on the same account may drop the override.
    owner_admin = UserContext(user_id="viewer", username="boss", role="admin")
    payload = await kite_ep.clear_daily_loss(user=owner_admin)
    assert payload["is_account_override"] is False


@pytest.mark.asyncio
async def test_an_admin_may_loosen_the_daily_loss_limit():
    body = kite_ep.DailyLossRequest(enabled=True, soft_warn_inr=-3000, hard_halt_inr=-5000)

    payload = await kite_ep.set_daily_loss(body, user=ADMIN)

    assert payload["hard_halt_inr"] == -5000
