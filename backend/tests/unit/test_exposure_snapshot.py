"""The exposure read that three callers share, and the UNKNOWN it must keep.

Found by running the release certification against the live machine: every
caller got None from a command line, because the durable stores answer nothing
until `db.init()` has run in that process. The gate could never pass — not
because exposure was open, but because nobody had opened the database. That is
the worst kind of safety check: one that is always blocking for a reason
unrelated to safety, which is how people learn to override it.
"""
from __future__ import annotations

import pytest

from app.services import exposure_snapshot as module
from app.services.exposure_snapshot import exposure_snapshot, unresolved_exposure_count


class _Position:
    def __init__(self, symbol):
        self.symbol = symbol


@pytest.fixture()
def stores(monkeypatch):
    from app.services import (
        adaptive_edge_positions,
        db,
        gamma_move_positions,
        intraday_positions,
    )
    from app.services.kite_engine import order_journal, positions

    state = {
        "uids": ["family"],
        "journal_uids": [],
        "configs": {},
        "open": {},
        "intraday": {},
        "adaptive": {},
        "gamma": {},
        "unresolved": {},
    }
    monkeypatch.setattr("app.services.db.init", lambda *a, **k: True)
    # The broker is asked for positions Sterling did not open; these tests are
    # about the local side, so it answers "nothing external" rather than
    # UNKNOWN, which would correctly make every total None.
    monkeypatch.setattr(module, "_external", lambda: (0, "", (), None))
    monkeypatch.setattr(positions, "known_uids", lambda: state["uids"])
    monkeypatch.setattr(order_journal, "known_uids", lambda: state["journal_uids"])
    monkeypatch.setattr(db, "get_all_configs_strict", lambda: state["configs"])
    monkeypatch.setattr(positions, "open_positions",
                        lambda uid: state["open"].get(uid, []))
    monkeypatch.setattr(intraday_positions, "open_positions",
                        lambda uid: state["intraday"].get(uid, []))
    monkeypatch.setattr(adaptive_edge_positions, "open_positions",
                        lambda uid: state["adaptive"].get(uid, []))
    monkeypatch.setattr(gamma_move_positions, "open_positions",
                        lambda uid: state["gamma"].get(uid, []))
    monkeypatch.setattr(order_journal, "unresolved",
                        lambda uid: state["unresolved"].get(uid, []))
    return state


def test_a_flat_deployment_reads_zero_not_unknown(stores):
    snapshot = exposure_snapshot()
    assert snapshot.total == 0
    assert snapshot.unresolved_intents == 0 and snapshot.open_positions == 0


def test_open_positions_are_counted_and_named(stores):
    stores["open"]["family"] = [_Position("NIFTY26JAN24000CE")]
    snapshot = exposure_snapshot()
    assert snapshot.open_positions == 1
    assert snapshot.held == ("NIFTY26JAN24000CE",)


def test_every_operator_is_counted_not_just_the_first(stores):
    stores["uids"] = ["family", "second"]
    stores["open"] = {"family": [_Position("A")], "second": [_Position("B")]}
    assert exposure_snapshot().open_positions == 2


def test_unresolved_intents_count_as_exposure(stores):
    stores["unresolved"]["family"] = [object(), object()]
    assert unresolved_exposure_count() == 2


def test_legacy_only_user_and_position_are_counted(stores):
    stores["uids"] = []
    stores["configs"] = {"intraday_positions_legacy-only": "[]"}
    stores["intraday"]["legacy-only"] = [_Position("NIFTY26OCT25000CE")]

    snapshot = exposure_snapshot()

    assert snapshot.open_positions == 1
    assert snapshot.held == ("NIFTY26OCT25000CE",)
    assert snapshot.uids == ("legacy-only",)


def test_unresolved_intent_user_is_discovered_without_position_projection(stores):
    stores["uids"] = []
    stores["journal_uids"] = ["journal-only"]
    stores["unresolved"]["journal-only"] = [object()]

    snapshot = exposure_snapshot()

    assert snapshot.unresolved_intents == 1
    assert snapshot.uids == ("journal-only",)


class TestUnknownNeverBecomesZero:
    def test_an_unreadable_broker_makes_the_total_unknown(self, monkeypatch):
        from app.services.kite_engine import order_journal, positions

        monkeypatch.setattr("app.services.db.init", lambda *a, **k: True)
        monkeypatch.setattr(positions, "known_uids", lambda: ["family"])
        monkeypatch.setattr(positions, "open_positions", lambda uid: [])
        monkeypatch.setattr(order_journal, "unresolved", lambda uid: [])
        monkeypatch.setattr(module, "_external", lambda: (None, "timed out", (), None))
        # Local flat plus an unanswerable broker is not flat.
        assert exposure_snapshot().total is None

    def test_an_unopenable_database_is_unknown(self, monkeypatch):
        def _boom(*_a, **_k):
            raise RuntimeError("database is locked")

        monkeypatch.setattr("app.services.db.init", _boom)
        snapshot = exposure_snapshot()
        assert snapshot.total is None
        assert "database is locked" in snapshot.detail

    def test_an_unreadable_registry_is_unknown(self, monkeypatch):
        from app.services.kite_engine import positions

        monkeypatch.setattr("app.services.db.init", lambda *a, **k: True)

        def _boom():
            raise RuntimeError("system_config unreadable")

        monkeypatch.setattr(positions, "known_uids", _boom)
        assert exposure_snapshot().total is None

    def test_one_unreadable_operator_makes_the_whole_answer_unknown(self, stores):
        from app.services.kite_engine import order_journal

        stores["uids"] = ["family", "second"]

        def _boom(uid):
            if uid == "second":
                raise RuntimeError("journal unavailable")
            return []

        import pytest as _pytest

        monkeypatch = _pytest.MonkeyPatch()
        monkeypatch.setattr(order_journal, "unresolved", _boom)
        try:
            snapshot = exposure_snapshot()
        finally:
            monkeypatch.undo()
        # A partial count is worse than no count: it reads as "less exposure
        # than there is", which is the direction that loses money.
        assert snapshot.total is None
        assert "second" in snapshot.detail


