"""The Simulation background loop and the small public API around the virtual books.

While the market is open the loop, every couple of seconds, works every owner that has open
virtual state: it marks positions to the real price, fires GTTs, and fills resting orders, using
the owner's own live session as the price source. Once a minute it writes an equity snapshot for
every owner with a virtual account. A pass can never raise into the application: an owner whose
session is gone is simply not priced that pass (nothing is invented), and any other failure is
logged and the loop carries on.

Public API (all keyed by the owner uid; a ``sim:<owner>`` uid is accepted and means the same):

* :func:`start_sim_runtime` / :func:`stop_sim_runtime`   idempotent start (sync, returns the task), clean stop
* :func:`run_pass`                                         one evaluation pass (also what the loop calls)
* :func:`on_ticks`                                         optional: owner ticks that cross a trigger run a pass at once
* :func:`get_sim_client`                                   the tenant's Kite client (re-exported)
* :func:`status`                                           capital, cash, equity, realised/unrealised today
* :func:`reset_owner`, :func:`set_capital`                 the operator actions
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import asdict
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from app.core.logging import get_logger
from app.services.simulation.identity import MODE_SIMULATION, owner_uid, sim_account_id, sim_uid
from app.services.simulation.ledger import IST, SimLedger, get_ledger
from app.services.simulation.quotes import QuoteSource, RealClientProvider
from app.services.simulation.sim_client import SimulatedKiteClient, default_postback_sink, get_sim_client
from app.services.simulation.virtual_exchange import VirtualExchange

log = get_logger(__name__)

DEFAULT_INTERVAL_S = 2.0
SNAPSHOT_EVERY_S = 60

_exchanges: Dict[str, VirtualExchange] = {}
_task: Optional["asyncio.Task"] = None
_handle: Optional["RuntimeHandle"] = None
_lock: Optional[asyncio.Lock] = None
_lock_loop: Optional[int] = None
_last_snapshot_bucket: Dict[str, int] = {}
_state: Dict[str, Any] = {"passes": 0, "last_pass_ms": 0, "last_error": "", "last_report": {}}
_sleep: Callable = asyncio.sleep


# ── session / provider defaults (lazy so importing this module stays light) ────────────────
def in_session(now: Optional[datetime] = None) -> bool:
    """Whether open positions are managed right now (the NSE/NFO continuous session)."""
    try:
        from app.services.kite_engine import market_hours
        return bool(market_hours.is_management_session(now or datetime.now(IST), exchange="NFO"))
    except Exception as exc:  # noqa: BLE001 - cannot prove the market is open: closed
        log.debug("sim runtime: session check failed: %s", exc)
        return False


def owner_provider(owner: str) -> RealClientProvider:
    """The owner's LIVE Kite client (price source), or raise when there is no live session."""
    from app.services.exchanges.kite import accounts
    return accounts.owner_live_client_provider(owner_uid(owner))


# ── per-owner exchange used by the loop ───────────────────────────────────────────────────
def runtime_exchange(owner: str, *, ledger: Optional[SimLedger] = None,
                     provider: Optional[RealClientProvider] = None,
                     clock: Optional[Callable] = None) -> VirtualExchange:
    """The loop's exchange for ``owner`` (built once). It shares every row with the tenant's client."""
    o = owner_uid(owner)
    ex = _exchanges.get(o)
    if ex is None:
        led = ledger or get_ledger()
        quotes = QuoteSource(provider or owner_provider(o), owner=o)
        ex = VirtualExchange(o, led, quotes, clock=clock)
        ex.set_postback_sink(default_postback_sink(o))
        _exchanges[o] = ex
    return ex


def forget_exchanges() -> None:
    _exchanges.clear()
    _last_snapshot_bucket.clear()
    _watch.clear()
    _inflight.clear()


def _pass_lock() -> asyncio.Lock:
    global _lock, _lock_loop
    loop = id(asyncio.get_running_loop())
    if _lock is None or _lock_loop != loop:
        _lock, _lock_loop = asyncio.Lock(), loop
    return _lock


