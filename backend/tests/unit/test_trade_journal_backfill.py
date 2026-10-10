"""The one-shot importer for trades that closed before the journal existed."""
from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from tj_support import OWNER, SIM, clock, journal_db, ms  # noqa: F401  (journal_db, clock are fixtures)

SYM = "NIFTY26O2925000CE"


@pytest.fixture
def stores(journal_db, clock, tmp_path, monkeypatch):
    """The application database, plus a Snapback position store the importer will find."""
    from app.services import db, snapback_live_positions as sbp, trade_journal_adapters as adapters
    assert db.init()
    store = sbp.LivePositionStore(tmp_path / "live_positions.db")
    monkeypatch.setattr(sbp, "default_store", lambda: store)
    return store


def _st_row(uid=OWNER, symbol=SYM, **over):
    from app.services.kite_engine.positions import CLOSED, OpenPosition
    base = dict(uid=uid, symbol=symbol, exchange="NFO", qty=75, lot_size=75, entry_premium=120.0, stop_premium=96.0,
                initial_stop_premium=96.0, order_id="O1", fill_price=120.0, status=CLOSED, strike=25000.0,
                expiry="2026-10-29", underlying="NIFTY", opened_ms=ms(9, 40), closed_ms=ms(11, 20),
                exit_reason="broker GTT triggered; fill @ ₹95.00", exit_fills={"X1": 75}, account_id="ACC1")
    base.update(over)
    return asdict(OpenPosition(**base))


def _put_registry(uid, rows):
    from app.services import db
    db.set_config(f"kite_engine_positions_{uid}", json.dumps(rows))


def _position(uid=OWNER, pid="plan-1", **over):
    from app.services.snapback_live_positions import LivePosition
    fields = dict(
        position_id=pid, uid=uid, account_id="ACC1", signal_id="snapback:fade_up:NIFTY:1", lane="swing",
        underlying="NIFTY", side="fade_up", option_type="PE", option_exchange="NFO", option_symbol="NIFTY26NOV25000PE",
        option_token=5, lot_size=75, option_qty=75, option_entry_price=300.0, option_entry_order_id="O1",
        option_expiry="2026-11-26", hedge_exchange="NFO", hedge_symbol="NIFTY26NOVFUT", hedge_side="BUY",
        hedge_qty=75, hedge_entry_price=25020.5, hedge_entry_order_id="H1", hedge_lot_size=75,
        rules={"lane": "swing", "premium_stop_pct": 35.0, "hold_days": 15}, status="CLOSED", stop_premium=195.0,
        desired_stop=195.0, high_water=310.0, opened_ms=ms(9, 40), entry_day="2026-10-12", exit_reason="PREMIUM_STOP",
        option_exit_qty=75, option_exit_value=75 * 195.0, option_exit_price=195.0, hedge_exit_qty=75,
        hedge_exit_value=75 * 25100.0, hedge_exit_price=25100.0, closed_ms=ms(13, 5),
        realised_pnl=round((195.0 - 300.0) * 75 + (25100.0 - 25020.5) * 75, 2))
    fields.update(over)
    return LivePosition(**fields)


def _put_snapback(store, monkeypatch, pos):
    """Written the way it was before the journal existed: no hook sees it."""
    from app.services import trade_journal_adapters as adapters
    with monkeypatch.context() as m:
        m.setattr(adapters, "sb_sync", lambda *_a, **_k: None)
        store.put(pos)


def _only(tj, uid):
    page = tj.list_trades([uid])
    assert page["total"] == 1, [t["narrative"] for t in page["items"]]
    return tj.get_trade(page["items"][0]["trade_id"], [uid])


# --------------------------------------------------------------------------------- Snapback

def test_a_closed_snapback_trade_is_imported_complete(journal_db, stores, monkeypatch):
    from app.services import trade_journal_backfill as bf
    _put_snapback(stores, monkeypatch, _position())
    report = bf.backfill()
    assert report["snapback"] == {"imported": 1, "existing": 0, "skipped": 0} and report["errors"] == []
    t = _only(journal_db, OWNER)
    assert (t["source"], t["status"], t["strategy"], t["mode"]) == ("backfill", "CLOSED", "snapback", "LIVE")
    assert (t["entry_price"], t["exit_price"], t["initial_sl"]) == (300.0, 195.0, 195.0)
    assert (t["entry_ts_ms"], t["exit_ts_ms"]) == (ms(9, 40), ms(13, 5))      # the stores' times, not the import's
    assert t["gross_pnl"] == -1912.5 and t["exit_reason"] == "SL" and t["extra"]["hedge"]["exit_price"] == 25100.0
    assert t["charges"] > 0 and t["net_pnl"] == pytest.approx(t["gross_pnl"] - t["charges"], abs=0.01)
    assert [e["type"] for e in t["timeline"]] == ["FILLED", "HEDGE_FILLED", "EXIT_FILLED"]
    assert [e["ts_ms"] for e in t["timeline"]] == [ms(9, 40), ms(9, 40), ms(13, 5)]
    assert "hedged with NIFTY26NOVFUT" in t["narrative"] and "when the stop-loss was hit" in t["narrative"]


