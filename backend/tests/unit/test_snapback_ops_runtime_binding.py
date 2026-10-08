from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.v1.endpoints.snapback_ops import ops_runtime


def _request():
    return SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))


@pytest.mark.asyncio
async def test_ops_runtime_selects_bound_account_not_first_connected(monkeypatch):
    bound = SimpleNamespace(client_id="KITE-BOUND")
    other_account = SimpleNamespace(
        kite_user_id="KITE-OTHER",
        id="other-id",
        user_id="other-user",
        connected=True,
        is_paper=False,
    )
    bound_account = SimpleNamespace(
        kite_user_id="KITE-BOUND",
        id="bound-id",
        user_id="bound-user",
        connected=True,
        is_paper=False,
    )

    monkeypatch.setattr(
        "app.services.account_binding_service.active_binding",
        lambda: bound,
    )
    monkeypatch.setattr(
        "app.services.exchanges.kite.accounts.all_accounts",
        lambda: [other_account, bound_account],
    )

    asked = []

    def status(uid):
        asked.append(uid)
        return {
            "connected": uid == "bound-user",
            "subscribed": [101] if uid == "bound-user" else [999],
            "last_tick_ms": 123 if uid == "bound-user" else 999999,
        }

    monkeypatch.setattr("app.services.exchanges.kite.ticker_manager.status", status)

    body = await ops_runtime(_request())

    assert body["broker"]["account_id"] == "KITE-BOUND"
    assert body["broker"]["binding_matches"] is True
    assert body["broker"]["connected"] is True
    assert asked == ["bound-user"]
    assert body["feed"]["subscribed"] == [101]
    assert body["feed"]["last_tick_ms"] == 123


@pytest.mark.asyncio
async def test_other_users_fresh_feed_cannot_satisfy_bound_account(monkeypatch):
    bound = SimpleNamespace(client_id="KITE-BOUND")
    other_account = SimpleNamespace(
        kite_user_id="KITE-OTHER",
        id="other-id",
        user_id="other-user",
        connected=True,
        is_paper=False,
    )
    bound_account = SimpleNamespace(
        kite_user_id="KITE-BOUND",
        id="bound-id",
        user_id="bound-user",
        connected=True,
        is_paper=False,
    )

    monkeypatch.setattr(
        "app.services.account_binding_service.active_binding",
        lambda: bound,
    )
    monkeypatch.setattr(
        "app.services.exchanges.kite.accounts.all_accounts",
        lambda: [other_account, bound_account],
    )

    def status(uid):
        if uid == "other-user":
            return {"connected": True, "subscribed": [999], "last_tick_ms": 999999}
        return {"connected": False, "subscribed": [], "last_tick_ms": 0}

    monkeypatch.setattr("app.services.exchanges.kite.ticker_manager.status", status)

    body = await ops_runtime(_request())

    assert body["broker"]["connected"] is True
    assert body["feed"]["connected"] is False
    assert body["feed"]["subscribed"] == []
    assert body["feed"]["last_tick_ms"] == 0


@pytest.mark.asyncio
async def test_paper_account_cannot_satisfy_live_binding(monkeypatch):
    bound = SimpleNamespace(client_id="KITE-BOUND")
    paper_account = SimpleNamespace(
        kite_user_id="KITE-BOUND",
        id="paper-id",
        user_id="bound-user",
        connected=True,
        is_paper=True,
    )

    monkeypatch.setattr(
        "app.services.account_binding_service.active_binding",
        lambda: bound,
    )
    monkeypatch.setattr(
        "app.services.exchanges.kite.accounts.all_accounts",
        lambda: [paper_account],
    )
    monkeypatch.setattr(
        "app.services.exchanges.kite.ticker_manager.status",
        lambda _uid: {"connected": True, "subscribed": [101], "last_tick_ms": 123},
    )

    body = await ops_runtime(_request())

    assert body["broker"]["binding_matches"] is True
    assert body["broker"]["connected"] is False
    assert "not an explicit live account" in body["broker"]["detail"]