# ── one pass ──────────────────────────────────────────────────────────────────────────────
async def run_pass(*, owners: Optional[List[str]] = None, now: Optional[datetime] = None, force: bool = False,
                   ledger: Optional[SimLedger] = None, session_open: Optional[Callable[[datetime], bool]] = None,
                   exchange_for: Optional[Callable[[str], VirtualExchange]] = None) -> dict:
    """Evaluate resting orders, GTTs and marks for every owner with open state; snapshot equity each minute.

    ``force`` evaluates outside the session too (tests, an operator "refresh"). Never raises."""
    report: Dict[str, Any] = {"owners": [], "errors": {}, "session": False, "snapshots": 0}
    try:
        led = ledger or get_ledger()
        moment = (now or datetime.now(IST)).astimezone(IST)
        live = bool((session_open or in_session)(moment)) or force
        report["session"] = live
        build = exchange_for or (lambda o: runtime_exchange(o, ledger=led))
        async with _pass_lock():
            targets = [owner_uid(o) for o in owners] if owners is not None else led.owners_with_open_state()
            for owner in targets:
                try:
                    ex = build(owner)
                    if live:
                        report["owners"].append({"owner": owner, **await ex.evaluate()})
                    else:
                        report["owners"].append({"owner": owner, "expired": ex.expire_day_orders()})
                except Exception as exc:  # noqa: BLE001 - one owner's trouble never stops the others
                    report["errors"][owner] = f"{type(exc).__name__}: {exc}"
                    log.warning("sim runtime: pass failed for %s: %s", owner, exc)
            for owner in targets:
                try:
                    refresh_watch(owner, ledger=led)
                except Exception as exc:  # noqa: BLE001
                    log.debug("sim runtime: watch refresh failed for %s: %s", owner, exc)
            if live:
                report["snapshots"] = _snapshot_all(led, moment, owners)
        _state.update(passes=_state["passes"] + 1, last_pass_ms=int(time.time() * 1000),
                      last_error="; ".join(report["errors"].values()), last_report=report)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        report["errors"]["runtime"] = f"{type(exc).__name__}: {exc}"
        _state["last_error"] = report["errors"]["runtime"]
        log.warning("sim runtime: pass failed: %s", exc)
    return report


