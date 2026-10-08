"""Family controls are operator actions, not anonymous ones."""

from __future__ import annotations

import inspect

import pytest


def test_family_endpoints_require_an_admin_operator():
    from fastapi.params import Depends as DependsParam
    from app.api.v1.endpoints import snapback_ops
    from app.core.auth import require_admin

    for name in ("family_operations", "family_stop_new_trades", "family_resume_new_trades"):
        fn = getattr(snapback_ops, name)
        param = inspect.signature(fn).parameters["user"]
        assert isinstance(param.default, DependsParam)
        assert param.default.dependency is require_admin, f"{name} is not admin-guarded"


@pytest.mark.asyncio
async def test_non_admin_cannot_pass_global_operations_guard():
    from fastapi import HTTPException
    from app.core.auth import UserContext, require_admin

    with pytest.raises(HTTPException) as exc:
        await require_admin(UserContext(user_id="viewer", username="viewer", role="user"))
    assert exc.value.status_code == 403


def test_mutations_record_the_actor():
    from app.api.v1.endpoints import snapback_ops

    for name in ("family_stop_new_trades", "family_resume_new_trades"):
        source = inspect.getsource(getattr(snapback_ops, name))
        assert "actor" in source


def test_halt_state_records_who_changed_it(tmp_path, monkeypatch):
    import json

    import app.services.snapback_family_ops as fam

    monkeypatch.setenv("STERLING_NEW_TRADES_HALT_PATH", str(tmp_path / "halt.json"))

    fam.set_new_trades_halted(True, reason="family stop switch (actor=operator)")

    payload = json.loads((tmp_path / "halt.json").read_text(encoding="utf-8"))

    assert "operator" in payload["reason"]
    assert payload["changed_at"]
