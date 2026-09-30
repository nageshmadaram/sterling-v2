"""Sterling's journal being empty is not the broker being flat.

Written after a live check found 16625 of CDSL26SEP1500CE open on the family
account with no Sterling intent, no fill, no registry entry and no protection —
while every local store read as flat. These tests keep that reading impossible.
"""
from __future__ import annotations

import pytest

from app.services.external_positions import (
    DISCOVERY_REASON,
    SOURCE,
    ExternalExposure,
    classify_broker_positions,
    external_exposure,
    render_external,
)

ACCOUNT = "AA0595"


@pytest.fixture(autouse=True)
def _empty_additional_local_exposure_sources(monkeypatch):
    """Tests here isolate broker-external behaviour from other local registries."""
    from app.services import (
        adaptive_edge_positions,
        db,
        gamma_move_positions,
        intraday_positions,
    )
    from app.services.kite_engine import order_journal

    monkeypatch.setattr(order_journal, "known_uids", lambda: [])
    monkeypatch.setattr(db, "get_all_configs_strict", lambda: {})
    monkeypatch.setattr(intraday_positions, "open_positions", lambda _uid: [])
    monkeypatch.setattr(adaptive_edge_positions, "open_positions", lambda _uid: [])
    monkeypatch.setattr(gamma_move_positions, "open_positions", lambda _uid: [])


def _broker_row(symbol="CDSL26SEP1500CE", quantity=16625, **overrides):
    row = {
        "tradingsymbol": symbol, "exchange": "NFO", "product": "NRML",
        "quantity": quantity, "average_price": 3.271429, "last_price": 3.35,
        "unrealised": 1306.24,
    }
    row.update(overrides)
    return row


class TestClassification:
    def test_a_position_sterling_never_opened_is_external(self):
        exposure = classify_broker_positions(
            [_broker_row()], account=ACCOUNT, known_symbols=set())
        assert exposure.flat is False
        position = exposure.positions[0]
        assert position.instrument == "CDSL26SEP1500CE"
        assert position.quantity == 16625
        assert position.source == SOURCE
        assert position.discovery_reason == DISCOVERY_REASON

    def test_its_provenance_says_sterling_did_not_open_it(self):
        position = classify_broker_positions(
            [_broker_row()], account=ACCOUNT, known_symbols=set()).positions[0]
        # Importing it as though Sterling had opened it would put a trade no
        # strategy made into the promotion sample.
        assert position.managed_by_sterling is False
        assert position.sterling_intent == "none"
        assert position.sterling_fill == "none"
        assert position.protection_known is False

    def test_a_position_sterling_knows_about_is_not_external(self):
        exposure = classify_broker_positions(
            [_broker_row(symbol="NIFTY26JAN24000CE")], account=ACCOUNT,
            known_symbols={"NIFTY26JAN24000CE"})
        assert exposure.flat is True

    def test_same_symbol_wrong_product_is_external(self):
        exposure = classify_broker_positions(
            [_broker_row(symbol="NIFTY26JAN24000CE", product="MIS")],
            account=ACCOUNT,
            known_symbols={("NFO", "NRML", "NIFTY26JAN24000CE")},
        )
        assert exposure.flat is False

    def test_same_symbol_wrong_exchange_is_external(self):
        exposure = classify_broker_positions(
            [_broker_row(symbol="NIFTY26JAN24000CE", exchange="BFO")],
            account=ACCOUNT,
            known_symbols={("NFO", "NRML", "NIFTY26JAN24000CE")},
        )
        assert exposure.flat is False

    def test_exact_managed_identity_is_not_external(self):
        exposure = classify_broker_positions(
            [_broker_row(symbol="NIFTY26JAN24000CE")],
            account=ACCOUNT,
            known_symbols={("NFO", "NRML", "NIFTY26JAN24000CE")},
        )
        assert exposure.flat is True

    def test_matching_ignores_case(self):
        exposure = classify_broker_positions(
            [_broker_row(symbol="nifty26jan24000ce")], account=ACCOUNT,
            known_symbols={"NIFTY26JAN24000CE"})
        assert exposure.flat is True

    def test_a_closed_position_is_not_exposure(self):
        exposure = classify_broker_positions(
            [_broker_row(quantity=0)], account=ACCOUNT, known_symbols=set())
        assert exposure.flat is True

    def test_a_short_position_is_still_exposure(self):
        exposure = classify_broker_positions(
            [_broker_row(quantity=-50)], account=ACCOUNT, known_symbols=set())
        assert exposure.flat is False
        assert exposure.positions[0].quantity == -50


