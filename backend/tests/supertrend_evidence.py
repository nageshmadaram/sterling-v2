"""Publish synthetic scan authority through the real refresh-board state."""
from tests.feature_grants import grant_kite


def publish(uid, row):
    from app.services.kite_engine.scan_refresh import RefreshBoard
    from app.services.kite_engine.scanner import scanner
    from app.engines.sterling_kite_engine.config import SterlingKiteEngineConfig
    grant_kite(uid)
    refresh = RefreshBoard([], [(row.source, row.underlying)],
                           mode=row.horizon_mode, timeframe=row.signal_timeframe_minutes)
    refresh.observe(row)
    refresh.finish(row.source, row.underlying, success=True)
    scan = scanner._user(uid, SterlingKiteEngineConfig())
    scan.horizon_mode = row.horizon_mode
    scan.timeframe_minutes = row.signal_timeframe_minutes
    scan.rows = [row]
    scan.refresh_board = refresh


async def observed_callback(uid, callback, row, item):
    publish(uid, row)
    return await callback(row, item)
