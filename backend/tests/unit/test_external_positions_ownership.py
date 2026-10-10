"""Sterling's own protective stop must never block Sterling's own next entry.

``_broker_automation`` used to flag ANY non-terminal order and ANY active GTT as
exposure-capable automation. The protective GTT the system arms on its own
position (and the working SL/entry orders it placed) therefore made the account
"not flat" for every strategy as soon as the first position was protected.

These tests pin the attribution rule:

* automation is Sterling's own when its broker id is recorded on a Sterling
  position/intent, or every contract it can trade is a contract Sterling holds
  (exact exchange/product/symbol identity);
* anything else stays a hazard (fail closed);
* mutual-fund orders and SIPs are not derivative exposure and are not read;
* an unreadable surface makes the whole answer unreadable.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import external_positions as ep
from app.services.external_positions import external_exposure

OWN_SYMBOL = "NIFTY26OCT25000CE"
OWN_IDENTITY = ("NFO", "NRML", OWN_SYMBOL)


def _gtt(trigger_id, symbol=OWN_SYMBOL, *, exchange="NFO", product="NRML",
         status="active", side="SELL"):
    return {
        "id": trigger_id,
        "status": status,
        "type": "single",
        "condition": {
            "exchange": exchange, "tradingsymbol": symbol,
            "trigger_values": [100.0], "last_price": 120.0,
        },
        "orders": [{
            "exchange": exchange, "tradingsymbol": symbol, "product": product,
            "transaction_type": side, "quantity": 75, "order_type": "LIMIT",
            "price": 99.0,
        }],
    }


def _order(order_id, symbol=OWN_SYMBOL, *, exchange="NFO", product="NRML",
           status="TRIGGER PENDING"):
    return {
        "order_id": order_id, "status": status, "exchange": exchange,
        "tradingsymbol": symbol, "product": product, "transaction_type": "SELL",
    }


class _Client:
    """A broker client double: every surface is a plain attribute."""

    def __init__(self, *, orders=(), gtts=(), alerts=(), account="acct-live",
                 raises=None):
        self._account_id = account
        self.orders = list(orders)
        self.gtts = list(gtts)
        self.alerts = list(alerts)
        self.raises = dict(raises or {})
        self.mf_calls = 0

    def _maybe_raise(self, surface):
        if surface in self.raises:
            raise self.raises[surface]

    async def get_positions_raw(self):
        self._maybe_raise("positions")
        return {"net": []}

    async def get_orders(self):
        self._maybe_raise("orders")
        return self.orders

    async def get_gtts(self):
        self._maybe_raise("gtts")
        return self.gtts

    async def get_alerts(self):
        self._maybe_raise("alerts")
        return self.alerts

    async def get_alert(self, alert_id):
        return {}

    # A mutual-fund outage must not be able to halt derivative trading.
    async def get_mf_orders(self):
        self.mf_calls += 1
        raise RuntimeError("MF surface must not be consulted")

    async def get_mf_sips(self):
        self.mf_calls += 1
        raise RuntimeError("MF surface must not be consulted")


@pytest.fixture()
def run(monkeypatch):
    """Run the production ``external_exposure`` against one fake live account."""

    async def _run(client, *, identities=(), gtt_ids=(), order_ids=()):
        account = SimpleNamespace(id="acct-live", kite_user_id="KITE1", is_paper=False)
        monkeypatch.setattr("app.services.exchanges.kite.accounts.bootstrap", lambda: None)
        monkeypatch.setattr(
            "app.services.exchanges.kite.accounts.all_accounts", lambda: [account]
        )

        async def acquire(_account):
            return client

        monkeypatch.setattr("app.services.exchanges.kite.accounts.acquire_client", acquire)
        monkeypatch.setattr(ep, "_known_symbols", lambda _a: set(identities))
        monkeypatch.setattr(
            ep, "_known_broker_ids",
            lambda _a: (frozenset(map(str, gtt_ids)), frozenset(map(str, order_ids))),
        )
        return await external_exposure()

    return _run


class TestOwnedGtt:
    @pytest.mark.asyncio
    async def test_gtt_recorded_on_a_sterling_position_is_not_a_hazard(self, run):
        exposure = await run(
            _Client(gtts=[_gtt(555, symbol="SOMETHING-ELSE")]), gtt_ids=[555]
        )
        assert exposure.readable is True
        assert exposure.automation == ()
        assert exposure.owned_automation == ("gtt:555:ACTIVE",)
        assert exposure.flat is True

    @pytest.mark.asyncio
    async def test_gtt_on_a_contract_sterling_holds_is_not_a_hazard(self, run):
        exposure = await run(_Client(gtts=[_gtt(777)]), identities=[OWN_IDENTITY])
        assert exposure.automation == ()
        assert exposure.owned_automation == ("gtt:777:ACTIVE",)
        assert exposure.flat is True

    @pytest.mark.asyncio
    async def test_oco_gtt_with_both_legs_on_the_held_contract_is_owned(self, run):
        oco = _gtt(778)
        oco["type"] = "two-leg"
        oco["orders"].append({**oco["orders"][0], "price": 140.0})
        exposure = await run(_Client(gtts=[oco]), identities=[OWN_IDENTITY])
        assert exposure.automation == ()
        assert exposure.flat is True

    @pytest.mark.asyncio
    async def test_foreign_gtt_is_a_hazard(self, run):
        exposure = await run(
            _Client(gtts=[_gtt(901, symbol="RELIANCE", exchange="NSE", product="CNC")]),
            identities=[OWN_IDENTITY], gtt_ids=[555],
        )
        assert exposure.automation == ("gtt:901:ACTIVE",)
        assert exposure.flat is False

    @pytest.mark.asyncio
    async def test_same_symbol_other_product_gtt_is_a_hazard(self, run):
        exposure = await run(
            _Client(gtts=[_gtt(902, product="MIS")]), identities=[OWN_IDENTITY]
        )
        assert exposure.automation == ("gtt:902:ACTIVE",)

    @pytest.mark.asyncio
    async def test_same_symbol_other_exchange_gtt_is_a_hazard(self, run):
        exposure = await run(
            _Client(gtts=[_gtt(903, exchange="BFO")]), identities=[OWN_IDENTITY]
        )
        assert exposure.automation == ("gtt:903:ACTIVE",)

    @pytest.mark.asyncio
    async def test_one_foreign_leg_makes_the_whole_gtt_a_hazard(self, run):
        mixed = _gtt(904)
        mixed["orders"].append({**mixed["orders"][0], "tradingsymbol": "BANKNIFTY26OCT55000PE"})
        exposure = await run(_Client(gtts=[mixed]), identities=[OWN_IDENTITY])
        assert exposure.automation == ("gtt:904:ACTIVE",)

    @pytest.mark.asyncio
    async def test_a_gtt_with_no_attributable_contract_is_a_hazard(self, run):
        anonymous = {"id": 905, "status": "active", "condition": {}, "orders": []}
        exposure = await run(_Client(gtts=[anonymous]), identities=[OWN_IDENTITY])
        assert exposure.automation == ("gtt:905:ACTIVE",)

    @pytest.mark.asyncio
    async def test_string_encoded_condition_and_orders_are_understood(self, run):
        import json

        raw = _gtt(556)
        raw["condition"] = json.dumps(raw["condition"])
        raw["orders"] = json.dumps(raw["orders"])
        exposure = await run(_Client(gtts=[raw]), identities=[OWN_IDENTITY])
        assert exposure.automation == ()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "status", ["cancelled", "deleted", "expired", "rejected", "disabled", "triggered"]
    )
    async def test_finished_gtts_are_neither_hazard_nor_owned(self, run, status):
        exposure = await run(_Client(gtts=[_gtt(950, status=status, symbol="X")]))
        assert exposure.automation == ()
        assert exposure.owned_automation == ()


class TestOwnedOrders:
    @pytest.mark.asyncio
    async def test_working_order_recorded_by_id_is_not_a_hazard(self, run):
        exposure = await run(
            _Client(orders=[_order("ORD-1", symbol="OTHER")]), order_ids=["ORD-1"]
        )
        assert exposure.automation == ()
        assert exposure.owned_automation == ("order:ORD-1:TRIGGER PENDING",)
        assert exposure.flat is True

    @pytest.mark.asyncio
    async def test_working_order_on_a_held_contract_is_not_a_hazard(self, run):
        exposure = await run(_Client(orders=[_order("ORD-2")]), identities=[OWN_IDENTITY])
        assert exposure.automation == ()
        assert exposure.flat is True

    @pytest.mark.asyncio
    async def test_foreign_open_order_is_a_hazard(self, run):
        exposure = await run(
            _Client(orders=[_order("ORD-9", symbol="INFY", exchange="NSE", product="CNC",
                                   status="OPEN")]),
            identities=[OWN_IDENTITY], order_ids=["ORD-1"],
        )
        assert exposure.automation == ("order:ORD-9:OPEN",)
        assert exposure.flat is False

    @pytest.mark.asyncio
    async def test_open_order_with_no_symbol_is_a_hazard(self, run):
        exposure = await run(
            _Client(orders=[{"order_id": "OPEN-1", "status": "OPEN"}]),
            identities=[OWN_IDENTITY],
        )
        assert exposure.automation == ("order:OPEN-1:OPEN",)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["COMPLETE", "CANCELLED", "REJECTED"])
    async def test_terminal_orders_are_ignored(self, run, status):
        exposure = await run(_Client(orders=[_order("DONE", symbol="X", status=status)]))
        assert exposure.automation == ()
        assert exposure.owned_automation == ()


class TestMutualFundsAreNotDerivativeExposure:
    @pytest.mark.asyncio
    async def test_mf_surfaces_are_not_consulted_and_never_a_hazard(self, run):
        client = _Client()
        exposure = await run(client)
        assert exposure.readable is True
        assert exposure.flat is True
        assert client.mf_calls == 0


class TestAlerts:
    @pytest.mark.asyncio
    async def test_an_ato_alert_is_still_a_hazard(self, run):
        exposure = await run(
            _Client(alerts=[{"uuid": "al-1", "type": "ato"}]), identities=[OWN_IDENTITY]
        )
        assert exposure.automation == ("alert:al-1:ATO",)
        assert exposure.flat is False

    @pytest.mark.asyncio
    async def test_a_plain_price_alert_is_not_a_hazard(self, run):
        exposure = await run(_Client(alerts=[{"uuid": "al-2", "type": "simple"}]))
        assert exposure.automation == ()

    @pytest.mark.asyncio
    async def test_an_enabled_ato_alert_is_a_hazard(self, run):
        exposure = await run(
            _Client(alerts=[{"uuid": "al-1", "type": "ato", "status": "enabled"}])
        )
        assert exposure.automation == ("alert:al-1:ATO",)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["disabled", "deleted", "triggered", "DISABLED"])
    async def test_a_spent_ato_alert_cannot_place_orders_and_is_not_a_hazard(
        self, run, status
    ):
        exposure = await run(_Client(alerts=[
            {"uuid": "al-1", "type": "ato", "status": status,
             "disabled_reason": "triggered"},
        ]))
        assert exposure.readable is True
        assert exposure.automation == ()
        assert exposure.flat is True

    @pytest.mark.asyncio
    async def test_an_ato_alert_with_no_status_stays_a_hazard(self, run):
        exposure = await run(_Client(alerts=[{"uuid": "al-3", "type": "ato"}]))
        assert exposure.automation == ("alert:al-3:ATO",)

    @pytest.mark.asyncio
    async def test_a_basket_alert_follows_the_same_status_rule(self, run):
        exposure = await run(_Client(alerts=[
            {"uuid": "al-4", "type": "simple", "basket": {"id": 1}, "status": "enabled"},
            {"uuid": "al-5", "type": "simple", "basket": {"id": 2}, "status": "deleted"},
        ]))
        assert exposure.automation == ("alert:al-4:SIMPLE",)


class TestUnreadableIsNeverFlat:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("surface", ["orders", "gtts", "alerts"])
    async def test_a_failing_surface_makes_the_snapshot_unreadable(self, run, surface):
        client = _Client(raises={surface: TimeoutError("broker down")})
        exposure = await run(client, identities=[OWN_IDENTITY])
        assert exposure.readable is False
        assert exposure.flat is None
        assert "automation state could not be read" in exposure.detail
        assert "TimeoutError" in exposure.detail

    @pytest.mark.asyncio
    async def test_a_malformed_row_makes_the_snapshot_unreadable(self, run):
        exposure = await run(_Client(gtts=["not-a-row"]))
        assert exposure.readable is False
        assert exposure.flat is None

    @pytest.mark.asyncio
    async def test_a_malformed_payload_makes_the_snapshot_unreadable(self, run):
        exposure = await run(_Client(orders={"status": "success"}))
        assert exposure.readable is False

    @pytest.mark.asyncio
    async def test_unreadable_local_ids_still_attribute_by_identity(self, run, monkeypatch):
        client = _Client(gtts=[_gtt(111), _gtt(222, symbol="OTHER")])
        account = SimpleNamespace(id="acct-live", kite_user_id="KITE1", is_paper=False)
        monkeypatch.setattr("app.services.exchanges.kite.accounts.bootstrap", lambda: None)
        monkeypatch.setattr(
            "app.services.exchanges.kite.accounts.all_accounts", lambda: [account]
        )

        async def acquire(_a):
            return client

        monkeypatch.setattr("app.services.exchanges.kite.accounts.acquire_client", acquire)
        monkeypatch.setattr(ep, "_known_symbols", lambda _a: {OWN_IDENTITY})
        monkeypatch.setattr(ep, "_known_broker_ids", lambda _a: None)
        exposure = await external_exposure()
        # The held contract's GTT is still recognised; the other one stays a hazard.
        assert exposure.automation == ("gtt:222:ACTIVE",)
        assert exposure.owned_automation == ("gtt:111:ACTIVE",)


class TestLocalOwnershipScan:
    """Sterling's own stores are where the GTT/order ids come from."""

    @pytest.fixture()
    def stores(self, monkeypatch):
        from app.services import (
            adaptive_edge_positions,
            db,
            gamma_move_positions,
            intraday_positions,
        )
        from app.services.kite_engine import order_journal, positions

        monkeypatch.setattr(db, "init", lambda: True)
        monkeypatch.setattr(db, "get_all_configs_strict", lambda: {})
        monkeypatch.setattr(positions, "known_uids", lambda: ["u1"])
        monkeypatch.setattr(order_journal, "known_uids", lambda: [])
        monkeypatch.setattr(order_journal, "unresolved", lambda *_a, **_k: [])
        monkeypatch.setattr(intraday_positions, "open_positions", lambda _u: [])
        monkeypatch.setattr(adaptive_edge_positions, "open_positions", lambda _u: [])
        monkeypatch.setattr(gamma_move_positions, "open_positions", lambda _u: [])
        return positions

    def test_registry_position_contributes_its_gtt_and_order_ids(self, stores, monkeypatch):
        position = SimpleNamespace(
            account_id="acct-1", exchange="NFO", product="NRML", symbol=OWN_SYMBOL,
            gtt_id=4242, order_id="ENTRY-1", qty_by_order={"ENTRY-1": 75, "SCALE-2": 75},
        )
        monkeypatch.setattr(stores, "open_positions", lambda _u: [position])

        owned = ep._scan_local_ownership("acct-1")

        assert owned is not None
        assert owned.identities == {OWN_IDENTITY}
        assert owned.gtt_ids == {"4242"}
        assert owned.order_ids == {"ENTRY-1", "SCALE-2"}
        assert ep._known_broker_ids("acct-1") == (
            frozenset({"4242"}), frozenset({"ENTRY-1", "SCALE-2"})
        )
        assert ep.owned_gtt_ids("acct-1") == frozenset({"4242"})

    def test_zero_ids_mean_none_and_are_not_recorded(self, stores, monkeypatch):
        position = SimpleNamespace(
            account_id="acct-1", exchange="NFO", product="NRML", symbol=OWN_SYMBOL,
            gtt_id=0, order_id="",
        )
        monkeypatch.setattr(stores, "open_positions", lambda _u: [position])

        owned = ep._scan_local_ownership("acct-1")

        assert owned.gtt_ids == frozenset()
        assert owned.order_ids == frozenset()

    def test_another_accounts_ids_are_not_attributed(self, stores, monkeypatch):
        position = SimpleNamespace(
            account_id="acct-other", exchange="NFO", product="NRML", symbol=OWN_SYMBOL,
            gtt_id=4242, order_id="ENTRY-1",
        )
        monkeypatch.setattr(stores, "open_positions", lambda _u: [position])

        owned = ep._scan_local_ownership("acct-1")

        assert owned.gtt_ids == frozenset() and owned.identities == frozenset()

    def test_unresolved_intent_contributes_its_order_id(self, stores, monkeypatch):
        from app.services.kite_engine import order_journal

        monkeypatch.setattr(order_journal, "known_uids", lambda: ["u1"])
        intent = SimpleNamespace(
            exchange="NFO", symbol=OWN_SYMBOL, payload={"product": "NRML"},
            order_id="PENDING-7",
        )
        monkeypatch.setattr(order_journal, "unresolved", lambda *_a, **_k: [intent])

        owned = ep._scan_local_ownership("acct-1")

        assert owned.order_ids == {"PENDING-7"}
        assert OWN_IDENTITY in owned.identities

    def test_gamma_position_contributes_its_gtt_id(self, stores, monkeypatch):
        from app.services import gamma_move_positions

        position = SimpleNamespace(
            account_id="acct-1", product="MIS",
            instrument=SimpleNamespace(exchange="BFO", tradingsymbol="SENSEX26OCT80000CE"),
            gtt_id=31337, order_id="G-1", exit_order_id="",
        )
        monkeypatch.setattr(gamma_move_positions, "open_positions", lambda _u: [position])

        owned = ep._scan_local_ownership("acct-1")

        assert owned.gtt_ids == {"31337"}
        # A pre-upgrade intraday row legitimately sits in the MIS bucket.
        assert ("BFO", "MIS", "SENSEX26OCT80000CE") in owned.identities

    def test_nrml_gamma_position_is_owned_in_its_real_bucket(self, stores, monkeypatch):
        """Gamma trades NRML (so a GTT can be armed); it must not read as foreign."""
        from app.engines.gamma_move import InstrumentRef, PositionState
        from app.services import gamma_move_positions

        inst = InstrumentRef(
            "222", "NIFTY26OCT24000CE", "CE", 24000, "2026-10-29", 75, 0.05, "NFO",
        )
        position = PositionState(
            signal_id="g1", instrument=inst, list_name="BREAKOUT-CE", tier="ENTRY",
            underlying="NIFTY", underlying_token=1, entry=100.0, quantity=75, lots=1,
            entered_ms=1, entry_day="2026-10-05", hold="swing", zone_edge=1.0,
            base_low=90.0, t1=110.0, t2=120.0, status="open", account_id="acct-1",
            gtt_id=777,
        )
        assert position.product == "NRML"
        monkeypatch.setattr(gamma_move_positions, "open_positions", lambda _u: [position])

        owned = ep._scan_local_ownership("acct-1")
        assert ("NFO", "NRML", "NIFTY26OCT24000CE") in owned.identities
        assert ("NFO", "MIS", "NIFTY26OCT24000CE") not in owned.identities

        row = {
            "tradingsymbol": "NIFTY26OCT24000CE", "exchange": "NFO",
            "product": "NRML", "quantity": 75, "average_price": 100.0,
        }
        exposure = ep.classify_broker_positions(
            [row], account="acct-1", known_symbols=set(owned.identities),
        )
        assert exposure.positions == ()
        assert exposure.flat is True

    def test_an_unreadable_store_attributes_nothing(self, stores, monkeypatch):
        def boom(_uid):
            raise RuntimeError("positions store corrupt")

        monkeypatch.setattr(stores, "open_positions", boom)

        assert ep._scan_local_ownership("acct-1") is None
        assert ep._known_symbols("acct-1") is None
        assert ep._known_broker_ids("acct-1") is None
        assert ep.owned_gtt_ids("acct-1") is None


