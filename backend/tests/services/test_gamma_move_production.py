"""The production order path: durability, the safety gate, and broker truth.

These are the cases that only bite in production — a restart while holding, a
fill that came in worse than the limit, a retry after a timeout — and none of
them show up in a happy-path run.
"""
from __future__ import annotations

import pytest

from app.engines.gamma_move import (GammaMoveConfig, GammaSignal, InstrumentRef,
                                    PositionState, TradeRecord)
from app.services import gamma_move_positions as store
from tests.engines.gamma_move.conftest import BASE_MS


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("STERLING_DB_PATH", str(tmp_path / "t.db"))
    from app.services import db
    from app.services import gamma_move_runner as runner
    monkeypatch.setattr(db, "_DB_PATH", str(tmp_path / "t.db"), raising=False)
    db.init()
    from tests.conftest import reconciled_control_plane

    reconciled_control_plane()
    store.reset()
    runner.clear()
    yield
    store.reset()
    runner.clear()


def inst(symbol="RELIANCE26SEP1300CE") -> InstrumentRef:
    return InstrumentRef(instrument_id="12345", tradingsymbol=symbol, option_type="CE",
                         strike=1300.0, expiry="2026-09-29", lot_size=500, tick_size=0.05)


def pos(**kw) -> PositionState:
    base = dict(signal_id="s1", instrument=inst(), entry=53.0, stop=45.0, quantity=500,
                lots=1, entered_ms=1, entry_day="2026-09-20", order_id="o1",
                account_id="acct-gamma")
    base.update(kw)
    return PositionState(**base)


class TestEngineSwitchAuthority:
    def test_direct_scan_is_noop_when_engine_is_off(self, monkeypatch):
        import asyncio
        from app.services import gamma_move_runner as runner

        monkeypatch.setattr(
            runner, "get_config",
            lambda _uid=None: GammaMoveConfig(enabled=False).validate(),
        )
        out = asyncio.run(runner.scan_once("u1"))
        assert out["scanned"] == 0
        assert out["armed"] == 0
        assert "disabled" in out["message"]

    def test_direct_arm_is_refused_when_engine_is_off(self, monkeypatch):
        import asyncio
        from app.services import gamma_move_runner as runner

        monkeypatch.setattr(
            runner, "get_config",
            lambda _uid=None: GammaMoveConfig(enabled=False).validate(),
        )
        out = asyncio.run(runner.arm("u1", "stale-signal"))
        assert out["ok"] is False
        assert "switched off" in out["message"]


class TestReplayOwnsLive:
    def test_scan_once_is_a_no_op_while_replay_owns_the_board(self, monkeypatch):
        import asyncio
        from app.services import gamma_move_runner as runner
        monkeypatch.setattr(runner, "_replay_owns_the_board", lambda: True)
        out = asyncio.run(runner.scan_once("u1"))
        assert out["scanned"] == 0
        assert "replay" in out["message"]

    def test_arm_is_refused_while_replay_owns_the_board(self, monkeypatch):
        import asyncio
        from app.services import gamma_move_runner as runner
        monkeypatch.setattr(runner, "_replay_owns_the_board", lambda: True)
        out = asyncio.run(runner.arm("u1", "s1"))
        assert out["ok"] is False
        assert "replay" in out["message"]


class TestDurability:
    def test_a_position_survives_a_restart(self):
        """The whole point. A crash while long must not lose the position — the
        process that comes back would never exit something it cannot see."""
        store.put("u1", pos())
        store.reset()                              # simulate the restart
        back = store.get("u1", "RELIANCE26SEP1300CE")
        assert back is not None
        assert back.entry == 53.0 and back.stop == 45.0 and back.quantity == 500
        assert back.instrument.tradingsymbol == "RELIANCE26SEP1300CE"

    def test_open_positions_excludes_closed_ones(self):
        store.put("u1", pos())
        assert len(store.open_positions("u1")) == 1
        store.close("u1", "RELIANCE26SEP1300CE", "stop")
        assert store.open_positions("u1") == []

    def test_one_unreadable_row_fails_the_registry_closed(self, monkeypatch):
        store.put("u1", pos())
        store.put("u1", pos(instrument=inst("OTHER26SEP1CE")))
        import json
        from app.services import db
        raw = json.loads(db.get_config("gamma_move_positions_u1"))
        raw.append({"garbage": True})
        db.set_config("gamma_move_positions_u1", json.dumps(raw))
        store.reset()
        # Skipping the bad row would assert that the position it described does not
        # exist, which frees its guard and leaves any broker-side protection
        # unmanaged. The registry refuses to load instead, naming the row.
        with pytest.raises(ValueError, match="invalid_gamma_move_position_row:2"):
            store.load("u1")