class TestTheCertificationGate:
    def test_open_exposure_cannot_be_attested_away(self, tmp_path):
        from app.services.release_certification import CertificationStore

        with pytest.raises(ValueError, match="derived from its own register"):
            CertificationStore(tmp_path).attest(
                "a" * 40, "open_exposure", "PASS", attested_by="operator")

    def test_the_gate_fails_while_a_position_is_open(self, stores):
        from app.services.release_certification import _open_exposure_gate

        stores["open"]["family"] = [_Position("NIFTY26JAN24000CE")]
        gate = _open_exposure_gate(exposure_snapshot())
        assert gate.status == "FAIL"
        assert "NIFTY26JAN24000CE" in gate.detail

    def test_the_gate_passes_only_on_a_flat_deployment(self, stores):
        from app.services.release_certification import _open_exposure_gate

        assert _open_exposure_gate(exposure_snapshot()).status == "PASS"

    def test_an_unreadable_store_is_unknown_not_a_pass(self, monkeypatch):
        from app.services.release_certification import _open_exposure_gate

        def _boom(*_a, **_k):
            raise RuntimeError("database is locked")

        monkeypatch.setattr("app.services.db.init", _boom)
        assert _open_exposure_gate().status == "UNKNOWN"


    def test_stale_recorded_broker_flat_is_unknown_not_pass(
        self, tmp_path, monkeypatch
    ):
        import json
        from datetime import datetime, timedelta, timezone

        from app.services.release_certification import _open_exposure_gate
        from app.services.kite_engine import positions

        record = tmp_path / "external_exposure.json"
        record.write_text(
            json.dumps({
                "observed_at": (
                    datetime.now(timezone.utc) - timedelta(days=2)
                ).isoformat(),
                "readable": True,
                "count": 0,
                "instruments": [],
                "detail": "",
            }),
            encoding="utf-8",
        )
        monkeypatch.setenv("STERLING_EXTERNAL_EXPOSURE_FILE", str(record))
        monkeypatch.setattr("app.services.db.init", lambda *a, **k: True)
        monkeypatch.setattr(positions, "known_uids", lambda: [])

        gate = _open_exposure_gate()

        assert gate.status == "UNKNOWN"
        assert "stale" in gate.detail.lower()


    def test_recent_flat_from_different_live_account_is_unknown(
        self, tmp_path, monkeypatch
    ):
        import json
        from datetime import datetime, timezone

        from app.services.release_certification import _open_exposure_gate

        record = tmp_path / "external_exposure.json"
        record.write_text(
            json.dumps({
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "readable": True,
                "count": 0,
                "accounts": ["old-account"],
                "instruments": [],
                "detail": "",
            }),
            encoding="utf-8",
        )
        monkeypatch.setenv("STERLING_EXTERNAL_EXPOSURE_FILE", str(record))
        monkeypatch.setattr("app.services.db.init", lambda *a, **k: True)
        monkeypatch.setattr(module, "_known_local_uids", lambda: ())
        monkeypatch.setattr(
            "app.services.external_positions.live_account_ids",
            lambda: ("current-account",),
        )

        gate = _open_exposure_gate()

        assert gate.status == "UNKNOWN"
        assert "different live-account set" in gate.detail


    def test_recent_flat_for_exact_live_account_can_pass(
        self, tmp_path, monkeypatch
    ):
        import json
        from datetime import datetime, timezone

        from app.services.release_certification import _open_exposure_gate

        record = tmp_path / "external_exposure.json"
        record.write_text(
            json.dumps({
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "readable": True,
                "count": 0,
                "accounts": ["current-account"],
                "instruments": [],
                "detail": "",
            }),
            encoding="utf-8",
        )
        monkeypatch.setenv("STERLING_EXTERNAL_EXPOSURE_FILE", str(record))
        monkeypatch.setattr("app.services.db.init", lambda *a, **k: True)
        monkeypatch.setattr(module, "_known_local_uids", lambda: ())
        monkeypatch.setattr(
            "app.services.external_positions.live_account_ids",
            lambda: ("current-account",),
        )

        assert _open_exposure_gate().status == "PASS"


    def test_future_recorded_broker_flat_is_unknown_not_pass(
        self, tmp_path, monkeypatch
    ):
        import json
        from datetime import datetime, timedelta, timezone

        from app.services.release_certification import _open_exposure_gate
        from app.services.kite_engine import positions

        record = tmp_path / "external_exposure.json"
        record.write_text(
            json.dumps({
                "observed_at": (
                    datetime.now(timezone.utc) + timedelta(hours=1)
                ).isoformat(),
                "readable": True,
                "count": 0,
                "instruments": [],
                "detail": "",
            }),
            encoding="utf-8",
        )
        monkeypatch.setenv("STERLING_EXTERNAL_EXPOSURE_FILE", str(record))
        monkeypatch.setattr("app.services.db.init", lambda *a, **k: True)
        monkeypatch.setattr(positions, "known_uids", lambda: [])

        gate = _open_exposure_gate()

        assert gate.status == "UNKNOWN"
        assert "future" in gate.detail.lower()
