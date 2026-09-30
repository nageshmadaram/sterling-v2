"""Kite session identity is immutable once a Sterling account is broker-bound."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.exchanges.kite import accounts, auth
from app.services.exchanges.kite.models import KiteAccountCreate


def _live_account():
    acct = accounts.add(
        "u1",
        KiteAccountCreate(
            label="Live",
            api_key="key",
            api_secret="secret",
            is_paper=False,
        ),
    )
    accounts.save_session(
        "u1",
        acct.id,
        access_token="ACCESS-1",
        refresh_token="REFRESH-1",
        kite_user_id="ZID1",
    )
    return acct


@pytest.mark.asyncio
async def test_profile_for_different_kite_user_quarantines_session(monkeypatch):
    acct = _live_account()

    class Client:
        async def get_profile(self):
            return {"user_id": "ZID2", "user_name": "Wrong Trader"}

        async def close(self):
            return None

    client = Client()
    monkeypatch.setattr(
        accounts,
        "acquire_client",
        AsyncMock(return_value=client),
    )

    health = await auth.ensure_session("u1", acct, force_validate=True)

    assert health.connected is False
    assert "identity" in health.message.lower()
    stored = accounts.get("u1", acct.id)
    assert stored.connected is False
    assert stored.has_refresh_token is False
    assert stored.kite_user_id == "ZID1"
    assert accounts.validated_age_ms(acct.id) is None


@pytest.mark.asyncio
async def test_renewal_for_different_kite_user_is_rejected_and_quarantined(
    monkeypatch,
):
    acct = _live_account()

    class Client:
        async def renew_access_token(self, refresh_token):
            assert refresh_token == "REFRESH-1"
            return {
                "access_token": "ACCESS-2",
                "refresh_token": "REFRESH-2",
                "user_id": "ZID2",
            }

        async def close(self):
            return None

    monkeypatch.setattr(accounts, "build_client", lambda _acct: Client())
    monkeypatch.setattr(accounts, "release_client", AsyncMock(return_value=None))

    data = await auth.renew("u1", acct)

    assert data is None
    stored = accounts.get("u1", acct.id)
    assert stored.connected is False
    assert stored.has_refresh_token is False
    assert stored.kite_user_id == "ZID1"