class TestBrokerTruth:
    def test_a_sent_order_is_not_yet_a_position(self):
        p = store.put("u1", pos())
        assert p.status == "pending"
        assert p.is_open                          # tracked, but not confirmed

    def test_a_worse_fill_moves_the_stop_with_it(self):
        """The stop was sized against the intended entry. Leaving it where it was
        silently widens the risk past what the sizer allowed."""
        store.put("u1", pos(entry=53.0, stop=45.0))
        p = store.mark_filled("u1", "RELIANCE26SEP1300CE", 56.0)
        assert p.fill_price == 56.0
        assert p.stop == 48.0                      # +3 drift carried onto the stop
        assert p.effective_entry == 56.0
        assert p.status == "open"

    def test_effective_entry_falls_back_to_the_intended_price(self):
        assert store.put("u1", pos()).effective_entry == 53.0

    def test_a_rejection_is_recorded_not_silently_dropped(self):
        store.put("u1", pos())
        p = store.mark_rejected("u1", "RELIANCE26SEP1300CE", "REJECTED")
        assert p.status == "rejected" and not p.is_open


class TestSafetyGate:
    def test_the_kill_switch_stops_an_entry(self, monkeypatch):
        from app.services import gamma_move_runner as runner
        monkeypatch.setattr("app.services.live_safety.kill_switch_state",
                            lambda: {"enabled": True, "reason": "manual halt"})
        ok, why = runner._safety("u1", "key-1")
        assert ok is False and "Kill switch" in why

    def test_it_fails_closed_when_it_cannot_be_evaluated(self, monkeypatch):
        """An unavailable safety check is not a passed one."""
        from app.services import gamma_move_runner as runner
        def boom(*a, **k):
            raise RuntimeError("safety subsystem down")
        monkeypatch.setattr("app.services.live_safety.assert_safe_to_trade", boom)
        ok, why = runner._safety("u1", "key-1")
        assert ok is False and "unavailable" in why

    def test_a_duplicate_key_is_refused(self, monkeypatch):
        from app.services import gamma_move_runner as runner
        monkeypatch.setattr("app.services.live_safety.check_idempotency",
                            lambda key: "ORDER-1")
        ok, why = runner._safety("u1", "key-1")
        assert ok is False and "Duplicate" in why


class TestModeIsReadNotStored:
    def test_config_carries_no_mode_fields(self):
        names = GammaMoveConfig.field_names()
        assert "execution_mode" not in names
        assert "protection_mode" not in names

    def test_unknown_account_mode_stays_unknown_and_manual(self):
        from app.services import gamma_move_runner as runner
        assert runner.is_paper("nobody") is None
        assert runner.auto_execute("nobody") is False

    def test_is_paper_follows_the_account(self, monkeypatch):
        from app.services import gamma_move_runner as runner

        class Acct:
            is_paper = False
        monkeypatch.setattr("app.services.exchanges.kite.accounts.get_active",
                            lambda uid: Acct())
        assert runner.is_paper("u1") is False


class TestAutoExecuteAuthority:
    @pytest.mark.parametrize(
        ("master", "strategy", "expected"),
        [
            (False, False, False),
            (False, True, False),
            (True, False, False),
            (True, True, True),
        ],
    )
    def test_requires_master_and_strategy_switch(
        self, monkeypatch, master, strategy, expected
    ):
        from types import SimpleNamespace
        from app.services import gamma_move_runner as runner
        from app.services.kite_engine import state as engine_state

        monkeypatch.setattr(
            engine_state, "get_config", lambda _uid: SimpleNamespace(auto_execute=master)
        )
        monkeypatch.setattr(
            runner,
            "get_config",
            lambda _uid=None: GammaMoveConfig(auto_execute=strategy).validate(),
        )
        assert runner.auto_execute("u1") is expected

    def test_config_failure_fails_closed(self, monkeypatch):
        from types import SimpleNamespace
        from app.services import gamma_move_runner as runner
        from app.services.kite_engine import state as engine_state

        monkeypatch.setattr(
            engine_state, "get_config", lambda _uid: SimpleNamespace(auto_execute=True)
        )
        monkeypatch.setattr(
            runner,
            "get_config",
            lambda _uid=None: (_ for _ in ()).throw(RuntimeError("db down")),
        )
        assert runner.auto_execute("u1") is False