def test_running_it_again_changes_nothing(journal_db, stores, monkeypatch):
    from app.services import trade_journal_backfill as bf
    _put_snapback(stores, monkeypatch, _position())
    bf.backfill()
    before = _only(journal_db, OWNER)
    again = bf.backfill()
    assert again["snapback"] == {"imported": 0, "existing": 1, "skipped": 0}
    assert _only(journal_db, OWNER) == before


def test_a_trade_the_hooks_journalled_is_left_alone(journal_db, stores):
    from app.services import trade_journal_backfill as bf
    stores.put(_position())                                                  # the live hook journals it
    assert _only(journal_db, OWNER)["source"] == "journal"
    before = _only(journal_db, OWNER)
    assert bf.backfill()["snapback"] == {"imported": 0, "existing": 1, "skipped": 0}
    assert _only(journal_db, OWNER) == before


def test_a_snapback_row_without_a_price_or_close_time_is_skipped_not_guessed(journal_db, stores, monkeypatch):
    from app.services import trade_journal_backfill as bf
    _put_snapback(stores, monkeypatch, _position(pid="a", closed_ms=0))
    _put_snapback(stores, monkeypatch, _position(pid="b", option_entry_price=0.0))
    _put_snapback(stores, monkeypatch, _position(pid="c", status="OPEN"))
    assert bf.backfill()["snapback"] == {"imported": 0, "existing": 0, "skipped": 2}
    assert journal_db.list_trades([OWNER])["total"] == 0


def test_a_snapback_exit_nobody_priced_is_a_closed_trade_without_pnl(journal_db, stores, monkeypatch):
    from app.services import trade_journal_backfill as bf, trade_journal_stats as stats
    _put_snapback(stores, monkeypatch, _position(option_exit_price=0.0, option_exit_qty=75, option_exit_unknown_qty=75,
                                                 option_exit_value=0.0, realised_pnl=0.0, exit_reason="reconciled"))
    bf.backfill()
    t = _only(journal_db, OWNER)
    assert t["status"] == "CLOSED" and t["exit_price"] is None and t["gross_pnl"] is None and t["net_pnl"] is None
    s = stats.summary(journal_db.rows_for_stats([OWNER]))
    assert s["trades"] == 1 and s["unknown_pnl"] == 1 and s["wins"] == 0 and s["losses"] == 0


# -------------------------------------------------------------------------------- Supertrend

def test_a_closed_supertrend_trade_takes_its_exit_price_from_the_recorded_fill(journal_db, stores):
    from app.services import trade_journal_backfill as bf
    _put_registry(OWNER, [_st_row()])
    report = bf.backfill()
    assert report["supertrend"] == {"imported": 1, "existing": 0, "skipped": 0}
    t = _only(journal_db, OWNER)
    assert (t["source"], t["status"], t["entry_price"], t["exit_price"], t["qty"]) == ("backfill", "CLOSED", 120.0, 95.0, 75)
    assert (t["entry_ts_ms"], t["exit_ts_ms"]) == (ms(9, 40), ms(11, 20))
    assert t["gross_pnl"] == -1875.0 and t["exit_reason"] == "SL" and t["initial_sl"] == 96.0
    assert [e["type"] for e in t["timeline"]] == ["FILLED", "EXIT_FILLED"]
    assert t["narrative"].startswith("Bought 1 lot NIFTY 25000 CE at 120.00")


def test_a_supertrend_trade_whose_exit_price_was_not_recorded_has_no_pnl(journal_db, stores):
    from app.services import trade_journal_backfill as bf
    _put_registry(OWNER, [_st_row(exit_reason="red count exit 3/3 (three_red_signal)")])
    bf.backfill()
    t = _only(journal_db, OWNER)
    assert t["status"] == "CLOSED" and t["exit_reason"] == "SIGNAL_EXIT"
    assert t["exit_price"] is None and t["gross_pnl"] is None and t["net_pnl"] is None and t["exit_ts_ms"] == ms(11, 20)