class TestEveryLiveAccountIsCounted:
    @pytest.mark.asyncio
    async def test_automatic_snapshot_aggregates_all_live_accounts(self, monkeypatch):
        from types import SimpleNamespace

        accounts = [
            SimpleNamespace(id="acct-1", kite_user_id="KITE1", is_paper=False),
            SimpleNamespace(id="acct-2", kite_user_id="KITE2", is_paper=False),
        ]

        class Client:
            def __init__(self, account_id, symbol):
                self._account_id = account_id
                self.symbol = symbol

            async def get_positions_raw(self):
                return {"net": [_broker_row(symbol=self.symbol)]}

            async def get_orders(self):
                return []

            async def get_gtts(self):
                return []

            async def get_mf_orders(self):
                return []

            async def get_mf_sips(self):
                return []

            async def get_alerts(self):
                return []

        clients = {
            "acct-1": Client("acct-1", "NIFTY26JAN24000CE"),
            "acct-2": Client("acct-2", "BANKNIFTY26JAN55000PE"),
        }

        monkeypatch.setattr(
            "app.services.exchanges.kite.accounts.bootstrap", lambda: None
        )
        monkeypatch.setattr(
            "app.services.exchanges.kite.accounts.all_accounts", lambda: accounts
        )

        async def acquire(account):
            return clients[account.id]

        monkeypatch.setattr(
            "app.services.exchanges.kite.accounts.acquire_client", acquire
        )
        monkeypatch.setattr(
            "app.services.external_positions._known_symbols", lambda _account: set()
        )

        exposure = await external_exposure()

        assert exposure.readable is True
        assert exposure.count == 2
        assert exposure.accounts == ("acct-1", "acct-2")
        assert {p.account for p in exposure.positions} == {"KITE1", "KITE2"}
        assert {p.instrument for p in exposure.positions} == {
            "NIFTY26JAN24000CE",
            "BANKNIFTY26JAN55000PE",
        }


    @pytest.mark.asyncio
    async def test_flat_positions_with_open_order_are_not_broker_flat(
        self, monkeypatch
    ):
        from types import SimpleNamespace

        account = SimpleNamespace(
            id="acct-order", kite_user_id="KITE-ORDER", is_paper=False
        )

        class Client:
            _account_id = "acct-order"

            async def get_positions_raw(self):
                return {"net": []}

            async def get_orders(self):
                return [{"order_id": "OPEN-1", "status": "OPEN"}]

            async def get_gtts(self):
                return []

            async def get_mf_orders(self):
                return []

            async def get_mf_sips(self):
                return []

            async def get_alerts(self):
                return []

        monkeypatch.setattr(
            "app.services.exchanges.kite.accounts.bootstrap", lambda: None
        )
        monkeypatch.setattr(
            "app.services.exchanges.kite.accounts.all_accounts", lambda: [account]
        )
        # acquire_client is awaited in production.
        async def acquire(_account):
            return Client()
        monkeypatch.setattr(
            "app.services.exchanges.kite.accounts.acquire_client", acquire
        )
        monkeypatch.setattr(
            "app.services.external_positions._known_symbols", lambda _account: set()
        )

        exposure = await external_exposure()

        assert exposure.readable is True
        assert exposure.positions == ()
        assert exposure.automation == ("order:OPEN-1:OPEN",)
        assert exposure.flat is False


    @pytest.mark.asyncio
    async def test_one_unreadable_live_account_makes_whole_snapshot_unknown(
        self, monkeypatch
    ):
        from types import SimpleNamespace

        accounts = [
            SimpleNamespace(id="acct-1", kite_user_id="KITE1", is_paper=False),
            SimpleNamespace(id="acct-2", kite_user_id="KITE2", is_paper=False),
        ]

        class GoodClient:
            _account_id = "acct-1"

            async def get_positions_raw(self):
                return {"net": []}

            async def get_orders(self):
                return []

            async def get_gtts(self):
                return []

            async def get_mf_orders(self):
                return []

            async def get_mf_sips(self):
                return []

            async def get_alerts(self):
                return []

        class BadClient:
            _account_id = "acct-2"

            async def get_positions_raw(self):
                raise TimeoutError("second account unavailable")

        clients = {"acct-1": GoodClient(), "acct-2": BadClient()}

        monkeypatch.setattr(
            "app.services.exchanges.kite.accounts.bootstrap", lambda: None
        )
        monkeypatch.setattr(
            "app.services.exchanges.kite.accounts.all_accounts", lambda: accounts
        )

        async def acquire(account):
            return clients[account.id]

        monkeypatch.setattr(
            "app.services.exchanges.kite.accounts.acquire_client", acquire
        )
        monkeypatch.setattr(
            "app.services.external_positions._known_symbols", lambda _account: set()
        )

        exposure = await external_exposure()

        assert exposure.readable is False
        assert exposure.count is None
        assert "KITE2" in exposure.detail
        assert "TimeoutError" in exposure.detail