class TestDefaultsDoNotOverrideIntent:
    """Changing a default must not flip a config someone deliberately set.

    `enabled` moved from off to on. An operator who had turned this engine off
    must find it still off after that change — a default is what applies when
    nobody has said otherwise, not a way to overrule them on upgrade.
    """

    def test_a_stored_off_state_survives_the_new_default(self):
        import json
        from app.services import db
        from app.services.gamma_move import get_config
        db.set_config("gamma_move_config", json.dumps({"enabled": False}))
        assert get_config().enabled is False

    def test_fields_never_stored_still_take_the_current_default(self):
        import json
        from app.services import db
        from app.services.gamma_move import get_config
        db.set_config("gamma_move_config", json.dumps({"enabled": False}))
        cfg = get_config()
        assert cfg.stop_mode == "both"
        assert cfg.scan_all_stocks is True

    def test_an_unset_config_is_on(self):
        from app.services import db
        from app.services.gamma_move import get_config
        db.set_config("gamma_move_config", "")
        assert get_config().enabled is True

    def test_an_unreadable_config_falls_back_OFF(self):
        """The one case where a fallback should overrule the default: a config
        that will not validate must not become a trading config."""
        from app.services import db
        from app.services.gamma_move import get_config
        db.set_config("gamma_move_config", "{not json at all")
        assert get_config().enabled is False

    def test_an_invalid_stored_config_falls_back_OFF(self):
        import json
        from app.services import db
        from app.services.gamma_move import get_config
        db.set_config("gamma_move_config", json.dumps({"min_oi_drop_pct": 0}))
        assert get_config().enabled is False


class TestReconcile:
    @pytest.mark.asyncio
    async def test_reconcile_handles_list_of_account_positions(self, monkeypatch):
        from app.services import gamma_move_runner
        from app.schemas.account import AccountPosition

        class MockClient:
            async def get_positions(self):
                return [
                    AccountPosition(
                        symbol="NIFTY26SEP25000CE",
                        underlying="NFO:NIFTY",
                        size=50.0,
                        side="long",
                        entry_price=120.0,
                        mark_price=130.0,
                        unrealized_pnl=500.0,
                        realized_pnl=0.0,
                        margin=6000.0,
                        position_type="MIS",
                    )
                ]

        async def _mock_client(uid):
            return MockClient()

        monkeypatch.setattr(gamma_move_runner, "_client", _mock_client)
        monkeypatch.setattr(gamma_move_runner, "is_paper", lambda uid: True)

        res = await gamma_move_runner.reconcile("default")
        assert res is not None

        orphans = await gamma_move_runner.orphan_positions("default", gamma_move_runner.get_config())
        assert len(orphans) == 1
        assert orphans[0]["symbol"] == "NIFTY26SEP25000CE"
        assert orphans[0]["quantity"] == 50
        assert orphans[0]["entry_price"] == 120.0


    @pytest.mark.asyncio
    async def test_reconcile_resizes_existing_gtt_to_confirmed_quantity(self, monkeypatch):
        from app.services import gamma_move_runner

        p = pos(
            status=store.OPEN,
            fill_price=53.0,
            gtt_id=99,
            gtt_at=45.0,
            gtt_qty=250,
            account_id="acct-gamma",
        )
        store.put("default", p)
        moved = []

        class MockClient:
            _is_paper = True
            _account_id = "acct-gamma"

            async def get_positions(self):
                return {"net": [{
                    "tradingsymbol": p.instrument.tradingsymbol,
                    "exchange": p.instrument.exchange,
                    "product": "MIS",
                    "quantity": 500,
                }]}

            async def modify_gtt(self, trigger_id, **kwargs):
                moved.append({"trigger_id": trigger_id, **kwargs})
                return {"trigger_id": trigger_id}

        async def _mock_client(_uid):
            return MockClient()

        monkeypatch.setattr(gamma_move_runner, "_client", _mock_client)
        result = await gamma_move_runner.reconcile("default")

        current = store.get("default", p.instrument.tradingsymbol)
        assert result["reprotected"] == 1
        assert current.gtt_id == 99
        assert current.gtt_qty == 500
        assert moved[-1]["orders"][0]["quantity"] == 500


    @pytest.mark.asyncio
    async def test_reconcile_keeps_foreign_account_position_durable_but_out_of_active_session(
        self, monkeypatch
    ):
        from app.services import gamma_move_runner

        foreign = pos(
            status=store.OPEN,
            fill_price=53.0,
            account_id="acct-other",
        )
        store.put("default", foreign)
        session = gamma_move_runner.session_for("default", GammaMoveConfig())
        session.strategy.state.positions[foreign.instrument.tradingsymbol] = foreign

        class MockClient:
            _is_paper = False
            _account_id = "acct-current"

            async def get_positions(self):
                return {"net": []}

        async def _mock_client(uid):
            return MockClient()

        monkeypatch.setattr(gamma_move_runner, "_client", _mock_client)
        result = await gamma_move_runner.reconcile("default")

        assert result["ownership_errors"] == [foreign.instrument.tradingsymbol]
        assert store.get("default", foreign.instrument.tradingsymbol).is_open
        assert foreign.instrument.tradingsymbol not in session.strategy.state.positions


    @pytest.mark.asyncio
    async def test_orphan_discovery_ignores_same_symbol_nrml_bucket(self, monkeypatch):
        from app.services import gamma_move_runner

        class MockClient:
            async def get_positions(self):
                return {"net": [{
                    "tradingsymbol": "NIFTY26SEP25000CE",
                    "exchange": "NFO",
                    "product": "NRML",
                    "quantity": 50,
                    "average_price": 120.0,
                }]}

        async def _mock_client(uid):
            return MockClient()

        monkeypatch.setattr(gamma_move_runner, "_client", _mock_client)
        orphans = await gamma_move_runner.orphan_positions(
            "default", gamma_move_runner.get_config()
        )
        assert orphans == []


