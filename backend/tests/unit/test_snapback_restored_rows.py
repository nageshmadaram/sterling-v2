"""After a restart the board shows the last scan at once, but never lets it be acted on."""
from app.services import snapback


def test_last_scan_rows_survive_a_restart_read_only(monkeypatch):
    uid = "restore-user"
    row = {"signal_id": "s1", "symbol": "INFY", "state": "armed", "stretch": 2.0}
    snapback._save_last_rows(uid, [row])
    monkeypatch.setattr(snapback, "_state", {})
    st = snapback.status(uid)
    assert [r["symbol"] for r in st.rows] == ["INFY"]
    assert st.rows[0]["restored"] is True
    cfg = snapback.get_effective_config(uid)
    served = snapback._aged_rows(st.rows, cfg, judge=True)
    assert served[0]["execution_eligible"] is False
    assert served[0]["state"] == "watching"
    assert "restored_from_last_scan" in served[0]["execution_block_reasons"]


def test_settings_change_forgets_saved_rows(monkeypatch):
    uid = "restore-user-2"
    snapback._save_last_rows(uid, [{"signal_id": "s1", "symbol": "TCS", "state": "watching"}])
    cur = snapback.get_config(uid)
    snapback.set_config({"premium_pct_of_capital": cur.premium_pct_of_capital / 2}, uid=uid)
    monkeypatch.setattr(snapback, "_state", {})
    assert snapback.status(uid).rows == []


def test_on_off_and_auto_switches_keep_the_board(monkeypatch):
    uid = "restore-user-3"
    snapback.set_config({}, uid=uid)
    st = snapback.status(uid)
    st.rows = [{"signal_id": "s1", "symbol": "SBIN", "state": "watching"}]
    cur = snapback.get_config(uid)
    snapback.set_config({"enabled": not cur.enabled}, uid=uid)
    snapback.set_config({"auto_execute": not cur.auto_execute}, uid=uid)
    assert [r["symbol"] for r in snapback.status(uid).rows] == ["SBIN"]
