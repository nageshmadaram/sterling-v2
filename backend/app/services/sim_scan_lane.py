"""A lane of its own for the virtual Simulation tenant's periodic scan passes.

The live loops (Gamma Unwind's 5 s cycle, the Supertrend scan, the Snapback dispatcher) walk their tenants one
after the other. The virtual tenant must not sit in that walk: a slow or throttled virtual pass (a universe
scan over the shared Kite limiters, a quote read that waits out its timeout) would add its whole duration to the
live cadence of reconcile, protection resync and reference-time sweeps. So the live loops take their real tenants
first, in order, awaiting each, and hand every virtual tenant to :func:`spawn`: one detached pass per
(loop, tenant), single flight, cut off after a timeout, its failure logged and never raised.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

from app.services.simulation.identity import is_sim_uid

log = logging.getLogger(__name__)

#: A virtual pass that has not finished by now is cut off so a hung one cannot hold its lane forever.
PASS_TIMEOUT_S = 300.0

_tasks: Dict[Tuple[str, str], "asyncio.Task"] = {}


def split(uids: Iterable[str]) -> Tuple[List[str], List[str]]:
    """``(real tenants, virtual tenants)``, each in the order given."""
    real: List[str] = []
    virtual: List[str] = []
    for uid in uids:
        (virtual if is_sim_uid(uid) else real).append(uid)
    return real, virtual


def spawn(kind: str, uid: str, factory: Callable[[], Awaitable[Any]],
          *, timeout_s: Optional[float] = None) -> bool:
    """Start one detached pass of ``kind`` for the virtual tenant ``uid`` unless its previous pass is still
    running (then False). Never awaits, never raises; needs a running event loop."""
    key = (str(kind), str(uid))
    try:
        loop = asyncio.get_running_loop()
        task = _tasks.get(key)
        if task is not None and not task.done() and task.get_loop() is loop:
            return False
        _tasks[key] = loop.create_task(_run(key, factory, float(timeout_s or PASS_TIMEOUT_S)))
        return True
    except Exception as exc:  # noqa: BLE001 - the live loop that called this must go on
        log.warning("simulation %s pass for %s not started: %s", kind, uid, exc)
        return False


async def _run(key: Tuple[str, str], factory: Callable[[], Awaitable[Any]], timeout_s: float) -> None:
    kind, uid = key
    try:
        # Everything this detached pass awaits (and any task it spawns) inherits the flag, so its
        # Kite REST calls yield the shared rate-limit budget to real trading calls.
        from app.services.exchanges.kite.client import BACKGROUND_CALLS

        BACKGROUND_CALLS.set(True)
        await asyncio.wait_for(factory(), timeout=timeout_s)
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        log.warning("simulation %s pass for %s timed out after %gs; cut off", kind, uid, timeout_s)
    except Exception as exc:  # noqa: BLE001
        log.warning("simulation %s pass for %s failed: %s", kind, uid, exc)
    finally:
        if _tasks.get(key) is asyncio.current_task():
            _tasks.pop(key, None)


def running(kind: Optional[str] = None) -> int:
    """How many virtual passes are in flight (of one ``kind``, or all)."""
    return sum(1 for (k, _u), t in _tasks.items() if not t.done() and (kind is None or k == kind))


async def drain() -> None:
    """Wait for every in-flight virtual pass (the tests' and the shutdown's join point)."""
    while True:
        live = [t for t in _tasks.values() if not t.done()]
        if not live:
            return
        await asyncio.gather(*live, return_exceptions=True)


def cancel_all() -> None:
    for task in list(_tasks.values()):
        try:
            task.cancel()
        except RuntimeError:                       # its loop is already closed
            pass
    _tasks.clear()