class TestSessionReuse:
    def test_session_for_reuses_the_persisted_record_across_midnight(self, monkeypatch):
        """A new Session at midnight would drop descale and the daily loss tally."""
        from app.services import gamma_move_runner as runner
        rec = TradeRecord(descale_step=2, trades=6, losses=6,
                          day="2026-09-08", day_realised_inr=-4000.0)
        store.save_record("u1", rec)
        monkeypatch.setattr(runner, "_today_str", lambda: "2026-09-08")
        s1 = runner.session_for("u1", GammaMoveConfig())
        assert s1.strategy.state.record.descale_step == 2
        monkeypatch.setattr(runner, "_today_str", lambda: "2026-09-09")
        s2 = runner.session_for("u1", GammaMoveConfig())
        assert s2 is s1
        assert s2.strategy.state.record is s1.strategy.state.record
        assert s2.strategy.state.record.descale_step == 2
        assert s2.strategy.state.day == "2026-09-09"

    def test_load_record_rebuilds_descale_from_dict(self):
        rec = TradeRecord.from_dict({
            "descale_step": 2, "trades": 6, "losses": 6,
            "day": "2026-09-08", "day_realised_inr": -4000,
        })
        assert rec.descale_step == 2
        assert rec.descaled is True
        store.save_record("u1", rec)
        store.reset()
        back = store.load_record("u1")
        assert back.descale_step == 2
        assert back.descaled is True
        assert back.day == "2026-09-08"