def test_supertrend_rows_that_are_open_partly_exited_or_incomplete_are_skipped(journal_db, stores):
    from app.services import trade_journal_backfill as bf
    rows = [_st_row(symbol="A", status="open", exit_reason=""),
            _st_row(symbol="B", exit_fills={"X1": 25, "X2": 50}),          # qty on the row is no longer the entry qty
            _st_row(symbol="C", closed_ms=0), _st_row(symbol="D", fill_price=0.0),
            _st_row(symbol="E", uid="someone-else"), _st_row(symbol="F", order_id="O9")]
    _put_registry(OWNER, rows)
    assert bf.backfill()["supertrend"] == {"imported": 1, "existing": 0, "skipped": 5}
    assert [t["symbol"] for t in journal_db.list_trades([OWNER])["items"]] == ["F"]


def test_a_supertrend_trade_the_hooks_journalled_is_left_alone(journal_db, stores):
    from app.services import trade_journal_adapters as adapters, trade_journal_backfill as bf
    from app.services.kite_engine.positions import OpenPosition
    row = _st_row()
    p = OpenPosition(**{k: v for k, v in row.items() if k in OpenPosition.__dataclass_fields__})
    adapters.st_filled(OWNER, p, "O1")
    adapters.st_closed(OWNER, p, 95.0, "broker GTT triggered", 75, "X1")
    _put_registry(OWNER, [row])
    before = _only(journal_db, OWNER)
    assert before["source"] == "journal"
    assert bf.backfill()["supertrend"] == {"imported": 0, "existing": 1, "skipped": 0}
    assert _only(journal_db, OWNER) == before


# ---------------------------------------------------------------------------- scope and safety

def test_a_simulation_tenant_and_gamma_are_never_imported(journal_db, stores, monkeypatch):
    from app.services import db, trade_journal_backfill as bf
    _put_registry(SIM, [_st_row(uid=SIM)])
    _put_snapback(stores, monkeypatch, _position(uid=SIM, pid="s1"))
    db.set_config(f"gamma_move_positions_{OWNER}", json.dumps([{"signal_id": "g1", "status": "closed"}]))
    report = bf.backfill()
    assert report["supertrend"] == {"imported": 0, "existing": 0, "skipped": 0}
    assert report["snapback"] == {"imported": 0, "existing": 0, "skipped": 0}
    assert journal_db.list_trades([OWNER, SIM])["total"] == 0


def test_an_explicit_uid_list_limits_the_import(journal_db, stores):
    from app.services import trade_journal_backfill as bf
    _put_registry("alice", [_st_row(uid="alice")])
    _put_registry("bob", [_st_row(uid="bob")])
    assert bf.backfill(["alice"])["supertrend"]["imported"] == 1
    assert [t["uid"] for t in journal_db.list_trades(["alice", "bob"])["items"]] == ["alice"]


def test_an_unreadable_store_is_reported_and_the_others_still_import(journal_db, stores):
    from app.services import db, trade_journal_backfill as bf
    db.set_config("kite_engine_positions_broken", "{not json")
    _put_registry(OWNER, [_st_row()])
    report = bf.backfill()
    assert report["supertrend"]["imported"] == 1 and any("broken" in e for e in report["errors"])


def test_backfill_never_raises_even_when_the_database_is_unavailable(journal_db, monkeypatch):
    from app.services import db, trade_journal_backfill as bf
    monkeypatch.setattr(db, "_available", False)
    assert bf.backfill()["errors"] == ["database_unavailable"]
    assert bf.run_once()["status"] == "skipped"


def test_the_startup_run_happens_once(journal_db, stores):
    from app.services import trade_journal_backfill as bf
    _put_registry(OWNER, [_st_row()])
    first = bf.run_once()
    assert first["status"] == "done" and first["supertrend"]["imported"] == 1
    _put_registry(OWNER, [_st_row(), _st_row(symbol="LATER", order_id="O2")])
    assert bf.run_once() == {"status": "skipped"}
    assert journal_db.list_trades([OWNER])["total"] == 1


def test_a_failed_startup_run_is_retried_next_boot(journal_db, stores):
    from app.services import db, trade_journal_backfill as bf
    db.set_config("kite_engine_positions_broken", "{not json")
    assert bf.run_once()["status"] == "partial"
    assert db.get_config(bf.MARKER_KEY) == ""
