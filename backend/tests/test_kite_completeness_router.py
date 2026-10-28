"""Router-level coverage for the completeness endpoints (session refresh,
auctions, holdings authorisation, MF order/SIP lifecycle + instruments, native
Alerts, and the order postback webhook)."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.services.exchanges.kite import accounts as ka
from app.services.exchanges.kite.client import KiteClient
from main import create_app
from tests.kite_paper_double import as_paper_double


def _mock_adapter():
    a = MagicMock()
    a.ping = AsyncMock(return_value=True)
    a.close = AsyncMock(return_value=None)
    return a


@pytest.fixture()
def client():
    from tests.feature_grants import grant_kite
    grant_kite("default", "alice", "bob")
    app = create_app()
    app.state.adapter = _mock_adapter()
    with TestClient(app) as c:
        yield c


def _add_account(client, paper=True):
    # The API creates LIVE accounts whatever ``is_paper`` says; ``paper=True`` then
    # turns the stored row into a simulated-broker test double (no network).
    r = client.post("/api/v1/kite/accounts", json={
        "label": "A", "api_key": "apikey123", "api_secret": "topsecret", "is_paper": paper,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    if paper:
        as_paper_double(body["id"])
        body["is_paper"] = True
    return body


# ─── Session refresh ─────────────────────────────────────────────────────────
def test_refresh_session_persists_new_token(client, monkeypatch):
    _add_account(client)

    async def fake_renew(self, refresh_token):
        assert refresh_token == "RTOK"
        self._access_token = "NEWTOK"
        return {"access_token": "NEWTOK", "user_id": "ZID1"}
    monkeypatch.setattr(KiteClient, "renew_access_token", fake_renew)

    r = client.post("/api/v1/kite/session/refresh", json={"refresh_token": "RTOK"})
    assert r.status_code == 200, r.text
    assert r.json()["connected"] is True
    acc = ka.get_active("default")
    assert acc.access_token == "NEWTOK"


def test_refresh_session_uses_stored_refresh_token(client, monkeypatch):
    acc = _add_account(client)
    ka.save_session("default", acc["id"], access_token="OLD", refresh_token="STORED-RTOK")

    seen = {}

    async def fake_renew(self, refresh_token):
        seen["token"] = refresh_token
        self._access_token = "NEWTOK"
        return {"access_token": "NEWTOK"}
    monkeypatch.setattr(KiteClient, "renew_access_token", fake_renew)

    # no refresh_token in the body → falls back to the one captured at login
    r = client.post("/api/v1/kite/session/refresh", json={})
    assert r.status_code == 200, r.text
    assert seen["token"] == "STORED-RTOK"


def test_refresh_session_without_token_or_store_is_400(client):
    _add_account(client)  # never logged in → no stored refresh_token
    r = client.post("/api/v1/kite/session/refresh", json={})
    assert r.status_code == 400


# ─── Auctions + holdings authorisation ───────────────────────────────────────
def test_auctions_empty_without_session(client):
    _add_account(client, paper=True)
    r = client.get("/api/v1/kite/auctions")
    assert r.status_code == 200
    assert r.json() == []


def test_initiate_holdings_authorise_returns_redirect_url(client, monkeypatch):
    _add_account(client, paper=True)

    async def fake_auth(self, instruments=None):
        return {"request_id": "REQ123"}
    monkeypatch.setattr(KiteClient, "initiate_holdings_auth", fake_auth)

    r = client.post("/api/v1/kite/holdings/authorise", json={"instruments": []})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["request_id"] == "REQ123"
    # the UI needs a ready-to-open consent URL with api_key + request_id
    assert "REQ123" in body["authorise_url"]
    assert "apikey123" in body["authorise_url"]


# ─── Mutual funds ────────────────────────────────────────────────────────────
def test_mf_order_detail(client, monkeypatch):
    _add_account(client, paper=True)

    async def fake(self, order_id):
        return {"order_id": order_id, "status": "COMPLETE"}
    monkeypatch.setattr(KiteClient, "get_mf_order", fake)

    r = client.get("/api/v1/kite/mf/orders/MF1")
    assert r.status_code == 200
    assert r.json()["status"] == "COMPLETE"


def test_place_mf_order_accepts_json_in_paper_mode(client):
    _add_account(client, paper=True)
    r = client.post("/api/v1/kite/mf/orders", json={
        "tradingsymbol": "INF209K01XI3",
        "transaction_type": "BUY",
        "amount": 1000,
    })
    assert r.status_code == 200, r.text
    assert r.json()["order_id"].startswith("PAPER-MF-")


def test_live_mf_buy_is_refused_before_transport(client, monkeypatch):
    _add_account(client, paper=False)
    monkeypatch.setenv("STERLING_FAMILY_MODE", "true")

    called = {"place": 0}

    async def fake_place(self, **kwargs):
        called["place"] += 1
        raise AssertionError("live MF BUY reached transport")

    monkeypatch.setattr(KiteClient, "place_mf_order", fake_place)
    r = client.post("/api/v1/kite/mf/orders", json={
        "tradingsymbol": "INF209K01XI3",
        "transaction_type": "BUY",
        "amount": 1000,
    })
    assert r.status_code == 423, r.text
    assert called["place"] == 0


def test_live_mf_redemption_carries_reduce_capability(client, monkeypatch):
    _add_account(client, paper=False)
    monkeypatch.setenv("STERLING_FAMILY_MODE", "true")

    async def holdings(self):
        return [{"tradingsymbol": "INF209K01XI3", "quantity": 2}]

    monkeypatch.setattr(KiteClient, "get_mf_holdings", holdings)

    async def guarded_redeem(self, **kwargs):
        from app.services.snapback_family_mode import guard_broker_write
        guard_broker_write("exit_order")
        assert kwargs["transaction_type"] == "SELL"
        return {"order_id": "MF-SELL-1"}

    async def held_units(self):
        # A live redemption must be provably covered by fresh holdings, so the
        # test account has to actually hold what it redeems.
        return [{"tradingsymbol": "INF209K01XI3", "quantity": 10.0}]

    monkeypatch.setattr(KiteClient, "place_mf_order", guarded_redeem)
    monkeypatch.setattr(KiteClient, "get_mf_holdings", held_units)
    r = client.post("/api/v1/kite/mf/orders", json={
        "tradingsymbol": "INF209K01XI3",
        "transaction_type": "SELL",
        "quantity": 2,
    })
    assert r.status_code == 200, r.text
    assert r.json()["order_id"] == "MF-SELL-1"


def test_live_mf_buy_cancellation_requires_verified_buy_identity(client, monkeypatch):
    _add_account(client, paper=False)
    monkeypatch.setenv("STERLING_FAMILY_MODE", "true")

    async def detail(self, order_id):
        return {"order_id": order_id, "transaction_type": "BUY", "status": "OPEN"}

    async def guarded_cancel(self, order_id):
        from app.services.snapback_family_mode import guard_broker_write
        guard_broker_write("cancel_mf_order")
        return {"order_id": order_id}

    monkeypatch.setattr(KiteClient, "get_mf_order", detail)
    monkeypatch.setattr(KiteClient, "cancel_mf_order", guarded_cancel)

    r = client.delete("/api/v1/kite/mf/orders/MF-BUY-1")
    assert r.status_code == 200, r.text
    assert r.json()["order_id"] == "MF-BUY-1"


def test_live_mf_sell_cancellation_is_refused(client, monkeypatch):
    _add_account(client, paper=False)
    monkeypatch.setenv("STERLING_FAMILY_MODE", "true")

    async def detail(self, order_id):
        return {"order_id": order_id, "transaction_type": "SELL", "status": "OPEN"}

    async def must_not_cancel(self, order_id):
        raise AssertionError("risk-reducing MF SELL was cancelled")

    monkeypatch.setattr(KiteClient, "get_mf_order", detail)
    monkeypatch.setattr(KiteClient, "cancel_mf_order", must_not_cancel)

    r = client.delete("/api/v1/kite/mf/orders/MF-SELL-1")
    assert r.status_code == 423, r.text


def test_live_mf_sip_cancel_carries_cancel_capability(client, monkeypatch):
    _add_account(client, paper=False)
    monkeypatch.setenv("STERLING_FAMILY_MODE", "true")

    async def guarded_cancel(self, sip_id):
        from app.services.snapback_family_mode import guard_broker_write
        guard_broker_write("cancel_mf_sip")
        return {"sip_id": sip_id}

    monkeypatch.setattr(KiteClient, "cancel_mf_sip", guarded_cancel)
    r = client.delete("/api/v1/kite/mf/sips/SIP-LIVE-1")
    assert r.status_code == 200, r.text
    assert r.json()["sip_id"] == "SIP-LIVE-1"


def test_place_mf_sip_paper(client):
    _add_account(client, paper=True)
    r = client.post("/api/v1/kite/mf/sips", json={
        "tradingsymbol": "INF209K01XI3", "amount": 1000, "instalments": 12, "frequency": "monthly",
    })
    assert r.status_code == 200, r.text
    assert r.json()["sip_id"].startswith("PAPER-SIP-")


def test_cancel_mf_sip_paper(client):
    _add_account(client, paper=True)
    r = client.delete("/api/v1/kite/mf/sips/SIP1")
    assert r.status_code == 200
    assert r.json()["sip_id"] == "SIP1"


def test_modify_mf_sip_paper(client):
    _add_account(client, paper=True)
    r = client.put("/api/v1/kite/mf/sips/SIP1", json={"amount": 2000, "status": "paused"})
    assert r.status_code == 200
    assert r.json()["sip_id"] == "SIP1"


def test_mf_instruments_search(client, monkeypatch):
    _add_account(client, paper=True)
    csv = ("tradingsymbol,amc,name,scheme_type,plan,last_price\n"
           "INF209K01XI3,Aditya Birla,ABSL Frontline Equity,Equity,Direct,350.5\n")

    async def fake_fetch(self, exchange=""):
        return csv
    monkeypatch.setattr(KiteClient, "_fetch_mf_instruments_csv", fake_fetch)

    r = client.get("/api/v1/kite/mf/instruments", params={"query": "frontline"})
    assert r.status_code == 200
    assert r.json()["instruments"][0]["tradingsymbol"] == "INF209K01XI3"


# ─── Alerts ──────────────────────────────────────────────────────────────────
def test_alerts_empty_without_session(client):
    _add_account(client, paper=True)
    r = client.get("/api/v1/kite/alerts")
    assert r.status_code == 200
    assert r.json() == []


def test_create_alert(client, monkeypatch):
    _add_account(client, paper=True)

    async def fake(self, **fields):
        return {"uuid": "u1", **fields}
    monkeypatch.setattr(KiteClient, "create_alert", fake)

    r = client.post("/api/v1/kite/alerts", json={
        "name": "INFY above 1500", "lhs_exchange": "NSE", "lhs_tradingsymbol": "INFY",
        "lhs_attribute": "LastTradedPrice", "operator": ">=", "rhs_constant": 1500,
    })
    assert r.status_code == 200, r.text
    assert r.json()["uuid"] == "u1"


def test_modify_and_delete_alerts(client, monkeypatch):
    _add_account(client, paper=True)

    async def fake_modify(self, uuid, **fields):
        return {"uuid": uuid, **fields}

    async def fake_delete(self, uuids):
        return {"deleted": list(uuids)}
    monkeypatch.setattr(KiteClient, "modify_alert", fake_modify)
    monkeypatch.setattr(KiteClient, "delete_alerts", fake_delete)

    r = client.put("/api/v1/kite/alerts/u1", json={"operator": "<=", "rhs_constant": 1400})
    assert r.status_code == 200
    assert r.json()["uuid"] == "u1"

    r = client.request("DELETE", "/api/v1/kite/alerts", json={"uuids": ["u1", "u2"]})
    assert r.status_code == 200
    assert r.json()["deleted"] == ["u1", "u2"]


# ─── Postback webhook ────────────────────────────────────────────────────────
def test_postback_routes_to_account_by_kite_user_id(client):
    import hashlib
    acc = _add_account(client, paper=True)
    ka.save_session("default", acc["id"], access_token="ATOK", kite_user_id="ZID1")

    r = client.post("/api/v1/kite/postback", json={
        "user_id": "ZID1", "order_id": "O1", "status": "COMPLETE",
        "order_timestamp": "2026-09-07 10:00:00",
        "checksum": hashlib.sha256(b"O12026-09-07 10:00:00topsecret").hexdigest(),
    })
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert r.json()["routed"] is True


def test_signed_postback_processing_failure_is_not_reported_as_routed(client, monkeypatch):
    import hashlib

    from app.services.exchanges.kite import ticker_manager

    acc = _add_account(client, paper=True)
    ka.save_session("default", acc["id"], access_token="ATOK", kite_user_id="ZID1")
    seen = {}

    async def fail_processing(user_id, order, *, client=None, strict_monitor=False):
        seen["user_id"] = user_id
        seen["strict_monitor"] = strict_monitor
        raise RuntimeError("position projection failed")

    monkeypatch.setattr(ticker_manager, "broadcast_order_update", fail_processing)

    r = client.post("/api/v1/kite/postback", json={
        "user_id": "ZID1", "order_id": "O1", "status": "COMPLETE",
        "order_timestamp": "2026-09-07 10:00:00",
        "checksum": hashlib.sha256(b"O12026-09-07 10:00:00topsecret").hexdigest(),
    })

    assert r.status_code == 200
    assert r.json() == {
        "ok": True,
        "routed": False,
        "reason": "processing failed",
    }
    assert seen == {"user_id": "default", "strict_monitor": True}


@pytest.mark.parametrize("checksum", [None, "", "invalid", 42])
def test_unsigned_or_invalid_postback_cannot_mutate_positions(client, checksum):
    acc = _add_account(client, paper=True)
    ka.save_session("default", acc["id"], access_token="ATOK", kite_user_id="ZID1")
    r = client.post("/api/v1/kite/postback", json={
        "user_id": "ZID1", "order_id": "O1", "status": "COMPLETE",
        "order_timestamp": "2026-09-07 10:00:00", "checksum": checksum})
    assert r.status_code == 200 and r.json()["routed"] is False


def test_postback_unknown_user_is_accepted_but_not_routed(client):
    r = client.post("/api/v1/kite/postback", json={"user_id": "NOBODY", "order_id": "O1"})
    assert r.status_code == 200
    assert r.json()["routed"] is False