class TestLiveSpreadGate:
    def test_spread_of_3_1_percent_is_refused(self):
        from app.services import gamma_move_runner as runner
        cfg = GammaMoveConfig()
        wide = {"depth": {"buy": [{"price": 100.0}], "sell": [{"price": 103.15}]}}
        msg = runner.spread_blocks_entry(wide, cfg)
        assert msg is not None
        assert "spread" in msg.lower()

    def test_spread_inside_3_percent_is_allowed(self):
        from app.services import gamma_move_runner as runner
        cfg = GammaMoveConfig()
        tight = {"depth": {"buy": [{"price": 100.0}], "sell": [{"price": 102.9}]}}
        assert runner.spread_blocks_entry(tight, cfg) is None

    def test_missing_depth_is_refused_when_spread_gate_is_enabled(self):
        from app.services import gamma_move_runner as runner
        msg = runner.spread_blocks_entry({"last_price": 101.0}, GammaMoveConfig())
        assert msg is not None
        assert "depth" in msg.lower() or "spread unavailable" in msg.lower()

    def test_nonpositive_spread_cap_explicitly_disables_the_gate(self):
        from app.services import gamma_move_runner as runner
        cfg = GammaMoveConfig(max_spread_pct=0.0)
        assert runner.spread_blocks_entry({}, cfg) is None

    @pytest.mark.asyncio
    async def test_arm_refuses_a_wide_live_quote(self, monkeypatch):
        from app.engines.gamma_move import SpotLevel, StrikeCandidate
        from app.services import gamma_move_runner as runner

        cfg = GammaMoveConfig()
        instrument = inst()
        cand = StrikeCandidate(
            underlying="RELIANCE",
            level=SpotLevel(price=1300.0, kind="resistance", touches=3),
            instrument=instrument, oi=6_000_000, days_to_expiry=9, spot=1298.0,
            premium=53.0, chain_oi_max=6_000_000)
        sig = GammaSignal(id="s-wide", candidate=cand, metrics=None, state="armed",
                          at_ms=BASE_MS, regime="up", entry=53.0, stop=45.0,
                          lots=1, quantity=500)
        session = runner.session_for("u1", cfg)
        session.signals[sig.id] = sig

        class Client:
            async def get_quote(self, keys):
                return {"NFO:RELIANCE26SEP1300CE": {
                    "depth": {"buy": [{"price": 50.0}], "sell": [{"price": 52.0}]},
                }}

            async def place_order(self, *a, **k):
                raise AssertionError("must not place when the quote is wide")

        async def _client(_uid):
            return Client()

        monkeypatch.setattr(runner, "_client", _client)
        result = await runner.arm("u1", sig.id)
        assert result["ok"] is False
        assert "spread" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_arm_routes_entry_through_canonical_execution(self, monkeypatch):
        from app.engines.gamma_move import SpotLevel, StrikeCandidate
        from app.services import canonical_fill_reconciliation, canonical_strategy_entry
        from app.services import gamma_move_runner as runner
        from app.services.execution_service import ExecutionResult

        monkeypatch.setattr(runner, "_today_str", lambda: "2026-10-03")
        cfg = GammaMoveConfig()
        instrument = inst()
        cand = StrikeCandidate(
            underlying="RELIANCE",
            level=SpotLevel(price=1300.0, kind="resistance", touches=3),
            instrument=instrument,
            oi=6_000_000,
            days_to_expiry=9,
            spot=1298.0,
            premium=53.0,
            chain_oi_max=6_000_000,
        )
        sig = GammaSignal(
            id="s-canonical",
            candidate=cand,
            metrics=None,
            state="armed",
            at_ms=BASE_MS,
            regime="up",
            entry=53.0,
            stop=45.0,
            lots=1,
            quantity=500,
        )
        session = runner.session_for("u1", cfg)
        session.signals[sig.id] = sig

        class PaperClient:
            _is_paper = True
            _account_id = "acct-gamma-paper"

            async def get_quote(self, keys):
                return {
                    "NFO:RELIANCE26SEP1300CE": {
                        "depth": {
                            "buy": [{"price": 52.5}],
                            "sell": [{"price": 53.0}],
                        },
                    }
                }

        client = PaperClient()
        seen = {}

        async def canonical_submit(**kwargs):
            seen.update(kwargs)
            return ExecutionResult(
                success=True,
                status="ACKNOWLEDGED",
                order_id="PAPER-GAMMA-1",
            )

        async def _client(_uid):
            return client

        monkeypatch.setattr(runner, "_client", _client)
        monkeypatch.setattr(runner, "_safety", lambda *_args, **_kwargs: (True, ""))
        async def resolve_fill(**kwargs):
            expected = int(kwargs["expected_quantity"])
            return canonical_fill_reconciliation.FillResolution(
                order_id="PAPER-GAMMA-1",
                intent_key="test-intent",
                status="COMPLETE",
                filled_quantity=expected,
                average_price=float(kwargs["reference_price"]),
                terminal=True,
                settled=True,
                reconciliation_required=False,
                projection_version=0,
                expected_quantity=expected,
            )

        monkeypatch.setattr(canonical_strategy_entry, "submit_entry", canonical_submit)
        monkeypatch.setattr(canonical_fill_reconciliation, "resolve_entry_fill", resolve_fill)
        monkeypatch.setattr(
            runner,
            "_place_protection",
            lambda *_args, **_kwargs: _async_value(0),
        )
        monkeypatch.setattr(
            runner,
            "_subscribe_watched",
            lambda *_args, **_kwargs: _async_value(None),
        )

        result = await runner.arm("u1", sig.id)

        assert result["ok"] is True
        assert seen["strategy_id"] == "gamma_move"
        assert seen["signal_id"] == f"{sig.id}:2026-10-03"
        assert seen["client"] is client


