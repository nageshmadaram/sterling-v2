"""Deployment shape a live Sterling machine must have, before it is ever started.

Sterling ships four strategies (SuperTrend, Snapback, Gamma Unwind, Price Rules) as a live,
real-money app, and every one of their loops runs. Family Mode, which used to
silence all but one of them, is retired: ``main.py`` starts every loop regardless
of ``STERLING_FAMILY_MODE``. What stays is the rest of the deployment contract: a
socket that is not bound to every interface on a home network, and a shutdown that
leaves a record rather than tearing the process down between an insert and its
commit.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, FrozenSet, Optional

log = logging.getLogger(__name__)

# Every background loop that can reach the broker or open a position, by module
# name, so main.py and this contract cannot drift apart silently. ``main.py`` must
# start each of them unconditionally.
ALWAYS_RUNNING_RUNNERS: FrozenSet[str] = frozenset({
    "gamma_move_runner",
    "adaptive_edge_runner",
    "intraday_runner",
    "kite_engine",
})

# Archived runners default to maintenance only until per-user durable opt-in.
# Disabling never stops reconciliation, protection or exits.
MAINTENANCE_ONLY_RUNNERS: FrozenSet[str] = frozenset({
    "adaptive_edge_runner",
    "intraday_runner",
})


def always_running_runners() -> FrozenSet[str]:
    """Runners ``main.py`` must start whatever the environment says."""
    return ALWAYS_RUNNING_RUNNERS


def maintenance_only_runners() -> FrozenSet[str]:
    """Archived runners that default to maintenance-only without explicit opt-in."""
    return MAINTENANCE_ONLY_RUNNERS


def bind_host() -> str:
    """Where the API listens. Closed by default.

    0.0.0.0 on a home network exposes an authenticated broker session to every
    other device on that network. Opening it is a deliberate act, so it takes an
    explicit environment variable.
    """
    return os.environ.get("STERLING_BIND_HOST", "127.0.0.1")


def record_shutdown(*, reason: str = "SIGTERM", warehouse: Any = None) -> Optional[Dict[str, Any]]:
    """Record that this process stopped on purpose.

    A process that crashed and one that was stopped cleanly leave identical
    evidence behind unless one of them says so. Without this, a gap in the
    session record cannot be told apart from a runtime that died mid-session.
    """
    stopped_at = datetime.now(timezone.utc).isoformat()
    payload = {"event": "RUNTIME_SHUTDOWN", "reason": reason, "stopped_at": stopped_at}

    try:
        if warehouse is None:
            from app.services.snapback_prospective_collector import (
                SnapbackObservationWarehouse,
            )

            warehouse = SnapbackObservationWarehouse()
        from app.services.snapback_health import record_cycle

        record_cycle("runtime_shutdown")
    except Exception as exc:  # noqa: BLE001 - shutdown must never raise
        log.warning("Snapback shutdown record failed: %s", exc)
        return None

    log.warning("Snapback runtime stopped (%s) at %s", reason, stopped_at)
    return payload
