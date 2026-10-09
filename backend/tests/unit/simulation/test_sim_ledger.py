"""The virtual books: accounting, owner isolation, reset, persistence."""
import pytest

from app.services.simulation import charges as ch
from app.services.simulation.ledger import DEFAULT_CAPITAL, SettingsError, SimLedger, public

OPT = "NIFTY2611325000CE"
FUT = "NIFTY26OCTFUT"


def _order(side, symbol=OPT, oid="1", product="NRML", exchange="NFO"):
    return {"order_id": oid, "exchange": exchange, "tradingsymbol": symbol, "product": product,
            "transaction_type": side, "instrument_token": 123, "tag": "t",
            "order_timestamp": "2026-10-12 10:16:00", "exchange_update_timestamp": "2026-10-12 10:16:00"}


def _fill(ledger, owner, side, qty, price, symbol=OPT, meta=None, oid="1", day="2026-10-12", mark=None):
    meta = meta or {"instrument_type": "CE", "strike": 25000.0, "tradingsymbol": symbol}
    c = ch.compute_charges(exchange="NFO", tradingsymbol=symbol, side=side, quantity=qty, price=price,
                           instrument_type=meta.get("instrument_type", ""))
    return ledger.record_fill(owner, order=_order(side, symbol, oid), quantity=qty, price=price, charges=c,
                              meta=meta, day=day, mark=mark), c


def test_a_new_account_starts_with_the_default_capital(ledger):
    acct = ledger.ensure_account("default")
    assert acct["capital"] == acct["cash"] == DEFAULT_CAPITAL == 1_000_000.0
    assert ledger.ensure_account("default")["created_ms"] == acct["created_ms"]      # idempotent
    assert ledger.account("nobody") is None


def test_a_sim_uid_and_its_owner_are_the_same_books(ledger):
    ledger.ensure_account("sim:alice", capital=5.0e5)
    assert ledger.account("alice")["capital"] == 5.0e5
    assert ledger.owners() == ["alice"]


def test_round_trip_books_realised_pnl_less_charges(ledger):
    _, buy = _fill(ledger, "o", "BUY", 75, 100.0)
    assert ledger.cash("o") == round(1_000_000 - buy.total, 2) == 999_973.02
    pos = ledger.get_position("o", "NFO", OPT, "NRML")
    assert (pos["quantity"], pos["avg_price"]) == (75, 100.0)
    trade, sell = _fill(ledger, "o", "SELL", 75, 110.0, oid="2")
    assert trade["_sim"]["realised"] == 750.0
    assert ledger.cash("o") == round(1_000_000 - buy.total + 750.0 - sell.total, 2) == 1_000_683.57
    pos = ledger.get_position("o", "NFO", OPT, "NRML")
    assert (pos["quantity"], pos["avg_price"]) == (0, 0.0)


def test_scaling_in_averages_the_cost(ledger):
    _fill(ledger, "o", "BUY", 75, 100.0)
    _fill(ledger, "o", "BUY", 75, 110.0, oid="2")
    pos = ledger.get_position("o", "NFO", OPT, "NRML")
    assert (pos["quantity"], pos["avg_price"]) == (150, 105.0)


def test_a_partial_close_realises_only_the_closed_part(ledger):
    _fill(ledger, "o", "BUY", 150, 100.0)
    trade, _ = _fill(ledger, "o", "SELL", 75, 112.0, oid="2")
    assert trade["_sim"]["realised"] == 900.0
    pos = ledger.get_position("o", "NFO", OPT, "NRML")
    assert (pos["quantity"], pos["avg_price"]) == (75, 100.0)


def test_a_short_that_is_covered_lower_books_a_profit(ledger):
    _fill(ledger, "o", "SELL", 75, 100.0)
    trade, _ = _fill(ledger, "o", "BUY", 75, 90.0, oid="2")
    assert trade["_sim"]["realised"] == 750.0


def test_flipping_through_zero_realises_the_close_and_reopens_at_the_fill(ledger):
    _fill(ledger, "o", "BUY", 75, 100.0)
    trade, _ = _fill(ledger, "o", "SELL", 150, 110.0, oid="2")
    assert trade["_sim"]["realised"] == 750.0
    pos = ledger.get_position("o", "NFO", OPT, "NRML")
    assert (pos["quantity"], pos["avg_price"]) == (-75, 110.0)