@pytest.mark.asyncio
class TestLegacyExitBrokerTruth:
    class ExitClient:
        _is_paper = False
        _account_id = "acct-gamma"

        def __init__(self, quantity: int):
            self.quantity = quantity
            self.orders = []

        async def get_positions(self):
            return {
                "net": [
                    {
                        "tradingsymbol": "RELIANCE26SEP1300CE",
                        "exchange": "NFO",
                        "product": "MIS",
                        "quantity": self.quantity,
                    }
                ] if self.quantity else []
            }

        async def place_order(self, symbol, side, qty, **kwargs):
            self.orders.append((symbol, side, qty, kwargs))
            return {"order_id": "EXIT-1"}

        async def get_order_history(self, order_id):
            return [{
                "status": "COMPLETE",
                "filled_quantity": self.quantity,
                "average_price": 40.0,
            }]

        async def get_order_trades(self, order_id):
            return []

    def _held(self, runner):
        p = pos(status=store.OPEN, fill_price=53.0, gtt_id=99,
                gtt_at=45.0, gtt_qty=500)
        session = runner.session_for("u1", GammaMoveConfig())
        session.strategy.state.positions[p.instrument.tradingsymbol] = p
        store.put("u1", p)
        return session, p

    async def test_exit_refuses_foreign_account_even_with_same_mis_inventory(self, monkeypatch):
        from app.services import gamma_move_runner as runner

        session, p = self._held(runner)
        p.account_id = "other-account"
        store.put("u1", p)
        client = self.ExitClient(500)

        async def _client(_uid):
            return client

        monkeypatch.setattr(runner, "_client", _client)
        assert await runner._broker_long_quantity(client, p) is None

        result = await runner.on_ticks(
            "u1", [{"instrument_token": 12345, "last_price": 40.0}]
        )

        assert result == "watching"
        assert p.exiting is False
        assert p.gtt_id == 99
        assert client.orders == []


    async def test_exit_refuses_when_broker_quantity_is_missing(self, monkeypatch):
        from app.services import gamma_move_runner as runner

        session, p = self._held(runner)
        client = self.ExitClient(0)

        async def _client(_uid):
            return client

        monkeypatch.setattr(runner, "_client", _client)
        result = await runner.on_ticks(
            "u1", [{"instrument_token": 12345, "last_price": 40.0}]
        )

        assert result == "watching"
        assert p.exiting is False
        assert p.gtt_id == 99
        assert client.orders == []

    async def test_active_gtt_blocks_a_second_sell(self, monkeypatch):
        from app.services import gamma_move_runner as runner
        from app.services.kite_engine import protective_stop as pstop

        session, p = self._held(runner)
        client = self.ExitClient(500)

        async def _client(_uid):
            return client

        async def gone(*_args, **_kwargs):
            return pstop.GONE

        async def active(*_args, **_kwargs):
            return pstop.STOP_ACTIVE

        monkeypatch.setattr(runner, "_client", _client)
        monkeypatch.setattr(pstop, "cancel_stop_result", gone)
        monkeypatch.setattr(pstop, "stop_status", active)

        result = await runner.on_ticks(
            "u1", [{"instrument_token": 12345, "last_price": 40.0}]
        )

        assert result == "watching"
        assert p.exiting is False
        assert p.gtt_id == 99
        assert client.orders == []

    async def test_proven_absent_gtt_allows_gamma_exit(self, monkeypatch):
        from app.services import gamma_move_runner as runner
        from app.services.kite_engine import protective_stop as pstop

        session, p = self._held(runner)
        client = self.ExitClient(500)

        async def _client(_uid):
            return client

        async def gone(*_args, **_kwargs):
            return pstop.GONE

        async def absent(*_args, **_kwargs):
            return pstop.STOP_ABSENT

        monkeypatch.setattr(runner, "_client", _client)
        monkeypatch.setattr(pstop, "cancel_stop_result", gone)
        monkeypatch.setattr(pstop, "stop_status", absent)

        result = await runner.on_ticks(
            "u1", [{"instrument_token": 12345, "last_price": 40.0}]
        )

        assert result == "exited"
        assert client.orders and client.orders[0][1] == "sell"
        assert p.instrument.tradingsymbol not in session.strategy.state.positions


