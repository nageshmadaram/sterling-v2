"""Other-mode (view-only) SuperTrend rows survive a restart instead of vanishing
until each mode's preview turn comes round again."""
import time

from app.engines.sterling_kite_engine.schemas import AlignmentChip, EngineSignalRow
from app.services.kite_engine import scanner as scanner_mod, service, state


def test_preview_rows_read_saved_rows_after_restart(monkeypatch):
    uid = "preview-restart"
    row = EngineSignalRow(
        underlying="NIFTY 50", token=256265, exchange="NFO", regime="BULL",
        alignment=AlignmentChip(fast=1, mid=1, slow=1), direction="long", option_type="CE",
        legs=[], spot=24500.0, stop_loss=24300.0, score=85.0, timestamp_ms=int(time.time() * 1000), signal_timeframe_minutes=5,
        is_active=True, is_fresh=True, source="spot", horizon_mode="scalping",
    )
    state.save_signal_cache(service.preview_key(uid, "scalping"), [row.model_dump()], 1)
    monkeypatch.setattr(scanner_mod.scanner, "_users", {})  # a fresh process
    rows = service.preview_rows(uid, "swing")
    assert [r.underlying for r in rows] == ["NIFTY 50"]
    assert rows[0].view_only is True
    assert rows[0].horizon_mode == "scalping"