def test_account_view_marks_open_positions_to_the_last_real_price(ledger):
    _, buy = _fill(ledger, "o", "BUY", 75, 100.0, mark=110.0)
    view = ledger.account_view("o", "2026-10-12")
    assert view["blocked_margin"] == 7500.0                      # long option: the premium
    assert view["unrealised"] == 750.0
    assert view["cash"] == 999_973.02
    assert view["available"] == 999_973.02 - 7500.0
    assert view["equity"] == 999_973.02 + 750.0
    assert view["charges_today"] == buy.total and view["realised_today"] == 0.0
    assert (view["open_positions"], view["trades_today"]) == (1, 1)
    ledger.set_mark("o", "NFO", OPT, "NRML", 90.0)
    view = ledger.account_view("o", "2026-10-12")
    assert view["unrealised"] == -750.0
    assert view["available"] == round(999_973.02 - 7500.0 - 750.0, 2)   # a loss shrinks what is free


def test_futures_and_short_options_hold_twelve_percent_of_notional(ledger):
    fut_meta = {"instrument_type": "FUT", "tradingsymbol": FUT}
    _fill(ledger, "o", "BUY", 75, 25000.0, symbol=FUT, meta=fut_meta)
    assert ledger.account_view("o", "2026-10-12")["blocked_margin"] == 225_000.0
    _fill(ledger, "p", "SELL", 75, 100.0)                         # short 25000 CE: 12% of strike * qty
    assert ledger.account_view("p", "2026-10-12")["blocked_margin"] == 225_000.0


def test_today_is_separated_from_other_days(ledger):
    _fill(ledger, "o", "BUY", 75, 100.0, day="2026-10-09")
    _fill(ledger, "o", "SELL", 75, 110.0, oid="2", day="2026-10-12")
    today = ledger.account_view("o", "2026-10-12")
    assert today["realised_today"] == 750.0 and today["trades_today"] == 1
    assert ledger.account_view("o", "2026-10-09")["realised_today"] == 0.0
    assert len(ledger.trades("o")) == 2 and len(ledger.trades("o", day="2026-10-12")) == 1


def test_owners_never_mix(ledger):
    _fill(ledger, "a", "BUY", 75, 100.0)
    ledger.put_order("a", {**_order("BUY"), "status": "OPEN", "_sim": {"day": "2026-10-12"}})
    assert ledger.positions("b") == [] and ledger.orders("b") == [] and ledger.trades("b") == []
    assert ledger.cash("b") == DEFAULT_CAPITAL
    ledger.reset("b")
    assert len(ledger.positions("a")) == 1 and len(ledger.orders("a")) == 1


def test_reset_clears_the_books_but_keeps_capital_and_settings_and_never_reuses_ids(ledger):
    ledger.set_capital("o", 2_000_000.0)
    ledger.set_setting("o", "slippage_ticks", 3)
    t1, _ = _fill(ledger, "o", "BUY", 75, 100.0)
    ledger.put_gtt("o", {"id": 5, "status": "active", "condition": {"exchange": "NFO", "tradingsymbol": OPT}})
    acct = ledger.reset("o")
    assert acct["capital"] == acct["cash"] == 2_000_000.0 and acct["reset_ms"] > 0
    assert ledger.positions("o") == [] and ledger.trades("o") == [] and ledger.gtts("o") == []
    assert ledger.charges("o") == []
    assert ledger.settings("o").slippage_ticks == 3
    t2, _ = _fill(ledger, "o", "BUY", 75, 100.0)
    assert int(t2["trade_id"]) > int(t1["trade_id"])


def test_reset_can_change_the_capital(ledger):
    ledger.ensure_account("o")
    assert ledger.reset("o", capital=250_000.0)["cash"] == 250_000.0
    with pytest.raises(ValueError):
        ledger.reset("o", capital=0)


def test_set_capital_adds_or_withdraws_funds_and_keeps_pnl(ledger):
    _fill(ledger, "o", "BUY", 75, 100.0)
    cash_before = ledger.cash("o")
    acct = ledger.set_capital("o", 1_500_000.0)
    assert acct["capital"] == 1_500_000.0 and acct["cash"] == round(cash_before + 500_000.0, 2)
    with pytest.raises(ValueError):
        ledger.set_capital("o", -1)


