"""Kite router: multi-tenant CRUD, routing guard, safety gate, paper happy paths."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.services import live_safety
from app.services.exchanges.kite.client import KiteClient
from app.services.exchanges.kite import accounts as kite_accounts
from main import create_app


def _mock_adapter():
    a = MagicMock()
    a.ping = AsyncMock(return_value=True)
    a.close = AsyncMock(return_value=None)
    return a


@pytest.fixture()
def client():
    app = create_app()
    app.state.adapter = _mock_adapter()
    with TestClient(app) as c:
        kite_accounts.clear()
        c.app.state.adapter = app.state.adapter
        try:
            yield c
        finally:
            kite_accounts.clear()


@pytest.fixture(autouse=True)
def _reset_safety():
    live_safety.set_kill_switch(False)
    live_safety._IDEMPOTENCY_CACHE.clear()
    live_safety.clear_retries()
    yield
    live_safety._IDEMPOTENCY_CACHE.clear()
    live_safety.clear_retries()
    live_safety.set_kill_switch(False)


def _add_account(client, headers=None, label="A", paper=True):
    r = client.post("/api/v1/kite/accounts", headers=headers or {}, json={
        "label": label, "api_key": "apikey123", "api_secret": "topsecret", "is_paper": paper,
    })
    assert r.status_code == 200, r.text
    return r.json()


# ─── Account CRUD + multi-tenancy ────────────────────────────────────────────
def test_add_account_redacts_secret_and_auto_activates(client):
    acc = _add_account(client)
    assert acc["is_active"] is True
    assert acc["api_key_hint"] == "****y123"
    assert "topsecret" not in str(acc)


def test_user_isolation_via_header(client):
    _add_account(client, headers={"X-User-Id": "alice"})
    bob = client.get("/api/v1/kite/accounts", headers={"X-User-Id": "bob"}).json()
    assert bob["count"] == 0
    alice = client.get("/api/v1/kite/accounts", headers={"X-User-Id": "alice"}).json()
    assert alice["count"] == 1


def test_update_and_delete_account(client):
    acc = _add_account(client)
    up = client.put(f"/api/v1/kite/accounts/{acc['id']}", json={"label": "renamed"})
    assert up.json()["label"] == "renamed"
    d = client.delete(f"/api/v1/kite/accounts/{acc['id']}")
    assert d.status_code == 204
    assert client.get("/api/v1/kite/accounts").json()["count"] == 0


# ─── Routing guard ───────────────────────────────────────────────────────────
def test_holdings_requires_active_account(client):
    r = client.get("/api/v1/kite/holdings")
    assert r.status_code == 409


def test_login_url_after_account_added(client):
    _add_account(client)
    r = client.get("/api/v1/kite/login-url")
    assert r.status_code == 200
    assert "api_key=apikey123" in r.json()["login_url"]


def test_status_reports_not_logged_in(client):
    _add_account(client)
    s = client.get("/api/v1/kite/status").json()
    assert s["connected"] is False
    assert "login" in s["message"].lower()


# ─── Orders / safety ─────────────────────────────────────────────────────────
def test_place_order_paper_happy_path(client):
    _add_account(client, paper=True)
    r = client.post("/api/v1/kite/orders", json={
        "tradingsymbol": "INFY", "exchange": "NSE", "transaction_type": "BUY",
        "quantity": 1, "order_type": "MARKET", "product": "CNC",
    })
    assert r.status_code == 200, r.text
    assert r.json()["order_id"].startswith("PAPER-")


def test_live_noncanonical_order_fallback_is_refused(client):
    """A live cash order cannot bypass canonical execution via KiteClient._place."""
    _add_account(client, paper=False)
    r = client.post("/api/v1/kite/orders", json={
        "tradingsymbol": "INFY", "exchange": "NSE", "transaction_type": "BUY",
        "quantity": 1, "order_type": "MARKET", "product": "CNC",
    })
    assert r.status_code == 423
    assert "canonical strategy path" in str(r.json()["detail"])


def test_paper_idempotency_cannot_mask_later_live_order_refusal(client):
    """Idempotency is scoped to execution identity, not just order shape.

    Reusing the paper order id after switching to a live account would bypass
    the live canonical-routing guard without calling the broker at all.
    """
    paper = _add_account(client, label="paper", paper=True)
    body = {
        "tradingsymbol": "INFY", "exchange": "NSE", "transaction_type": "BUY",
        "quantity": 1, "order_type": "MARKET", "product": "CNC",
    }
    paper_order = client.post("/api/v1/kite/orders", json=body)
    assert paper_order.status_code == 200
    assert paper_order.json()["order_id"].startswith("PAPER-")

    live = _add_account(client, label="live", paper=False)
    activate = client.post(f"/api/v1/kite/accounts/{live['id']}/activate")
    assert activate.status_code == 200
    assert activate.json()["id"] == live["id"]
    assert live["id"] != paper["id"]

    live_order = client.post("/api/v1/kite/orders", json=body)
    assert live_order.status_code == 423
    assert "canonical strategy path" in str(live_order.json()["detail"])
    assert live_order.json().get("deduplicated") is not True


def test_live_generic_order_modify_is_refused(client):
    _add_account(client, paper=False)
    r = client.put("/api/v1/kite/orders/OID-1", json={"quantity": 2})
    assert r.status_code == 423
    assert "generic order modification" in str(r.json()["detail"])


def test_live_untracked_order_cancellation_is_refused(client):
    _add_account(client, paper=False)
    r = client.delete("/api/v1/kite/orders/UNTRACKED-1")
    assert r.status_code == 423
    assert "canonical ENTRY orders" in str(r.json()["detail"])


def test_live_generic_gtt_deletion_is_refused(client):
    _add_account(client, paper=False)
    r = client.delete("/api/v1/kite/gtt/12345")
    assert r.status_code == 423
    assert "generic GTT deletion" in str(r.json()["detail"])


def test_live_generic_gtt_creation_is_refused(client):
    _add_account(client, paper=False)
    r = client.post("/api/v1/kite/gtt", json={
        "trigger_type": "single",
        "tradingsymbol": "INFY",
        "exchange": "NSE",
        "last_price": 1500.0,
        "trigger_values": [1510.0],
        "orders": [{
            "tradingsymbol": "INFY",
            "exchange": "NSE",
            "transaction_type": "BUY",
            "quantity": 1,
            "order_type": "LIMIT",
            "product": "CNC",
            "price": 1510.0,
        }],
    })
    assert r.status_code == 423
    assert "generic GTT creation" in str(r.json()["detail"])


def test_live_position_conversion_is_refused(client):
    _add_account(client, paper=False)
    r = client.put("/api/v1/kite/positions/convert", json={
        "tradingsymbol": "INFY",
        "exchange": "NSE",
        "transaction_type": "BUY",
        "position_type": "day",
        "quantity": 1,
        "old_product": "MIS",
        "new_product": "CNC",
    })
    assert r.status_code == 423
    assert "position conversion" in str(r.json()["detail"])


def test_live_mutual_fund_purchase_is_refused(client):
    _add_account(client, paper=False)
    r = client.post(
        "/api/v1/kite/mf/orders",
        json={
            "tradingsymbol": "INF209K01XI3",
            "transaction_type": "BUY",
            "amount": 1000,
        },
    )
    assert r.status_code == 423
    assert "mutual-fund purchase" in str(r.json()["detail"])


def test_live_mutual_fund_amount_redemption_is_refused(client):
    _add_account(client, paper=False)
    r = client.post(
        "/api/v1/kite/mf/orders",
        json={
            "tradingsymbol": "INF209K01XI3",
            "transaction_type": "SELL",
            "amount": 1000,
        },
    )
    assert r.status_code == 423
    assert "requires a positive quantity" in str(r.json()["detail"])


def test_live_mutual_fund_redemption_cannot_exceed_broker_holding(client, monkeypatch):
    _add_account(client, paper=False)

    async def holdings(self):
        return [{"tradingsymbol": "INF209K01XI3", "quantity": 10.0}]

    async def must_not_sell(self, **_kwargs):
        raise AssertionError("redemption must not exceed verified broker units")

    monkeypatch.setattr(KiteClient, "get_mf_holdings", holdings)
    monkeypatch.setattr(KiteClient, "place_mf_order", must_not_sell)

    r = client.post(
        "/api/v1/kite/mf/orders",
        json={
            "tradingsymbol": "INF209K01XI3",
            "transaction_type": "SELL",
            "quantity": 11,
        },
    )

    assert r.status_code == 423
    assert "broker holds 10.0" in str(r.json()["detail"])


def test_live_mutual_fund_redemption_is_bound_to_holding_and_reduce_authority(
    client, monkeypatch
):
    from app.services.snapback_family_mode import INTENT_REDUCE, current_intent

    _add_account(client, paper=False)
    seen = {}

    async def holdings(self):
        return [{"tradingsymbol": "INF209K01XI3", "quantity": 10.0}]

    async def redeem(self, **kwargs):
        seen.update(kwargs)
        seen["intent"] = current_intent()
        return {"order_id": "MF-SELL-1"}

    monkeypatch.setattr(KiteClient, "get_mf_holdings", holdings)
    monkeypatch.setattr(KiteClient, "place_mf_order", redeem)

    r = client.post(
        "/api/v1/kite/mf/orders",
        json={
            "tradingsymbol": "INF209K01XI3",
            "transaction_type": "SELL",
            "quantity": 7,
        },
    )

    assert r.status_code == 200, r.text
    assert r.json()["order_id"] == "MF-SELL-1"
    assert seen["quantity"] == 7.0
    assert seen["amount"] is None
    assert seen["intent"] == INTENT_REDUCE


def test_live_mutual_fund_sip_creation_is_refused(client):
    _add_account(client, paper=False)
    r = client.post("/api/v1/kite/mf/sips", json={
        "tradingsymbol": "INF209K01XI3",
        "amount": 1000,
        "instalments": 12,
        "frequency": "monthly",
    })
    assert r.status_code == 423
    assert "SIP creation" in str(r.json()["detail"])


def test_live_ato_alert_creation_is_refused(client):
    _add_account(client, paper=False)
    r = client.post("/api/v1/kite/alerts", json={
        "name": "auto buy",
        "lhs_exchange": "NSE",
        "lhs_tradingsymbol": "INFY",
        "lhs_attribute": "LastTradedPrice",
        "operator": "<=",
        "rhs_constant": 1400,
        "alert_type": "ato",
        "basket": [{
            "exchange": "NSE",
            "tradingsymbol": "INFY",
            "transaction_type": "BUY",
            "quantity": 1,
            "product": "CNC",
            "order_type": "MARKET",
        }],
    })
    assert r.status_code == 423
    assert "ATO alert creation" in str(r.json()["detail"])


def test_live_ato_alert_cannot_be_rearmed_or_changed(client, monkeypatch):
    _add_account(client, paper=False)

    async def existing_ato(self, uuid):
        return {"uuid": uuid, "type": "ato"}

    async def must_not_modify(self, uuid, **fields):
        raise AssertionError("live ATO modification reached broker transport")

    monkeypatch.setattr(KiteClient, "get_alert", existing_ato)
    monkeypatch.setattr(KiteClient, "modify_alert", must_not_modify)

    r = client.put("/api/v1/kite/alerts/A1", json={"status": "enabled"})
    assert r.status_code == 423
    assert "ATO alert modification" in str(r.json()["detail"])


def test_live_ato_alert_can_be_disabled(client, monkeypatch):
    _add_account(client, paper=False)
    seen = {}

    async def existing_ato(self, uuid):
        return {"uuid": uuid, "type": "ato"}

    async def disable(self, uuid, **fields):
        seen.update(fields)
        return {"uuid": uuid, **fields}

    monkeypatch.setattr(KiteClient, "get_alert", existing_ato)
    monkeypatch.setattr(KiteClient, "modify_alert", disable)

    r = client.put("/api/v1/kite/alerts/A1", json={"status": "disabled"})
    assert r.status_code == 200
    assert seen == {"status": "disabled"}


def test_place_order_blocked_by_kill_switch(client):
    _add_account(client, paper=True)
    live_safety.set_kill_switch(True, reason="test halt")
    r = client.post("/api/v1/kite/orders", json={
        "tradingsymbol": "INFY", "exchange": "NSE", "transaction_type": "BUY",
        "quantity": 1, "order_type": "MARKET", "product": "CNC",
    })
    assert r.status_code == 423
    assert r.json()["detail"]["code"] == "kill_switch"


def test_place_order_blocked_by_daily_loss(client, monkeypatch):
    _add_account(client, paper=True)
    cfg = live_safety.DailyLossConfig(enabled=True, soft_warn_inr=-500.0, hard_halt_inr=-1000.0)
    live_safety.configure_daily_loss(cfg, uid="default")
    from app.services.kite_engine import state
    monkeypatch.setattr(state, "daily_realized_pnl_strict", lambda uid: -1500.0)
    r = client.post("/api/v1/kite/orders", json={
        "tradingsymbol": "INFY", "exchange": "NSE", "transaction_type": "BUY",
        "quantity": 1, "order_type": "MARKET", "product": "CNC",
    })
    assert r.status_code == 423
    assert r.json()["detail"]["code"] == "daily_loss_halt"
    live_safety.clear_daily_loss("default")


def test_get_and_clear_safety_retries(client):
    live_safety.clear_retries()
    item = live_safety.enqueue_retry({"symbol": "NIFTY26AUG24500CE"}, "rate_limited")
    r = client.get("/api/v1/kite/risk/retries")
    assert r.status_code == 200
    retries = r.json()
    assert len(retries) == 1
    assert retries[0]["id"] == item.id
    assert retries[0]["last_error"] == "rate_limited"

    r_del = client.delete("/api/v1/kite/risk/retries")
    assert r_del.status_code == 200
    assert r_del.json()["cleared"] is True
    assert client.get("/api/v1/kite/risk/retries").json() == []


def test_paper_holdings_and_positions_empty(client):
    _add_account(client, paper=True)
    assert client.get("/api/v1/kite/holdings").json() == []
    assert client.get("/api/v1/kite/positions").json() == {"net": [], "day": []}


def test_convert_position_paper(client):
    _add_account(client, paper=True)
    r = client.put("/api/v1/kite/positions/convert", json={
        "tradingsymbol": "INFY", "exchange": "NSE", "transaction_type": "BUY",
        "position_type": "day", "quantity": 1, "old_product": "MIS", "new_product": "CNC",
    })
    assert r.status_code == 200  # paper → simulated convert


def test_instruments_search(client, monkeypatch):
    _add_account(client, paper=True)
    csv = ("instrument_token,tradingsymbol,name,segment,exchange\n"
           "408065,INFY,INFOSYS,NSE,NSE\n")

    async def fake_fetch(self, exchange):
        return csv
    monkeypatch.setattr(KiteClient, "_fetch_instruments_csv", fake_fetch)
    r = client.get("/api/v1/kite/instruments", params={"exchange": "NSE", "query": "INFY"})
    assert r.status_code == 200
    assert r.json()["instruments"][0]["tradingsymbol"] == "INFY"


def test_ticker_subscribe_without_live_session(client):
    _add_account(client, paper=True)
    r = client.post("/api/v1/kite/ticker/subscribe", json={"instrument_tokens": [408065], "mode": "ltp"})
    assert r.status_code == 200
    assert r.json()["ok"] is False  # paper account → no live ticker


def test_callback_success_connects_active_account(client, monkeypatch):
    _add_account(client, paper=True)

    async def fake_gen(self, request_token):
        return {"access_token": "ATOK", "user_id": "ZID1", "user_name": "Trader"}

    async def fake_profile(self):
        return {"user_id": "ZID1", "user_name": "Trader"}
    monkeypatch.setattr(KiteClient, "generate_session", fake_gen)
    monkeypatch.setattr(KiteClient, "get_profile", fake_profile)  # /status now validates the token

    r = client.get("/api/v1/kite/callback?request_token=rt&action=login&status=success")
    assert r.status_code == 200
    assert "Connected" in r.text and "Trader" in r.text
    s = client.get("/api/v1/kite/status").json()
    assert s["connected"] is True and s["user_name"] == "Trader"


def test_callback_missing_token_renders_error(client):
    _add_account(client, paper=True)
    r = client.get("/api/v1/kite/callback?status=success")
    assert r.status_code == 400
    assert "Incomplete redirect" in r.text
    # The error page must NOT dismiss itself — the reason has to stay readable.
    assert "setInterval" not in r.text


def test_callback_no_active_account(client):
    r = client.get("/api/v1/kite/callback?request_token=rt&status=success")
    assert r.status_code == 400
    assert "No active Kite account" in r.text


def test_callback_status_not_success(client):
    _add_account(client, paper=True)
    r = client.get("/api/v1/kite/callback?status=cancelled")
    assert r.status_code == 400
    assert "Login was not completed" in r.text
    assert "setInterval" not in r.text


def test_watchlist_sync_aggregates_account_instruments(client, monkeypatch):
    _add_account(client, paper=True)

    async def fake_holdings(self):
        return [{"exchange": "NSE", "tradingsymbol": "INFY", "instrument_token": 408065}]

    async def fake_positions(self):
        return {"net": [{"exchange": "NFO", "tradingsymbol": "NIFTY25JAN25000CE",
                         "instrument_token": 111, "quantity": 50}]}

    async def fake_gtts(self):
        return [{"condition": {"exchange": "NSE", "tradingsymbol": "TCS", "instrument_token": 222}}]

    monkeypatch.setattr(KiteClient, "get_holdings", fake_holdings)
    monkeypatch.setattr(KiteClient, "get_positions_raw", fake_positions)
    monkeypatch.setattr(KiteClient, "get_gtts", fake_gtts)

    r = client.get("/api/v1/kite/watchlist/sync")
    assert r.status_code == 200
    d = r.json()
    assert d["count"] == 3
    syms = {i["symbol"] for i in d["items"]}
    assert syms == {"NSE:INFY", "NFO:NIFTY25JAN25000CE", "NSE:TCS"}
    assert d["sources"] == {"holding": 1, "position": 1, "gtt": 1}


def test_watchlist_sync_requires_active_account(client):
    r = client.get("/api/v1/kite/watchlist/sync")
    assert r.status_code == 409


def test_trades_endpoint_empty_without_session(client):
    _add_account(client, paper=True)  # no access_token yet
    r = client.get("/api/v1/kite/trades")
    assert r.status_code == 200
    assert r.json() == []


def test_status_session_expired_when_token_invalid(client, monkeypatch):
    _add_account(client, paper=True)
    # simulate a connected account whose token Kite rejects
    from app.services.exchanges.kite import accounts as ka
    acc = ka.get_active("default")
    ka.save_session("default", acc.id, access_token="DEADTOKEN")
    # save_session records the token as just-issued-by-Kite, and /status trusts
    # that for auth.VALIDATION_TTL_MS instead of re-asking on every poll. Drop the
    # proof to reach the state the network check actually runs in: TTL elapsed, or
    # a restarted process that has proven nothing yet.
    ka.forget_validation(acc.id)

    from app.services.exchanges.kite.errors import KiteTokenError

    async def fake_profile(self):
        raise KiteTokenError("Incorrect api_key or access_token.", error_type="TokenException")
    monkeypatch.setattr(KiteClient, "get_profile", fake_profile)

    s = client.get("/api/v1/kite/status").json()
    assert s["connected"] is False
    # Two distinct causes now get distinct wording: a closed 06:00 IST window vs.
    # Kite refusing a token that is still inside it (logged out elsewhere / revoked).
    # This is the latter; both must tell the user to reconnect.
    assert "reconnect" in s["message"].lower()

def test_obsolete_engine_scan_report_route_is_not_registered(client):
    response = client.get("/api/v1/kite/engine/scan-report")
    assert response.status_code == 404