def _snapshot_all(led: SimLedger, moment: datetime, owners: Optional[List[str]]) -> int:
    """One equity point per owner per minute, marked at the last real prices the pass just stored."""
    bucket = int(moment.timestamp() // SNAPSHOT_EVERY_S)
    written = 0
    for owner in ([owner_uid(o) for o in owners] if owners is not None else led.owners()):
        if _last_snapshot_bucket.get(owner) == bucket:
            continue
        try:
            led.add_snapshot(owner, led.account_view(owner, moment.date().isoformat()),
                             ts_ms=bucket * SNAPSHOT_EVERY_S * 1000)
            _last_snapshot_bucket[owner] = bucket
            written += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("sim runtime: equity snapshot failed for %s: %s", owner, exc)
    return written


# ── tick-driven GTT / trigger evaluation (optional: the loop alone is correct, this makes it prompt) ──
_watch: Dict[str, Dict[int, List[tuple]]] = {}
_inflight: set = set()


def refresh_watch(owner: str, *, ledger: Optional[SimLedger] = None) -> None:
    """Remember which price levels would trigger something for ``owner`` (active GTTs, pending stop orders)."""
    led, o = ledger or get_ledger(), owner_uid(owner)
    rules: Dict[int, List[tuple]] = {}
    for gtt in led.gtts(o, status="active"):
        token = int(gtt["condition"].get("instrument_token") or 0)
        for direction, trigger in zip(gtt["_sim"]["dirs"], gtt["condition"]["trigger_values"]):
            rules.setdefault(token, []).append((direction, float(trigger)))
    for order in led.open_orders(o):
        if order["status"] == "TRIGGER PENDING":
            direction = "up" if order["transaction_type"] == "BUY" else "down"
            rules.setdefault(int(order["instrument_token"]), []).append((direction, float(order["trigger_price"])))
    if rules:
        _watch[o] = rules
    else:
        _watch.pop(o, None)


def on_ticks(owner: str, ticks: List[dict]) -> bool:
    """Feed the owner's live ticks. When one crosses a watched trigger, schedule an evaluation pass at once
    (instead of waiting for the next loop pass) and return True. Never blocks and never raises: call it from
    the tick fan-out as is. Needs a running event loop."""
    try:
        o = owner_uid(owner)
        rules = _watch.get(o)
        if not rules or o in _inflight:
            return False
        for tick in ticks or []:
            ltp = float(tick.get("last_price") or 0.0)
            levels = rules.get(int(tick.get("instrument_token") or 0))
            if ltp <= 0 or not levels:
                continue
            if any((d == "down" and ltp <= level) or (d == "up" and ltp >= level) for d, level in levels):
                _inflight.add(o)
                asyncio.get_running_loop().create_task(_tick_pass(o))
                return True
    except Exception as exc:  # noqa: BLE001
        log.debug("sim runtime: tick feed ignored: %s", exc)
    return False


async def _tick_pass(owner: str) -> None:
    try:
        ex = _exchanges.get(owner)
        if ex is not None:
            ex.quotes.invalidate()                 # the tick says the price moved: do not serve a cached book
        await run_pass(owners=[owner])
    finally:
        _inflight.discard(owner)


# ── the loop ──────────────────────────────────────────────────────────────────────────────
async def _loop(interval_s: float) -> None:
    first = True
    while True:
        if not first:
            await _sleep(interval_s)
        first = False                              # the first pass runs straight after startup (a restart catch-up)
        try:
            await run_pass()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - run_pass already swallows; this is the last line of defence
            log.warning("sim runtime: loop iteration failed: %s", exc)


class RuntimeHandle:
    """What :func:`start_sim_runtime` returns: the loop's task, usable as one (``cancel``, ``done`` ...).

    It can also be awaited WITHOUT blocking (``await start_sim_runtime()`` yields the task at once), so a
    caller written either in the sync ``task = start()`` style used elsewhere in ``main.py`` or the
    ``await start()`` style gets a running loop and never hangs waiting for it to end."""

    def __init__(self, task) -> None:
        self.task = task

    def __await__(self):
        async def _ready():
            return self.task
        return _ready().__await__()

    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        return getattr(self.task, name)


async def run_sim_runtime(interval_s: float = DEFAULT_INTERVAL_S) -> None:
    """The loop itself, for ``asyncio.create_task(run_sim_runtime())`` in a lifespan."""
    await _loop(float(interval_s))


def start_sim_runtime(*, interval_s: float = DEFAULT_INTERVAL_S) -> RuntimeHandle:
    """Start the loop (idempotent: while it runs, a second call returns the same handle). Needs a running loop."""
    global _task, _handle
    if _handle is not None and not _handle.done():
        return _handle
    _task = asyncio.create_task(_loop(float(interval_s)), name="sim-runtime")
    _handle = RuntimeHandle(_task)
    log.info("simulation runtime started (every %.1fs while the market is open)", interval_s)
    return _handle


async def stop_sim_runtime() -> None:
    global _task, _handle
    task, _task, _handle = _task, None, None
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
    log.info("simulation runtime stopped")


def is_running() -> bool:
    return _task is not None and not _task.done()


# ── status and operator actions ───────────────────────────────────────────────────────────
def status(owner: str, *, ledger: Optional[SimLedger] = None, day: Optional[str] = None) -> dict:
    """The owner's virtual account at a glance (creates the default account on first look)."""
    led = ledger or get_ledger()
    o = owner_uid(owner)
    day = day or datetime.now(IST).date().isoformat()
    view = led.account_view(o, day)
    return {**view, "mode": MODE_SIMULATION, "owner": o, "uid": sim_uid(o), "account_id": sim_account_id(o),
            "running": is_running(), "last_pass_ms": _state["last_pass_ms"], "passes": _state["passes"],
            "last_error": _state["last_error"], "settings": asdict(led.settings(o))}


def reset_owner(owner: str, *, capital: Optional[float] = None, ledger: Optional[SimLedger] = None) -> dict:
    """Wipe the owner's virtual books (orders, fills, positions, GTTs, charges, equity); keep capital and settings."""
    led = ledger or get_ledger()
    o = owner_uid(owner)
    account = led.reset(o, capital=capital)
    _resume_own_halt(o)
    _exchanges.pop(o, None)
    _last_snapshot_bucket.pop(o, None)
    _watch.pop(o, None)
    log.info("simulation books reset for %s (capital %.2f)", o, account["capital"])
    return account


def _resume_own_halt(owner: str) -> None:
    """A reset is a fresh start: lift the tenant's OWN halt (never another reason, never a live scope)."""
    try:
        from app.services import db
        from app.services.simulation import halt
        row = db.get_execution_control(uid=sim_uid(owner), account_id=sim_account_id(owner))
        if row.get("operator_state") == "HALTED" and row.get("reason_code") == halt.SIM_HALT_CODE:
            halt.clear_halt(sim_uid(owner))
    except Exception as exc:  # noqa: BLE001 - the books are already reset; the halt can be lifted by hand
        log.warning("simulation reset: could not lift the tenant's own halt: %s", exc)


def set_capital(owner: str, amount: float, *, ledger: Optional[SimLedger] = None) -> dict:
    """Set the starting virtual capital; cash moves by the same difference."""
    return (ledger or get_ledger()).set_capital(owner_uid(owner), float(amount))


__all__ = [
    "DEFAULT_INTERVAL_S", "SimulatedKiteClient", "get_sim_client", "in_session", "is_running", "owner_provider",
    "reset_owner", "run_pass", "run_sim_runtime", "RuntimeHandle", "runtime_exchange", "forget_exchanges",
    "set_capital", "start_sim_runtime", "status", "stop_sim_runtime", "on_ticks", "refresh_watch",
]