def test_state_survives_a_new_ledger_instance(db_factory):
    first = SimLedger(db_factory)
    _fill(first, "o", "BUY", 75, 100.0, mark=104.0)
    first.put_order("o", {**_order("BUY"), "status": "OPEN", "_sim": {"day": "2026-10-12", "k": 1}})
    first.add_history("o", {**_order("BUY"), "status": "OPEN"})
    first.set_setting("o", "partial_fills", True)
    second = SimLedger(db_factory)
    assert second.cash("o") == first.cash("o") == 999_973.02
    pos = second.get_position("o", "NFO", OPT, "NRML")
    assert (pos["quantity"], pos["avg_price"], pos["last_price"]) == (75, 100.0, 104.0)
    assert second.get_order("o", "1")["_sim"]["k"] == 1
    assert [h["status"] for h in second.history("o", "1")] == ["OPEN"]
    assert second.settings("o").partial_fills is True
    assert second.owners_with_open_state() == ["o"]


def test_the_default_connection_is_the_application_database(tmp_path, monkeypatch):
    from app.services import db
    monkeypatch.setattr(db, "_DB_PATH", str(tmp_path / "app.db"))
    one = SimLedger()
    one.ensure_account("default")
    one.set_capital("default", 123_456.0)
    assert SimLedger().account("default")["capital"] == 123_456.0
    monkeypatch.setattr(db, "_DB_PATH", str(tmp_path / "other.db"))        # another database, another books
    assert SimLedger().account("default") is None


def test_a_failed_transaction_leaves_nothing_behind(ledger):
    ledger.ensure_account("o")
    with pytest.raises(RuntimeError):
        with ledger.transaction():
            ledger.adjust_cash("o", -500.0)
            raise RuntimeError("boom")
    assert ledger.cash("o") == DEFAULT_CAPITAL


def test_order_history_and_public_view(ledger):
    doc = {**_order("BUY"), "status": "OPEN", "_sim": {"day": "d", "placed_ms": 1}}
    ledger.put_order("o", doc)
    ledger.add_history("o", {**doc, "status": "PUT ORDER REQ RECEIVED"})
    ledger.add_history("o", doc)
    assert [h["status"] for h in ledger.history("o", "1")] == ["PUT ORDER REQ RECEIVED", "OPEN"]
    assert "_sim" not in public(ledger.get_order("o", "1")) and public(None) is None
    assert [o["order_id"] for o in ledger.open_orders("o")] == ["1"]
    ledger.put_order("o", {**doc, "status": "COMPLETE"})
    assert ledger.open_orders("o") == []


def test_ids_are_unique_and_increasing(ledger):
    ids = [ledger.next_id("order") for _ in range(3)]
    assert ids == sorted(set(ids)) and ledger.next_id("gtt") == 1


def test_settings_are_typed_and_validated(ledger):
    assert ledger.settings("o").slippage_ticks == 1 and ledger.settings("o").partial_fills is False
    s = ledger.set_setting("o", "slippage_ticks", "2")
    assert s.slippage_ticks == 2
    assert ledger.set_setting("o", "enforce_tick_size", "false").enforce_tick_size is False
    for key, value in (("nope", 1), ("slippage_ticks", 99), ("slippage_ticks", "x"), ("futures_margin_pct", 0)):
        with pytest.raises(SettingsError):
            ledger.set_setting("o", key, value)


def test_equity_snapshots_round_trip(ledger):
    _fill(ledger, "o", "BUY", 75, 100.0, mark=101.0)
    view = ledger.account_view("o", "2026-10-12")
    ledger.add_snapshot("o", view, ts_ms=1_000)
    ledger.add_snapshot("o", view, ts_ms=2_000)
    ledger.add_snapshot("p", view, ts_ms=1_500)
    rows = ledger.snapshots("o")
    assert [r["ts_ms"] for r in rows] == [1_000, 2_000] and rows[0]["equity"] == view["equity"]
    assert [r["ts_ms"] for r in ledger.snapshots("o", since_ms=1_500)] == [2_000]


def test_an_empty_owner_is_refused(ledger):
    with pytest.raises(ValueError):
        ledger.ensure_account("  ")


def test_a_savepoint_undoes_only_its_own_writes(ledger):
    ledger.ensure_account("o")
    with ledger.transaction():
        ledger.adjust_cash("o", -100.0)
        with pytest.raises(RuntimeError):
            with ledger.savepoint():
                ledger.adjust_cash("o", -900.0)
                raise RuntimeError("one bad unit")
        ledger.adjust_cash("o", -1.0)
    assert ledger.cash("o") == DEFAULT_CAPITAL - 101.0
    with ledger.savepoint():                                        # usable on its own too
        ledger.adjust_cash("o", 1.0)
    assert ledger.cash("o") == DEFAULT_CAPITAL - 100.0