class TestOwnedAutomationDoesNotBlockAdmission:
    @pytest.mark.asyncio
    async def test_owned_stop_is_recorded_and_admission_permits(
        self, run, tmp_path, monkeypatch
    ):
        monkeypatch.setenv(
            "STERLING_EXTERNAL_EXPOSURE_FILE", str(tmp_path / "external.json")
        )
        monkeypatch.setattr(ep, "_live_account_configured", lambda: True)
        monkeypatch.setattr(ep, "live_account_ids", lambda: ("acct-live",))

        exposure = await run(
            _Client(gtts=[_gtt(555)], orders=[_order("ORD-1")]),
            identities=[OWN_IDENTITY], gtt_ids=[555], order_ids=["ORD-1"],
        )
        ep.record_observation(exposure)

        record = ep.last_observation()
        assert record["automation"] == []
        assert sorted(record["owned_automation"]) == [
            "gtt:555:ACTIVE", "order:ORD-1:TRIGGER PENDING",
        ]
        assert ep.admission_blocker() == ("", "")

    @pytest.mark.asyncio
    async def test_a_foreign_gtt_still_blocks_admission(self, run, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "STERLING_EXTERNAL_EXPOSURE_FILE", str(tmp_path / "external.json")
        )
        monkeypatch.setattr(ep, "_live_account_configured", lambda: True)
        monkeypatch.setattr(ep, "live_account_ids", lambda: ("acct-live",))

        exposure = await run(
            _Client(gtts=[_gtt(901, symbol="RELIANCE", exchange="NSE", product="CNC")]),
            identities=[OWN_IDENTITY],
        )
        ep.record_observation(exposure)

        code, reason = ep.admission_blocker()
        assert code == "external_broker_automation"
        assert "gtt:901:ACTIVE" in reason
