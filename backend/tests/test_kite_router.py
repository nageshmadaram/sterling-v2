"""Kite router: multi-tenant CRUD, routing guard, safety gate, paper happy paths."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.services import live_safety
from app.services.exchanges.kite.client import KiteClient
from app.services.exchanges.kite import accounts as kite_accounts
from main import create_app
from tests.kite_paper_double import as_paper_double


def _mock_adapter():
    a = MagicMock()
    a.ping = AsyncMock(return_value=True)
    a.close = AsyncMock(return_value=None)
    return a


@pytest.fixture()
def client():
    from tests.feature_grants import grant_broker_configuration
    grant_broker_configuration("default", "alice", "bob")
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
    """Create an account through the public API.

    The API always creates a LIVE account and ignores ``is_paper`` (a legacy client
    may still send it, which this helper does on purpose). ``paper=True`` then turns
    the stored row into a simulated-broker test double so the paper-transport
    behaviour under test can run without a network.
    """
    r = client.post("/api/v1/kite/accounts", headers=headers or {}, json={
        "label": label, "api_key": "apikey123", "api_secret": "topsecret", "is_paper": paper,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_paper"] is False, "the API must never create a non-live account"
    if paper:
        as_paper_double(body["id"])
        body["is_paper"] = True
    return body


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


def test_account_mode_cannot_be_reinterpreted_in_place(client):
    acc = _add_account(client, paper=True)

    r = client.put(
        f"/api/v1/kite/accounts/{acc['id']}",
        json={"is_paper": False},
    )

    assert r.status_code == 423
    assert "Account type cannot change" in str(r.json()["detail"])


def test_api_never_creates_a_non_live_account_whatever_the_client_sends(client):
    for sent in (True, False, None, "yes", 1):
        body = {"label": f"A-{sent}", "api_key": f"key-{sent}-abcd", "api_secret": "topsecret"}
        if sent is not None:
            body["is_paper"] = sent
        r = client.post("/api/v1/kite/accounts", json=body)
        assert r.status_code == 200, r.text
        created = r.json()
        assert created["is_paper"] is False
        assert kite_accounts.get("default", created["id"]).is_paper is False
        assert kite_accounts.build_client(kite_accounts.get("default", created["id"]))._is_paper is False


def test_update_ignores_the_mode_flag_and_refuses_to_change_it(client):
    acc = _add_account(client, paper=False)
    assert acc["is_paper"] is False

    same = client.put(f"/api/v1/kite/accounts/{acc['id']}",
                      json={"label": "renamed", "is_paper": False})
    assert same.status_code == 200
    assert same.json()["label"] == "renamed" and same.json()["is_paper"] is False

    flip = client.put(f"/api/v1/kite/accounts/{acc['id']}", json={"is_paper": True})
    assert flip.status_code == 423
    assert kite_accounts.get("default", acc["id"]).is_paper is False


def test_live_logout_refuses_broker_position_before_revoking_session(client, monkeypatch):
    from app.api.v1.endpoints import kite as kite_endpoint

    acc = _add_account(client, paper=False)
    kite_accounts.get("default", acc["id"]).access_token_enc = "present-session"
    broker = MagicMock()
    broker._is_paper = False
    broker._account_id = acc["id"]
    broker.get_positions_raw = AsyncMock(return_value={
        "net": [{"tradingsymbol": "NIFTY26OCT25000CE", "quantity": 75}]
    })
    broker.get_orders = AsyncMock(return_value=[])
    broker.get_gtts = AsyncMock(return_value=[])
    broker.get_mf_orders = AsyncMock(return_value=[])
    broker.get_mf_sips = AsyncMock(return_value=[])
    broker.get_alerts = AsyncMock(return_value=[])

    monkeypatch.setattr(kite_endpoint, "_local_account_exposure", lambda *_a: [])
    monkeypatch.setattr(
        kite_accounts, "acquire_client", AsyncMock(return_value=broker)
    )
    clear_session = MagicMock()
    stop_ticker = AsyncMock()
    monkeypatch.setattr(kite_accounts, "clear_session", clear_session)
    monkeypatch.setattr(kite_endpoint.ticker_manager, "stop", stop_ticker)

    r = client.post("/api/v1/kite/logout")

    assert r.status_code == 423
    assert "still holds position" in str(r.json()["detail"])
    clear_session.assert_not_called()
    stop_ticker.assert_not_awaited()


def test_paper_logout_does_not_require_broker_flatness(client, monkeypatch):
    from app.api.v1.endpoints import kite as kite_endpoint

    _add_account(client, paper=True)
    detachable = AsyncMock()
    monkeypatch.setattr(kite_endpoint, "_require_account_detachable", detachable)

    r = client.post("/api/v1/kite/logout")

    assert r.status_code == 200
    assert r.json()["message"] == "Logged out"
    detachable.assert_not_awaited()


def test_live_account_with_broker_position_cannot_be_deleted(client, monkeypatch):
    from app.api.v1.endpoints import kite as kite_endpoint

    acc = _add_account(client, paper=False)
    broker = MagicMock()
    broker._is_paper = False
    broker._account_id = acc["id"]
    broker.get_positions_raw = AsyncMock(return_value={
        "net": [{"tradingsymbol": "NIFTY26OCT25000CE", "quantity": 75}]
    })
    broker.get_orders = AsyncMock(return_value=[])
    broker.get_gtts = AsyncMock(return_value=[])
    broker.get_mf_orders = AsyncMock(return_value=[])
    broker.get_mf_sips = AsyncMock(return_value=[])
    broker.get_alerts = AsyncMock(return_value=[])

    monkeypatch.setattr(kite_endpoint, "_local_account_exposure", lambda *_a: [])
    monkeypatch.setattr(
        kite_accounts, "acquire_client", AsyncMock(return_value=broker)
    )

    r = client.delete(f"/api/v1/kite/accounts/{acc['id']}")

    assert r.status_code == 423
    assert "still holds position" in str(r.json()["detail"])
    assert client.get("/api/v1/kite/accounts").json()["count"] == 1


def test_active_account_switch_refuses_live_pending_order(client, monkeypatch):
    from app.api.v1.endpoints import kite as kite_endpoint

    live = _add_account(client, label="live", paper=False)
    paper = _add_account(client, label="paper", paper=True)

    broker = MagicMock()
    broker._is_paper = False
    broker._account_id = live["id"]
    broker.get_positions_raw = AsyncMock(return_value={"net": []})
    broker.get_orders = AsyncMock(
        return_value=[{"order_id": "OID-PENDING", "status": "OPEN"}]
    )
    broker.get_gtts = AsyncMock(return_value=[])
    broker.get_mf_orders = AsyncMock(return_value=[])
    broker.get_mf_sips = AsyncMock(return_value=[])
    broker.get_alerts = AsyncMock(return_value=[])

    monkeypatch.setattr(kite_endpoint, "_local_account_exposure", lambda *_a: [])
    monkeypatch.setattr(
        kite_accounts, "acquire_client", AsyncMock(return_value=broker)
    )

    r = client.post(f"/api/v1/kite/accounts/{paper['id']}/activate")

    assert r.status_code == 423
    assert "live/pending order" in str(r.json()["detail"])
    assert client.get("/api/v1/kite/accounts").json()["active_id"] == live["id"]


def test_active_account_switch_refuses_live_mutual_fund_sip(client, monkeypatch):
    from app.api.v1.endpoints import kite as kite_endpoint

    live = _add_account(client, label="live", paper=False)
    paper = _add_account(client, label="paper", paper=True)

    broker = MagicMock()
    broker._is_paper = False
    broker._account_id = live["id"]
    broker.get_positions_raw = AsyncMock(return_value={"net": []})
    broker.get_orders = AsyncMock(return_value=[])
    broker.get_gtts = AsyncMock(return_value=[])
    broker.get_mf_orders = AsyncMock(return_value=[])
    broker.get_mf_sips = AsyncMock(
        return_value=[{"sip_id": "SIP-ACTIVE", "status": "ACTIVE"}]
    )
    broker.get_alerts = AsyncMock(return_value=[])

    monkeypatch.setattr(kite_endpoint, "_local_account_exposure", lambda *_a: [])
    monkeypatch.setattr(
        kite_accounts, "acquire_client", AsyncMock(return_value=broker)
    )

    r = client.post(f"/api/v1/kite/accounts/{paper['id']}/activate")

    assert r.status_code == 423
    assert "mutual-fund SIP" in str(r.json()["detail"])
    assert client.get("/api/v1/kite/accounts").json()["active_id"] == live["id"]


def test_live_account_with_ato_alert_cannot_be_deleted(client, monkeypatch):
    from app.api.v1.endpoints import kite as kite_endpoint

    acc = _add_account(client, paper=False)
    broker = MagicMock()
    broker._is_paper = False
    broker._account_id = acc["id"]
    broker.get_positions_raw = AsyncMock(return_value={"net": []})
    broker.get_orders = AsyncMock(return_value=[])
    broker.get_gtts = AsyncMock(return_value=[])
    broker.get_mf_orders = AsyncMock(return_value=[])
    broker.get_mf_sips = AsyncMock(return_value=[])
    # List payload omits type: detach must resolve broker detail rather than
    # assuming an untyped alert is a harmless notification.
    broker.get_alerts = AsyncMock(return_value=[{"uuid": "ATO-1", "name": "auto"}])
    broker.get_alert = AsyncMock(return_value={"uuid": "ATO-1", "type": "ato"})

    monkeypatch.setattr(kite_endpoint, "_local_account_exposure", lambda *_a: [])
    monkeypatch.setattr(
        kite_accounts, "acquire_client", AsyncMock(return_value=broker)
    )

    r = client.delete(f"/api/v1/kite/accounts/{acc['id']}")

    assert r.status_code == 423
    assert "ATO/unknown executable alert" in str(r.json()["detail"])
    broker.get_alert.assert_awaited_once_with("ATO-1")
    assert client.get("/api/v1/kite/accounts").json()["count"] == 1


def test_flat_live_account_can_be_deleted_after_fresh_broker_proof(
    client, monkeypatch
):
    from app.api.v1.endpoints import kite as kite_endpoint

    acc = _add_account(client, paper=False)
    broker = MagicMock()
    broker._is_paper = False
    broker._account_id = acc["id"]
    broker.get_positions_raw = AsyncMock(return_value={"net": []})
    broker.get_orders = AsyncMock(return_value=[])
    broker.get_gtts = AsyncMock(return_value=[])
    broker.get_mf_orders = AsyncMock(return_value=[])
    broker.get_mf_sips = AsyncMock(return_value=[])
    broker.get_alerts = AsyncMock(return_value=[])

    monkeypatch.setattr(kite_endpoint, "_local_account_exposure", lambda *_a: [])
    monkeypatch.setattr(
        kite_accounts, "acquire_client", AsyncMock(return_value=broker)
    )

    r = client.delete(f"/api/v1/kite/accounts/{acc['id']}")

    assert r.status_code == 204
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


def test_status_without_an_account_does_not_call_it_a_non_live_account(client):
    s = client.get("/api/v1/kite/status").json()
    assert s["connected"] is False
    assert s["is_paper"] is False
    assert "No active Kite account" in s["message"]


# ─── Orders / safety ─────────────────────────────────────────────────────────
def test_place_order_paper_happy_path(client):
    _add_account(client, paper=True)
    r = client.post("/api/v1/kite/orders", json={
        "tradingsymbol": "INFY", "exchange": "NSE", "transaction_type": "BUY",
        "quantity": 1, "order_type": "MARKET", "product": "CNC",
    })
    assert r.status_code == 200, r.text
    assert r.json()["order_id"].startswith("PAPER-")


@pytest.mark.parametrize("marker", ["UNKNOWN", "PENDING"])
def test_order_idempotency_marker_never_masquerades_as_broker_ack(
    client, monkeypatch, marker,
):
    """An uncertain POST must not return 200 with a synthetic broker order ID."""
    from app.api.v1.endpoints import kite

    _add_account(client, paper=True)
    monkeypatch.setattr(kite, "_safety_gate", lambda *_a, **_kw: "synthetic-key")
    live_safety.record_idempotency("synthetic-key", marker)
    broker = SimpleNamespace(_is_paper=True, _place=AsyncMock())
    monkeypatch.setattr(kite_accounts, "acquire_client", AsyncMock(return_value=broker))

    request = {
        "tradingsymbol": "INFY", "exchange": "NSE", "transaction_type": "BUY",
        "quantity": 1, "order_type": "MARKET", "product": "CNC",
    }
    response = client.post("/api/v1/kite/orders", json=request)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "submission_unknown"
    assert "order_id" not in response.json()
    assert live_safety.check_idempotency("synthetic-key") == marker
    broker._place.assert_not_awaited()


def test_live_noncanonical_order_fallback_is_refused(client):
    """A live cash order cannot bypass canonical execution via KiteClient._place."""
    _add_account(client, paper=False)
    r = client.post("/api/v1/kite/orders", json={
        "tradingsymbol": "INFY", "exchange": "NSE", "transaction_type": "BUY",
        "quantity": 1, "order_type": "MARKET", "product": "CNC",
    })
    assert r.status_code == 423
    assert "canonical strategy path" in str(r.json()["detail"])


@pytest.mark.parametrize("mode", [None, 0, 1, "false", "missing"])
@pytest.mark.parametrize("operation", ["place", "cancel"])
def test_order_mutations_refuse_unverified_client_mode(client, monkeypatch, mode, operation):
    _add_account(client)
    broker = SimpleNamespace(_place=AsyncMock(), cancel_order=AsyncMock())
    if mode != "missing":
        broker._is_paper = mode
    monkeypatch.setattr(kite_accounts, "acquire_client", AsyncMock(return_value=broker))

    if operation == "place":
        response = client.post("/api/v1/kite/orders", json={
            "tradingsymbol": "INFY", "exchange": "NSE", "transaction_type": "BUY",
            "quantity": 1, "order_type": "MARKET", "product": "CNC",
        })
    else:
        response = client.delete("/api/v1/kite/orders/OID-1")

    assert response.status_code == 423, response.text
    assert "Cannot verify broker mode" in response.json()["detail"]
    broker._place.assert_not_awaited()
    broker.cancel_order.assert_not_awaited()


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


@pytest.mark.parametrize("alert_type", ["UNKNOWN", "", " ato "])
def test_live_alert_creation_refuses_unknown_or_disguised_order_type(
    client, monkeypatch, alert_type
):
    _add_account(client, paper=False)
    create = AsyncMock()
    monkeypatch.setattr(KiteClient, "create_alert", create)

    response = client.post("/api/v1/kite/alerts", json={
        "name": "unknown semantics", "lhs_tradingsymbol": "INFY",
        "rhs_constant": 1400, "alert_type": alert_type,
    })

    assert response.status_code == 423, response.text
    create.assert_not_awaited()


@pytest.mark.parametrize("detail", [
    {"uuid": "A1", "type": "UNKNOWN"},
    {"uuid": "other", "type": "simple"},
    {"type": "simple"},
    [],
])
def test_live_alert_modify_requires_verified_identity_and_known_type(client, monkeypatch, detail):
    _add_account(client, paper=False)
    modify = AsyncMock()
    monkeypatch.setattr(KiteClient, "get_alert", AsyncMock(return_value=detail))
    monkeypatch.setattr(KiteClient, "modify_alert", modify)

    response = client.put("/api/v1/kite/alerts/A1", json={"status": "enabled"})

    assert response.status_code == 423, response.text
    assert "Cannot verify live alert" in response.json()["detail"]
    modify.assert_not_awaited()


@pytest.mark.parametrize("requested_type", ["UNKNOWN", "", " ato "])
def test_live_alert_modify_refuses_unknown_or_disguised_order_type(
    client, monkeypatch, requested_type
):
    _add_account(client, paper=False)
    modify = AsyncMock()
    monkeypatch.setattr(KiteClient, "get_alert", AsyncMock(return_value={"uuid": "A1", "type": "simple"}))
    monkeypatch.setattr(KiteClient, "modify_alert", modify)

    response = client.put("/api/v1/kite/alerts/A1", json={"type": requested_type})

    assert response.status_code == 423, response.text
    modify.assert_not_awaited()


@pytest.mark.parametrize("mode", [None, 0, 1, "false"])
@pytest.mark.parametrize("operation", ["create", "modify"])
def test_alert_mutations_refuse_unverified_client_mode(client, monkeypatch, mode, operation):
    _add_account(client)
    broker = SimpleNamespace(_is_paper=mode, create_alert=AsyncMock(), modify_alert=AsyncMock())
    monkeypatch.setattr(kite_accounts, "acquire_client", AsyncMock(return_value=broker))

    if operation == "create":
        response = client.post("/api/v1/kite/alerts", json={
            "name": "notification", "lhs_tradingsymbol": "INFY", "rhs_constant": 1400,
        })
    else:
        response = client.put("/api/v1/kite/alerts/A1", json={"status": "enabled"})

    assert response.status_code == 423, response.text
    assert "Cannot verify broker mode" in response.json()["detail"]
    broker.create_alert.assert_not_awaited()
    broker.modify_alert.assert_not_awaited()


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


def test_live_ato_disable_cannot_disarm_unverified_protection(client, monkeypatch):
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
    assert r.status_code == 423
    assert seen == {}


@pytest.mark.parametrize("current", [
    {"uuid": "A1", "type": "ato"},
    {"uuid": "A1", "type": "UNKNOWN"},
    {"uuid": "OTHER", "type": "simple"},
    {"uuid": "A1", "type": "simple", "alert_type": "ato"},
    {"uuid": "A1", "type": "simple", "basket": []},
])
def test_live_alert_delete_preserves_unverified_protection(client, monkeypatch, current):
    _add_account(client, paper=False)

    async def existing(self, uuid):
        return current

    async def must_not_delete(self, uuids):
        raise AssertionError("unverified alert deletion reached broker")

    monkeypatch.setattr(KiteClient, "get_alert", existing)
    monkeypatch.setattr(KiteClient, "delete_alerts", must_not_delete)
    result = client.request("DELETE", "/api/v1/kite/alerts", json={"uuids": ["A1"]})
    assert result.status_code == 423


def test_live_ato_alert_deletion_is_refused_before_transport(client, monkeypatch):
    _add_account(client, paper=False)

    async def existing_ato(self, uuid):
        return {"uuid": uuid, "type": "ato"}

    async def must_not_delete(self, uuids):
        raise AssertionError("live ATO deletion reached broker transport")

    monkeypatch.setattr(KiteClient, "get_alert", existing_ato)
    monkeypatch.setattr(KiteClient, "delete_alerts", must_not_delete)

    r = client.request(
        "DELETE",
        "/api/v1/kite/alerts",
        json={"uuids": ["A1"]},
    )
    assert r.status_code == 423
    assert "ATO alert deletion" in str(r.json()["detail"])


def test_live_option_sell_wrapper_uses_reduce_exposure_semantics(client, monkeypatch):
    from app.api.v1.endpoints import kite as kite_endpoint

    _add_account(client, paper=False)
    seen = {}

    def gate(_user, _parts, **kwargs):
        seen.update(kwargs)
        return "idem-live-exit"

    async def fake_run(_user, _fn):
        return {"order_id": "EXIT-1"}

    monkeypatch.setattr(kite_endpoint, "_safety_gate", gate)
    monkeypatch.setattr(kite_endpoint, "_run", fake_run)

    r = client.post("/api/v1/kite/orders", json={
        "tradingsymbol": "NIFTY26OCT25000PE",
        "exchange": "NFO",
        "transaction_type": "SELL",
        "quantity": 75,
        "order_type": "MARKET",
        "product": "NRML",
    })

    assert r.status_code == 200, r.text
    assert seen["exposure_effect"] == "REDUCE_EXPOSURE"
    assert seen["check_daily_loss"] is False


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


def test_session_without_access_token_is_not_reported_connected(client, monkeypatch):
    acc = _add_account(client, paper=True)

    async def fake_gen(self, request_token):
        return {"user_id": "ZID1", "user_name": "Trader"}

    monkeypatch.setattr(KiteClient, "generate_session", fake_gen)

    r = client.post(
        "/api/v1/kite/session",
        json={"account_id": acc["id"], "request_token": "rt"},
    )

    assert r.status_code == 409
    assert "kite_access_token_missing" in str(r.json()["detail"])
    listed = client.get("/api/v1/kite/accounts").json()["accounts"][0]
    assert listed["connected"] is False


def test_callback_success_connects_signed_account(client, monkeypatch):
    _add_account(client, paper=True)
    state = client.get("/api/v1/kite/login-url").json()["state"]

    async def fake_gen(self, request_token):
        return {"access_token": "ATOK", "user_id": "ZID1", "user_name": "Trader"}

    async def fake_profile(self):
        return {"user_id": "ZID1", "user_name": "Trader"}
    monkeypatch.setattr(KiteClient, "generate_session", fake_gen)
    monkeypatch.setattr(KiteClient, "get_profile", fake_profile)  # /status now validates the token

    r = client.get(
        "/api/v1/kite/callback",
        params={
            "request_token": "rt",
            "action": "login",
            "status": "success",
            "state": state,
        },
    )
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


def test_callback_without_signed_state_is_refused(client):
    _add_account(client, paper=True)

    r = client.get("/api/v1/kite/callback?request_token=rt&status=success")

    assert r.status_code == 400
    assert "Login link required" in r.text
    assert "signed account binding" in r.text


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


@pytest.fixture(autouse=True)
def isolated_public_login_preflight(monkeypatch):
    # Broker login network behavior has its own MockTransport contract tests.
    from unittest.mock import AsyncMock
    monkeypatch.setattr("app.services.exchanges.kite.login_preflight.check_login_key", AsyncMock())


def test_initial_live_credentials_can_be_repaired_and_telegram_uses_saved_key(client, monkeypatch):
    from app.services.notifications import telegram_kite
    account = _add_account(client, paper=False)
    broker = AsyncMock(side_effect=RuntimeError("no access token"))
    monkeypatch.setattr(kite_accounts, "acquire_client", broker)
    result = client.put(f"/api/v1/kite/accounts/{account['id']}", json={
        "api_key": "correctedkey", "api_secret": "correctedsecret"})
    assert result.status_code == 200, result.text
    saved = kite_accounts.get("default", account["id"])
    assert saved.api_key == "correctedkey" and saved.api_secret == "correctedsecret"
    assert saved.access_token_enc == "" and saved.kite_user_id == ""
    broker.assert_not_awaited()
    monkeypatch.setattr("app.services.public_access.status", lambda: {"app_url":"https://sterling.example"})
    _, keyboard = telegram_kite.broker_login_card()
    from urllib.parse import urlparse, parse_qs
    assert parse_qs(urlparse(keyboard["inline_keyboard"][0][0]["url"]).query) == {"sterling_login":["1"], "broker_login":["1"]}
    assert "correctedkey" not in str(keyboard)
    assert "api_key=correctedkey" in client.get("/api/v1/kite/login-url").json()["login_url"]


def test_initial_key_repair_still_refuses_local_exposure(client, monkeypatch):
    from app.api.v1.endpoints import kite as endpoint
    account = _add_account(client, paper=False)
    monkeypatch.setattr(endpoint, "_local_account_exposure", lambda *args: ["unresolved intent"])
    result = client.put(f"/api/v1/kite/accounts/{account['id']}", json={"api_key":"correctedkey"})
    assert result.status_code == 423 and "tracks exposure" in result.json()["detail"]
    assert kite_accounts.get("default", account["id"]).api_key == "apikey123"


@pytest.mark.parametrize("history", ["identity", "prior_login", "session", "audit"])
def test_previously_connected_account_cannot_use_initial_repair(client, monkeypatch, history):
    from app.services import db
    account = _add_account(client, paper=False)
    stored = kite_accounts.get("default", account["id"])
    if history == "identity": stored.kite_user_id = "SYNTHETIC"
    elif history == "prior_login": stored.last_login_at_ms = 1
    elif history == "session": stored.access_token_enc = "synthetic-nonempty"
    else:
        with db._conn() as connection:
            connection.execute("INSERT INTO kite_account_audit (ts_ms,account_id,user_id,event,detail_json) VALUES (1,?,'default','quarantine','{}')", (account["id"],))
    monkeypatch.setattr(kite_accounts, "acquire_client", AsyncMock(side_effect=RuntimeError("session expired")))
    result = client.put(f"/api/v1/kite/accounts/{account['id']}", json={"api_key":"correctedkey"})
    assert result.status_code == 423 and "flatness cannot be proved" in result.json()["detail"]
    assert stored.api_key == "apikey123"


def test_force_delete_cannot_ignore_broker_held_position(client, monkeypatch):
    from app.api.v1.endpoints import kite as endpoint
    from types import SimpleNamespace
    account = _add_account(client, paper=False)
    stored = kite_accounts.get('default', account['id'])
    stored.kite_user_id = 'KNOWN-BROKER-IDENTITY'
    monkeypatch.setattr(endpoint, '_local_account_exposure', lambda *args: [])
    broker = SimpleNamespace(_is_paper=False, _account_id=stored.id,
        get_positions_raw=AsyncMock(return_value={'net':[{'tradingsymbol':'OWNED','quantity':50}]}))
    monkeypatch.setattr(kite_accounts, 'acquire_client', AsyncMock(return_value=broker))
    result = client.delete(f"/api/v1/kite/accounts/{stored.id}?force=true")
    assert result.status_code == 423 and 'broker still holds' in result.json()['detail']
    assert kite_accounts.get('default', stored.id) is stored


def test_price_rules_exposure_is_included_in_account_detach_checks(client, monkeypatch):
    from app.api.v1.endpoints import kite as endpoint
    from app.services import price_rules_store
    from types import SimpleNamespace
    account = _add_account(client, paper=False)
    monkeypatch.setattr(price_rules_store, 'open_positions', lambda uid: [SimpleNamespace(account_id=account['id'],symbol='OWNED-PRICE-RULE')])
    assert any('price_rules:OWNED-PRICE-RULE' in item for item in endpoint._local_account_exposure('default',account['id']))


def test_missing_session_logout_is_noop_preserving_monitoring(client, monkeypatch):
    from app.api.v1.endpoints import kite as endpoint
    acc = _add_account(client, paper=False)
    detachable = AsyncMock()
    clear = MagicMock()
    stop = AsyncMock()
    monkeypatch.setattr(endpoint, '_require_account_detachable', detachable)
    monkeypatch.setattr(kite_accounts, 'clear_session', clear)
    monkeypatch.setattr(endpoint.ticker_manager, 'stop', stop)
    response = client.post('/api/v1/kite/logout')
    assert response.status_code == 200
    assert 'Already logged out' in response.json()['message']
    detachable.assert_not_awaited()
    clear.assert_not_called()
    stop.assert_not_awaited()
    assert kite_accounts.get('default', acc['id']) is not None