@pytest.mark.asyncio
class TestAdoptBrokerTruth:
    @staticmethod
    def _prime(runner):
        from app.engines.gamma_move import SpotLevel, StrikeCandidate

        instrument = inst()
        candidate = StrikeCandidate(
            underlying="RELIANCE",
            level=SpotLevel(price=1300.0, kind="resistance", touches=3),
            instrument=instrument,
            oi=6_000_000,
            days_to_expiry=9,
            spot=1298.0,
            premium=53.0,
            chain_oi_max=6_000_000,
        )
        signal = GammaSignal(
            id="s-adopt",
            candidate=candidate,
            metrics=None,
            state="armed",
            at_ms=BASE_MS,
            regime="up",
            entry=53.0,
            stop=45.0,
            lots=1,
            quantity=500,
        )
        runner.session_for("u1", GammaMoveConfig()).signals[signal.id] = signal
        return signal

    async def test_live_adopt_refuses_missing_broker_inventory(self, monkeypatch):
        from app.services import gamma_move_runner as runner

        signal = self._prime(runner)

        class Client:
            _is_paper = False
            _account_id = "acct-gamma"

            async def get_positions(self):
                return {"net": []}

        async def _client(_uid):
            return Client()

        called = []

        async def should_not_protect(*_args, **_kwargs):
            called.append(True)
            return 88

        monkeypatch.setattr(runner, "_client", _client)
        monkeypatch.setattr(runner, "_place_protection", should_not_protect)

        result = await runner.adopt(
            "u1", signal.candidate.instrument.tradingsymbol, 500, 53.0
        )

        assert result["ok"] is False
        assert "broker quantity" in result["message"]
        assert store.get("u1", signal.candidate.instrument.tradingsymbol) is None
        assert called == []

    async def test_paper_adopt_remains_available(self, monkeypatch):
        from app.services import gamma_move_runner as runner

        signal = self._prime(runner)

        class Client:
            _is_paper = True
            _account_id = "paper-gamma"

        async def _client(_uid):
            return Client()

        monkeypatch.setattr(runner, "_client", _client)
        async def protected(*_args, **_kwargs):
            return 88

        monkeypatch.setattr(runner, "_place_protection", protected)

        result = await runner.adopt(
            "u1", signal.candidate.instrument.tradingsymbol, 500, 53.0
        )

        assert result["ok"] is True
        assert result["gtt_id"] == 88
        assert store.get("u1", signal.candidate.instrument.tradingsymbol) is not None


class TestSignalPersistenceAndHydration:
    @pytest.mark.asyncio
    async def test_signals_persisted_and_hydrated_across_restarts(self, monkeypatch):
        from app.engines.gamma_move import SpotLevel, StrikeCandidate
        from app.services import gamma_move_runner as runner

        instrument = inst()
        cand = StrikeCandidate(
            underlying="RELIANCE",
            level=SpotLevel(price=1300.0, kind="resistance", touches=3),
            instrument=instrument, oi=6_000_000, days_to_expiry=9, spot=1298.0,
            premium=53.0, chain_oi_max=6_000_000)
        sig = GammaSignal(id="s-persist-1", candidate=cand, metrics=None, state="armed",
                          at_ms=BASE_MS, regime="up", entry=53.0, stop=45.0,
                          lots=1, quantity=500)

        async def fake_scan(_uid, _cfg, _strategy):
            return [sig]

        async def _fake_sub(*a, **k):
            pass

        async def _fake_dump(_uid):
            return []

        from app.services import gamma_move_scanner, gamma_move
        monkeypatch.setattr(gamma_move_scanner, "scan_once", fake_scan)
        monkeypatch.setattr(runner, "_subscribe_watched", _fake_sub)
        monkeypatch.setattr(gamma_move, "nfo_dump", _fake_dump)

        res = await runner.scan_once("u1")
        assert res["scanned"] == 1
        assert res["signals"][0]["id"] == "s-persist-1"

        # Simulate full server restart / memory wipe
        runner.clear("u1")

        # Session hydration on restart
        session = runner.session_for("u1")
        assert "s-persist-1" in session.signals
        hydrated = session.signals["s-persist-1"]
        assert hydrated.id == "s-persist-1"
        assert hydrated.candidate.underlying == "RELIANCE"
        assert hydrated.candidate.oi == 6_000_000

        # Snapshot hydration on restart
        runner.clear("u1")
        from app.services.gamma_move import snapshot
        snap = await snapshot("u1")
        assert len(snap["candidates"]) == 1
        assert snap["candidates"][0]["id"] == "s-persist-1"