class TestManagedIdentityAttribution:
    def test_all_managed_position_registries_contribute_exact_broker_identity(
        self, monkeypatch
    ):
        from types import SimpleNamespace

        from app.services import (
            adaptive_edge_positions,
            db,
            external_positions,
            gamma_move_positions,
            intraday_positions,
        )
        from app.services.kite_engine import order_journal, positions

        monkeypatch.setattr(db, "init", lambda: True)
        monkeypatch.setattr(
            db,
            "get_all_configs_strict",
            lambda: {
                "intraday_positions_legacy": "[]",
                "adaptive_edge_positions:legacy": "[]",
                "gamma_move_positions_legacy": "[]",
            },
        )
        monkeypatch.setattr(positions, "known_uids", lambda: [])
        monkeypatch.setattr(order_journal, "known_uids", lambda: [])
        monkeypatch.setattr(order_journal, "unresolved", lambda *_a, **_k: [])
        monkeypatch.setattr(positions, "open_positions", lambda _uid: [])

        monkeypatch.setattr(
            intraday_positions,
            "open_positions",
            lambda _uid: [
                SimpleNamespace(
                    account_id="acct-1",
                    contract=SimpleNamespace(
                        exchange="NFO", tradingsymbol="INTRADAY26OCT25000CE"
                    ),
                )
            ],
        )
        monkeypatch.setattr(
            adaptive_edge_positions,
            "open_positions",
            lambda _uid: [
                SimpleNamespace(
                    account_id="acct-1",
                    exchange="NFO",
                    symbol="ADAPTIVE26OCT25000PE",
                )
            ],
        )
        monkeypatch.setattr(
            gamma_move_positions,
            "open_positions",
            lambda _uid: [
                SimpleNamespace(
                    account_id="acct-1",
                    instrument=SimpleNamespace(
                        exchange="BFO", tradingsymbol="SENSEX26OCT80000CE"
                    ),
                )
            ],
        )

        known = external_positions._known_symbols("acct-1")

        assert known == {
            ("NFO", "MIS", "INTRADAY26OCT25000CE"),
            ("NFO", "MIS", "ADAPTIVE26OCT25000PE"),
            ("BFO", "MIS", "SENSEX26OCT80000CE"),
        }


    def test_unresolved_intent_user_is_attributed_before_position_projection(
        self, monkeypatch
    ):
        from types import SimpleNamespace

        from app.services import (
            adaptive_edge_positions,
            db,
            external_positions,
            gamma_move_positions,
            intraday_positions,
        )
        from app.services.kite_engine import order_journal, positions

        monkeypatch.setattr(db, "init", lambda: True)
        monkeypatch.setattr(db, "get_all_configs_strict", lambda: {})
        monkeypatch.setattr(positions, "known_uids", lambda: [])
        monkeypatch.setattr(order_journal, "known_uids", lambda: ["intent-only"])
        monkeypatch.setattr(positions, "open_positions", lambda _uid: [])
        monkeypatch.setattr(intraday_positions, "open_positions", lambda _uid: [])
        monkeypatch.setattr(adaptive_edge_positions, "open_positions", lambda _uid: [])
        monkeypatch.setattr(gamma_move_positions, "open_positions", lambda _uid: [])

        intent = SimpleNamespace(
            exchange="NFO",
            symbol="NIFTY26OCT25000CE",
            payload={"product": "NRML"},
        )
        monkeypatch.setattr(
            order_journal,
            "unresolved",
            lambda uid, account_id=None: [intent]
            if uid == "intent-only" and account_id == "acct-1"
            else [],
        )

        known = external_positions._known_symbols("acct-1")

        assert known == {("NFO", "NRML", "NIFTY26OCT25000CE")}


    def test_position_owned_by_another_account_is_not_attributed(
        self, monkeypatch
    ):
        from types import SimpleNamespace

        from app.services import (
            adaptive_edge_positions,
            db,
            external_positions,
            gamma_move_positions,
            intraday_positions,
        )
        from app.services.kite_engine import order_journal, positions

        monkeypatch.setattr(db, "init", lambda: True)
        monkeypatch.setattr(
            db, "get_all_configs_strict", lambda: {"intraday_positions_legacy": "[]"}
        )
        monkeypatch.setattr(positions, "known_uids", lambda: [])
        monkeypatch.setattr(order_journal, "known_uids", lambda: [])
        monkeypatch.setattr(order_journal, "unresolved", lambda *_a, **_k: [])
        monkeypatch.setattr(positions, "open_positions", lambda _uid: [])
        monkeypatch.setattr(
            intraday_positions,
            "open_positions",
            lambda _uid: [
                SimpleNamespace(
                    account_id="acct-other",
                    contract=SimpleNamespace(
                        exchange="NFO", tradingsymbol="NOT-OURS"
                    ),
                )
            ],
        )
        monkeypatch.setattr(adaptive_edge_positions, "open_positions", lambda _uid: [])
        monkeypatch.setattr(gamma_move_positions, "open_positions", lambda _uid: [])

        assert external_positions._known_symbols("acct-1") == set()


