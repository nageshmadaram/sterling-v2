"""Background refresh for the Snapback board.

``snapback.scan_once`` ran only when someone POSTed ``/config/snapback/scan``, and
the dock's global re-scan does not include Snapback, so the board sat at
"0 of 0 scanned" until a manual scan. This refresh is display-only: it does not
write prospective evidence and it never places an order. It runs once at
startup and then every ``interval`` seconds, including outside the session, so a
host that slept through market hours still shows current rows.
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)


def _kite_user_ids() -> list[str]:
    try:
        from app.services.exchanges.kite import accounts
        accounts.bootstrap()
        return sorted({a.user_id for a in accounts.all_accounts() if a.is_active})
    except Exception:
        return []


async def refresh_all_once() -> None:
    from app.services import snapback

    for uid in _kite_user_ids():
        try:
            await snapback.scan_once(uid, record_evidence=False)
        except Exception as exc:  # noqa: BLE001
            log.warning("snapback board refresh failed for %s: %s", uid, exc)


async def auto_scan_loop(interval: float = 900) -> None:
    log.info("snapback board auto-refresh loop started (every %ss)", interval)
    try:
        while True:
            try:
                await refresh_all_once()
            except Exception as exc:  # noqa: BLE001
                log.warning("snapback board refresh iteration error: %s", exc)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        log.info("snapback board auto-refresh loop stopped")
        raise