@pytest.mark.asyncio
async def test_disabled_gamma_still_reconciles_owned_positions(monkeypatch):
    from app.services import gamma_move_runner as runner

    cfg = GammaMoveConfig(enabled=False)
    calls = []
    monkeypatch.setattr(runner, "_kite_user_ids", lambda: ["u1"])
    monkeypatch.setattr(runner, "get_config", lambda _uid=None: cfg)
    monkeypatch.setattr(runner, "_is_market_open", lambda _cfg: True)
    monkeypatch.setattr(
        runner.positions_store,
        "open_positions",
        lambda _uid: [object()],
    )

    async def rec(uid):
        calls.append(uid)
        return {"restored": 1, "orphans": [], "vanished": []}

    async def forbidden_scan(_uid):
        raise AssertionError("disabled Gamma Move must not scan for new entries")

    monkeypatch.setattr(runner, "reconcile", rec)
    monkeypatch.setattr(runner, "scan_once", forbidden_scan)

    out = await runner.scan_all_once()

    assert calls == ["u1"]
    assert out["u1"] == "disabled; managing 1 open position(s)"


@pytest.mark.asyncio
async def test_scan_loop_enforces_session_end_exit_without_post_close_tick(
    monkeypatch,
):
    """The scheduler must enforce close_at_session_end without needing a tick."""
    from app.services import gamma_move_runner as runner

    cfg = GammaMoveConfig(
        enabled=True,
        close_at_session_end=True,
    ).validate()
    held = pos(
        status=store.OPEN,
        fill_price=53.0,
        gtt_id=99,
        gtt_at=45.0,
        gtt_qty=500,
    )
    store.put("u1", held)
    session = runner.session_for("u1", cfg)
    session.strategy.state.positions[held.instrument.tradingsymbol] = held

    class Client:
        _is_paper = True
        _account_id = "acct-gamma"

        async def get_quote(self, keys):
            return {keys[0]: {"last_price": 54.0}}

    async def client_for(_uid):
        return Client()

    async def reconcile(_uid):
        return {
            "restored": 1,
            "orphans": [],
            "vanished": [],
            "reprotected": 0,
            "ownership_errors": [],
        }

    exits = []

    async def execute_claimed(
        uid, _client, position, _cfg, current_session, *, price, reason
    ):
        exits.append((uid, position.instrument.tradingsymbol, price, reason))
        current_session.strategy.state.positions.pop(
            position.instrument.tradingsymbol, None
        )
        return True, "COMPLETE"

    async def subscribe(_uid, _session):
        return None

    monkeypatch.setattr(runner, "_replay_owns_the_board", lambda: False)
    monkeypatch.setattr(runner, "_kite_user_ids", lambda: ["u1"])
    monkeypatch.setattr(runner, "get_config", lambda _uid=None: cfg)
    monkeypatch.setattr(runner, "_is_market_open", lambda _cfg: False)
    monkeypatch.setattr(runner, "reconcile", reconcile)
    monkeypatch.setattr(runner, "_client", client_for)
    monkeypatch.setattr(runner, "_execute_claimed_exit", execute_claimed)
    monkeypatch.setattr(runner, "_subscribe_watched", subscribe)

    out = await runner.scan_all_once()

    assert exits == [
        ("u1", held.instrument.tradingsymbol, 54.0, "session_end")
    ]
    assert "session-end closed 1" in out["u1"]
    assert "blocked 0" in out["u1"]