class TestUnknownIsNeverFlat:
    def test_unreadable_local_state_refuses_to_classify_anything(self):
        # If Sterling cannot say what it knows, no broker position can be
        # attributed OR excluded.
        exposure = classify_broker_positions(
            [_broker_row()], account=ACCOUNT, known_symbols=None)
        assert exposure.readable is False
        assert exposure.flat is None
        assert exposure.count is None

    @pytest.mark.asyncio
    async def test_missing_broker_account_identity_is_unknown(self, monkeypatch):
        class _Client:
            async def get_positions_raw(self):
                return {"net": [_broker_row()]}

        monkeypatch.setattr(
            "app.services.external_positions._known_symbols",
            lambda account_id: set(),
        )
        exposure = await external_exposure(_Client())
        assert exposure.readable is False
        assert exposure.flat is None
        assert "account identity" in exposure.detail

    @pytest.mark.asyncio
    async def test_an_unreachable_broker_is_unknown_not_flat(self):
        class _Client:
            async def get_positions_raw(self):
                raise TimeoutError()

        exposure = await external_exposure(_Client(), account=ACCOUNT)
        assert exposure.flat is None
        # A bare TimeoutError stringifies to nothing; the type must survive.
        assert "TimeoutError" in exposure.detail

    @pytest.mark.asyncio
    async def test_no_live_account_is_unknown_not_flat(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.exchanges.kite.accounts.all_accounts", lambda: [])
        monkeypatch.setattr(
            "app.services.exchanges.kite.accounts.bootstrap", lambda: None)
        exposure = await external_exposure()
        assert exposure.flat is None

    def test_the_rendering_says_unknown_rather_than_none(self):
        text = render_external(ExternalExposure(readable=False, detail="gateway down"))
        assert "UNKNOWN" in text and "gateway down" in text


class TestItReachesTheGates:
    def test_the_snapshot_total_includes_external_exposure(self, monkeypatch):
        from app.services import exposure_snapshot as module

        monkeypatch.setattr("app.services.db.init", lambda *a, **k: True)
        monkeypatch.setattr("app.services.kite_engine.positions.known_uids", lambda: [])
        monkeypatch.setattr(module, "_external",
                            lambda: (1, "", ("CDSL26SEP1500CE",), None))
        snapshot = module.exposure_snapshot()
        assert snapshot.total == 1

    def test_an_unknown_broker_makes_the_total_unknown(self, monkeypatch):
        from app.services import exposure_snapshot as module

        monkeypatch.setattr("app.services.db.init", lambda *a, **k: True)
        monkeypatch.setattr("app.services.kite_engine.positions.known_uids", lambda: [])
        monkeypatch.setattr(module, "_external", lambda: (None, "timed out", (), None))
        snapshot = module.exposure_snapshot()
        assert snapshot.total is None

    def test_the_certification_gate_names_the_external_position(self, monkeypatch):
        from app.services import exposure_snapshot as module
        from app.services.release_certification import _open_exposure_gate

        snapshot = module.ExposureSnapshot(
            unresolved_intents=0, open_positions=0, external_positions=1,
            external_instruments=("CDSL26SEP1500CE",))
        gate = _open_exposure_gate(snapshot)
        assert gate.status == "FAIL"
        assert "CDSL26SEP1500CE" in gate.detail
        # The blocker now covers unmanaged positions AND pending broker automation
        # (orders/GTT/MF/ATO), so it says "EXTERNAL", not "positions Sterling did not open".
        assert "EXTERNAL broker exposure" in gate.detail

    def test_the_start_sequence_will_not_reach_clean(self, monkeypatch):
        from app.services import exposure_snapshot as module
        from app.services import service_lifecycle

        monkeypatch.setattr(
            service_lifecycle, "_set_recovery",
            lambda *a, **k: pytest.fail("recovery was cleared with exposure open"))
        monkeypatch.setattr(
            "app.services.exposure_snapshot.exposure_snapshot",
            lambda **k: module.ExposureSnapshot(
                unresolved_intents=0, open_positions=0, external_positions=1,
                external_instruments=("CDSL26SEP1500CE",)))
        result = service_lifecycle._step_clear_recovery()
        assert result.ok is False
        assert "the broker holds" in result.detail

    def test_an_unknown_exposure_also_blocks_clean(self, monkeypatch):
        from app.services import exposure_snapshot as module
        from app.services import service_lifecycle

        monkeypatch.setattr(
            service_lifecycle, "_set_recovery",
            lambda *a, **k: pytest.fail("recovery was cleared on an unknown exposure"))
        monkeypatch.setattr(
            "app.services.exposure_snapshot.exposure_snapshot",
            lambda **k: module.ExposureSnapshot(
                unresolved_intents=0, open_positions=0, external_positions=None,
                external_detail="broker timed out"))
        result = service_lifecycle._step_clear_recovery()
        assert result.ok is None
        assert "broker timed out" in result.detail

    def test_the_digest_puts_it_above_every_procedural_state(self):
        from app.services.daily_digest import Digest, _next_action

        digest = Digest(generated_at="now", recovery="FAIL: RECOVERY_REQUIRED",
                        external_exposure="1 NOT MANAGED BY STERLING: CDSL26SEP1500CE")
        # Procedural states cost time; an unwatched live position costs money
        # while the operator is reading.
        assert "Sterling did not open" in _next_action(digest)


class TestTheClientIsNotSharedBetweenLoops:
    def test_the_cache_identity_includes_the_event_loop(self):
        import inspect

        from app.services.exchanges.kite import accounts

        source = inspect.getsource(accounts.acquire_client)
        # A client cached from a closed loop fails with "Event loop is closed",
        # which a flatness check would read as "the broker is unreachable".
        assert "_running_loop_id()" in source


class TestItBlocksNewRiskAdmission:
    """An unexplained broker position means the account's total risk is unknown.

    Admission reads a recorded observation rather than calling the broker: a
    safety check that needs the network fails exactly when the network does,
    and this one sits on the path of every order decision.
    """

    @pytest.fixture()
    def recorded(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STERLING_EXTERNAL_EXPOSURE_FILE",
                           str(tmp_path / "external_exposure.json"))
        monkeypatch.setattr(
            "app.services.external_positions._live_account_configured", lambda: True)
        monkeypatch.setattr(
            "app.services.external_positions.live_account_ids",
            lambda: ("acct-live",),
        )
        return tmp_path / "external_exposure.json"

    def _write(self, path, **overrides):
        import json
        from datetime import datetime, timezone

        record = {"observed_at": datetime.now(timezone.utc).isoformat(),
                  "readable": True, "count": 0, "accounts": ["acct-live"],
                  "instruments": [], "detail": ""}
        record.update(overrides)
        path.write_text(json.dumps(record), encoding="utf-8")

    def test_an_external_position_blocks_an_exposure_increase(self, recorded):
        from app.services.external_positions import admission_blocker

        self._write(recorded, count=1, instruments=["CDSL26SEP1500CE"])
        code, reason = admission_blocker()
        assert code == "external_broker_exposure"
        assert "CDSL26SEP1500CE" in reason

    def test_pending_broker_automation_blocks_an_exposure_increase(self, recorded):
        from app.services.external_positions import admission_blocker

        self._write(recorded, automation=["order:OPEN-1:OPEN"])
        code, reason = admission_blocker()

        assert code == "external_broker_automation"
        assert "OPEN-1" in reason

    def test_an_unreadable_broker_answer_also_blocks(self, recorded):
        from app.services.external_positions import admission_blocker

        self._write(recorded, readable=False, count=None, detail="ConnectTimeout")
        code, reason = admission_blocker()
        # "We could not ask" is not "there is nothing there".
        assert code == "external_exposure_unreadable"
        assert "ConnectTimeout" in reason

    def test_never_having_looked_blocks_on_a_live_host(self, recorded):
        from app.services.external_positions import admission_blocker

        assert admission_blocker()[0] == "external_exposure_never_observed"

    def test_a_stale_observation_blocks(self, recorded):
        from datetime import datetime, timedelta, timezone

        from app.services.external_positions import admission_blocker

        old = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        self._write(recorded, observed_at=old)
        # Reconciliation itself expires after 60 seconds; admission must not
        # keep trusting yesterday's (or even two-minutes-ago) flat account.
        assert admission_blocker()[0] == "external_exposure_stale"

    def test_a_future_dated_observation_blocks(self, recorded):
        from datetime import datetime, timedelta, timezone

        from app.services.external_positions import admission_blocker

        future = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
        self._write(recorded, observed_at=future)

        code, reason = admission_blocker()

        assert code == "external_exposure_unreadable"
        assert "future" in reason

    def test_a_recent_flat_observation_permits(self, recorded):
        from app.services.external_positions import admission_blocker

        self._write(recorded)
        assert admission_blocker() == ("", "")

    def test_recent_flat_observation_from_another_account_blocks(
        self, recorded
    ):
        from app.services.external_positions import admission_blocker

        self._write(recorded, accounts=["different-account"])
        code, reason = admission_blocker()

        assert code == "external_exposure_account_identity"
        assert "different live-account set" in reason

    def test_recent_old_format_observation_without_account_identity_blocks(
        self, recorded
    ):
        from app.services.external_positions import admission_blocker

        self._write(recorded, accounts=[])
        code, reason = admission_blocker()

        assert code == "external_exposure_account_identity"
        assert "no account identity" in reason

    def test_a_host_with_no_live_account_is_not_blocked(self, tmp_path, monkeypatch):
        from app.services.external_positions import admission_blocker

        monkeypatch.setenv("STERLING_EXTERNAL_EXPOSURE_FILE",
                           str(tmp_path / "nothing.json"))
        monkeypatch.setattr(
            "app.services.external_positions._live_account_configured", lambda: False)
        # No broker account means no broker position is possible, so a missing
        # observation is not a gap.
        assert admission_blocker() == ("", "")


    def test_unreadable_live_account_registry_blocks_admission(
        self, tmp_path, monkeypatch
    ):
        from app.services.external_positions import admission_blocker

        monkeypatch.setenv(
            "STERLING_EXTERNAL_EXPOSURE_FILE",
            str(tmp_path / "nothing.json"),
        )

        def registry_unavailable():
            raise RuntimeError("account database unavailable")

        monkeypatch.setattr(
            "app.services.exchanges.kite.accounts.bootstrap",
            registry_unavailable,
        )

        code, reason = admission_blocker()

        assert code == "external_exposure_unreadable"
        assert "live broker account" in reason

    def test_a_corrupt_record_is_treated_as_never_observed(self, recorded):
        from app.services.external_positions import admission_blocker

        recorded.write_text("{ not json", encoding="utf-8")
        assert admission_blocker()[0] == "external_exposure_never_observed"

    def test_the_blocker_reaches_the_admission_verdict(self, recorded, monkeypatch):
        from app.services.safety_supervisor import SafetySupervisor

        self._write(recorded, count=1, instruments=["CDSL26SEP1500CE"])
        snapshot = SafetySupervisor().snapshot()
        assert "external_broker_exposure" in snapshot.blockers

    def test_a_reading_crash_blocks_rather_than_permits(self, monkeypatch):
        from app.services.safety_supervisor import SafetySupervisor

        def _boom():
            raise RuntimeError("store exploded")

        monkeypatch.setattr(
            "app.services.external_positions.admission_blocker", _boom)
        assert SafetySupervisor._external_exposure_blocker()[0] == \
            "external_exposure_unreadable"


class TestTheObservationIsRecordedWhereItIsRead:
    def test_reading_the_snapshot_records_what_the_broker_said(self, tmp_path, monkeypatch):
        from app.services import exposure_snapshot as module
        from app.services.external_positions import ExternalExposure, last_observation

        monkeypatch.setenv("STERLING_EXTERNAL_EXPOSURE_FILE",
                           str(tmp_path / "external_exposure.json"))
        monkeypatch.setattr("app.services.db.init", lambda *a, **k: True)
        monkeypatch.setattr("app.services.kite_engine.positions.known_uids", lambda: [])

        async def _exposure(*_a, **_k):
            return ExternalExposure(
                positions=(), accounts=("acct-live",), readable=True
            )

        monkeypatch.setattr("app.services.external_positions.external_exposure", _exposure)
        module.exposure_snapshot()

        record = last_observation()
        assert record is not None and record["readable"] is True
        assert record["accounts"] == ["acct-live"]
