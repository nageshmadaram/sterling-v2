"""Per-strategy mode (MANUAL / SIMULATION / AUTO), the config that seeds the virtual tenant, and its reset.

Each strategy has exactly one mode, and the mode is nothing more than which tenant's AUTO is on:

* ``MANUAL``      neither the owner's (live) nor the virtual tenant's strategy AUTO is on;
* ``SIMULATION``  the virtual tenant's strategy AUTO is on and the owner's is off;
* ``AUTO``        the owner's strategy AUTO is on (real money) and the virtual tenant's is off.

A strategy is armed for a tenant when the tenant's master ``auto_execute`` (engine config, shared by
the three strategies) AND the strategy's own flag are on: Supertrend's ``supertrend_auto_execute``,
Gamma's and Snapback's ``auto_execute`` in their own configs. Nothing else is touched, so a change of
one strategy never changes another.

Rules the code below keeps:

* switching never leaves both tenants armed: the side being left is disarmed FIRST, the master switch
  is turned on LAST, so a failure halfway leaves the strategy off, never on in both places;
* AUTO is the same decision as today's live AUTO: it runs the same readiness check the config routes
  use (``autoexec_enable_blockers``); SIMULATION runs it against the virtual tenant's own books;
* turning a master switch on never arms a strategy nobody asked for (Supertrend's own flag defaults to
  on, so the others are disarmed first when the master was off);
* the virtual tenant is seeded with a COPY of the owner's config the first time it is needed, with a
  few safe values: nothing armed, a broker-capable stop mode, a daily-loss cap.

Nothing here talks to a broker, the kill switch or SAFE_MODE.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from typing import Callable, Dict, List

from app.core.logging import get_logger
from app.services.simulation import identity, tenant

log = get_logger(__name__)

STRATEGIES = ("supertrend", "snapback", "gamma")
MANUAL, SIMULATION, AUTO = "MANUAL", identity.MODE_SIMULATION, "AUTO"
MODES = (MANUAL, SIMULATION, AUTO)
#: Daily-loss cap (% of virtual capital) seeded when the owner's engine config has none.
SEED_MAX_DAILY_LOSS_PCT = 2.0

_lock = threading.RLock()


class ModeError(Exception):
    """A refused mode action. ``status`` is the HTTP status the API maps it to."""

    status = 400

    def __init__(self, code: str, message: str, reasons: List[str] | None = None):
        super().__init__(message)
        self.code, self.message, self.reasons = code, message, list(reasons or [])


class ModeBlocked(ModeError):
    """AUTO / SIMULATION was not switched on: the readiness check found something unresolved."""

    status = 409


class ModeForbidden(ModeError):
    status = 403


def check_strategy(value: object) -> str:
    strategy = str(value or "").strip().lower()
    if strategy not in STRATEGIES:
        raise ModeError("invalid_strategy", f"strategy must be one of {', '.join(STRATEGIES)}")
    return strategy


def check_mode(value: object) -> str:
    mode = str(value or "").strip().upper()
    if mode not in MODES:
        raise ModeError("invalid_mode", f"mode must be one of {', '.join(MODES)}")
    return mode


def _protective(stop_mode: str) -> str:
    """The virtual broker holds the stop, so a monitor-only stop mode is seeded as ``both``."""
    return "both" if stop_mode == "monitor" else stop_mode


# ─── stored config ──────────────────────────────────────────────────────────
def _has_row(key: str) -> bool:
    from app.services import db

    return bool(db.get_config(key))


def _engine_key(uid: str) -> str:
    return f"kite_engine_config_{uid}"


def _engine_cfg(uid: str):
    from app.services.kite_engine import state

    return state.get_config(uid)


def _engine_put(uid: str, **update):
    from app.services.kite_engine import state

    return state.set_config(uid, state.get_config(uid).model_copy(update=update))


# ─── one adapter per strategy: its own flag, and how its config is copied ───────
class _Supertrend:
    name = "supertrend"

    def key(self, uid): return _engine_key(uid)
    def flag(self, uid): return bool(_engine_cfg(uid).supertrend_auto_execute)
    def enabled(self, uid): return bool(_engine_cfg(uid).engine_enabled)
    def set_flag(self, uid, on): _engine_put(uid, supertrend_auto_execute=bool(on))
    def set_enabled(self, uid, on): _engine_put(uid, engine_enabled=bool(on))
    def seed(self, owner, uid): _seed_engine(owner, uid)


class _Gamma:
    name = "gamma"

    def key(self, uid): return f"gamma_move_config:{uid}"
    def flag(self, uid): return bool(self._svc().get_config(uid).auto_execute)
    def enabled(self, uid): return bool(self._svc().get_config(uid).enabled)
    def set_flag(self, uid, on): self._svc().set_config({"auto_execute": bool(on)}, uid=uid)
    def set_enabled(self, uid, on): self._svc().set_config({"enabled": bool(on)}, uid=uid)

    @staticmethod
    def _svc():
        from app.services import gamma_move

        return gamma_move

    def seed(self, owner, uid):
        _seed_engine(owner, uid)
        if not _has_row(self.key(uid)):
            values = self._svc().get_config(owner).as_dict()
            values.update(auto_execute=False, enabled=True, stop_mode=_protective(values.get("stop_mode", "both")))
            self._svc().set_config(values, uid=uid)


class _Snapback:
    name = "snapback"

    def key(self, uid): return f"snapback_config:{uid}"
    def flag(self, uid): return bool(self._svc().get_config(uid).auto_execute)
    def enabled(self, uid): return bool(self._svc().get_config(uid).enabled)
    def set_flag(self, uid, on): self._svc().set_config({"auto_execute": bool(on)}, uid=uid)
    def set_enabled(self, uid, on): self._svc().set_config({"enabled": bool(on)}, uid=uid)

    @staticmethod
    def _svc():
        from app.services import snapback

        return snapback

    def seed(self, owner, uid):
        _seed_engine(owner, uid)
        if not _has_row(self.key(uid)):
            values = self._svc().get_config(owner).as_dict()
            values.update(auto_execute=False, enabled=True, stop_mode=_protective(values.get("stop_mode", "both")))
            self._svc().set_config(values, uid=uid)


_ADAPTERS: Dict[str, object] = {a.name: a for a in (_Supertrend(), _Snapback(), _Gamma())}


def _seed_engine(owner: str, uid: str) -> bool:
    """Copy the owner's engine config to the virtual tenant if it has none. True when a copy was made.

    The engine config carries the master AUTO switch, risk sizing, stop mode and the Supertrend
    settings, so every strategy needs it. Safe values: nothing armed, the engine on, a stop mode the
    virtual broker can hold, and a daily-loss cap (a missing one would refuse automatic entries).
    """
    if _has_row(_engine_key(uid)):
        return False
    source = _engine_cfg(owner)
    cap = source.max_daily_loss_pct
    from app.services.kite_engine import state

    state.set_config(uid, source.model_copy(update={
        "auto_execute": False, "supertrend_auto_execute": False, "engine_enabled": True,
        "stop_mode": _protective(source.stop_mode),
        "max_daily_loss_pct": float(cap) if cap and cap > 0 else SEED_MAX_DAILY_LOSS_PCT,
    }))
    return True


def seed_strategy(owner: str, strategy: str) -> bool:
    """Seed the virtual tenant's config for ``strategy`` from the owner's (idempotent). True if copied."""
    owner = identity.owner_uid(owner)
    uid = identity.sim_uid(owner)
    adapter = _ADAPTERS[check_strategy(strategy)]
    before = _has_row(adapter.key(uid))
    adapter.seed(owner, uid)
    return not before and _has_row(adapter.key(uid))


# ─── reading the mode ───────────────────────────────────────────────────────
def _master(uid: str) -> bool:
    return bool(_engine_cfg(uid).auto_execute)


def _side(uid: str, strategy: str) -> dict:
    adapter = _ADAPTERS[strategy]
    master, flag = _master(uid), adapter.flag(uid)
    return {"master": master, "strategy": flag, "armed": master and flag, "enabled": adapter.enabled(uid)}


def strategy_state(owner: str, strategy: str) -> dict:
    """One strategy's mode and the switches behind it, for both tenants."""
    owner = identity.owner_uid(owner)
    strategy = check_strategy(strategy)
    live, sim = _side(owner, strategy), _side(identity.sim_uid(owner), strategy)
    mode = AUTO if live["armed"] else SIMULATION if sim["armed"] else MANUAL
    return {
        "strategy": strategy, "mode": mode,
        # real money wins the label when both are armed: that state needs attention, so it is flagged
        "conflict": live["armed"] and sim["armed"],
        "live": live, "simulation": {**sim, "configured": _has_row(_ADAPTERS[strategy].key(identity.sim_uid(owner)))},
    }


def modes(owner: str) -> Dict[str, dict]:
    return {s: strategy_state(owner, s) for s in STRATEGIES}


# ─── switching ──────────────────────────────────────────────────────────────
def _blockers(uid: str) -> List[str]:
    """The readiness check every live AUTO switch runs (registry and durable journal, no broker call)."""
    try:
        from app.services.kite_engine import service

        return list(service.autoexec_enable_blockers(uid, was_on=False, now_on=True))
    except Exception as exc:  # noqa: BLE001 - an unreadable check is not a clear one
        return [f"Readiness check unavailable: {exc}"]


def _disarm(uid: str, strategy: str) -> None:
    """Turn the strategy's own flag off (the master is shared and is left alone)."""
    adapter = _ADAPTERS[strategy]
    if identity.is_sim_uid(uid) and not _has_row(_engine_key(uid)):
        return                                   # never seeded: nothing is armed, and nothing to create
    if adapter.flag(uid):
        adapter.set_flag(uid, False)


def _arm(uid: str, strategy: str) -> None:
    """Arm the strategy for ``uid``: its own flag first, the shared master LAST."""
    adapter = _ADAPTERS[strategy]
    if not _master(uid):
        # The master is off, so whatever flags are on are dormant. Arming the master would wake them,
        # and Supertrend's own flag defaults to on: disarm the others first.
        for other in STRATEGIES:
            if other != strategy:
                _disarm(uid, other)
    if identity.is_sim_uid(uid) and not adapter.enabled(uid):
        adapter.set_enabled(uid, True)           # choosing SIMULATION means run it
    if not adapter.flag(uid):
        adapter.set_flag(uid, True)
    if not _master(uid):
        _engine_put(uid, auto_execute=True)


def _settle_sim_master(owner: str) -> None:
    """Switch the virtual tenant's master off once none of its strategies is armed (it is inert then)."""
    uid = identity.sim_uid(owner)
    if not _has_row(_engine_key(uid)) or not _master(uid):
        return
    if not any(_ADAPTERS[s].flag(uid) for s in STRATEGIES):
        _engine_put(uid, auto_execute=False)


def set_mode(owner: str, strategy: str, mode: str, *, allow_live: bool = True) -> dict:
    """Put ``strategy`` in ``mode``. Returns its new state plus ``changed``.

    ``allow_live=False`` (the Simulation view) refuses AUTO: arming real money is a decision for the
    live view. Raises :class:`ModeBlocked` (409) when the readiness check refuses AUTO/SIMULATION.
    """
    owner = identity.owner_uid(owner)
    strategy, mode = check_strategy(strategy), check_mode(mode)
    if mode == AUTO and not allow_live:
        raise ModeForbidden("not_available_in_simulation", "Not available in Simulation view")
    uid = identity.sim_uid(owner)
    with _lock:
        before = strategy_state(owner, strategy)
        if mode == AUTO:
            if not before["live"]["armed"]:
                _require_clear(owner, "AUTO")
        elif mode == SIMULATION:
            tenant.boot_tenant(owner)
            if not before["simulation"]["armed"]:
                _require_clear(uid, "Simulation")
        try:
            if mode == AUTO:
                _disarm(uid, strategy)
                _arm(owner, strategy)
            elif mode == SIMULATION:
                _disarm(owner, strategy)
                seed_strategy(owner, strategy)
                _arm(uid, strategy)
            else:
                _disarm(owner, strategy)
                _disarm(uid, strategy)
            _settle_sim_master(owner)
        except ModeError:
            raise
        except Exception as exc:  # noqa: BLE001
            # The order above leaves the strategy off, never armed in both places, on any failure.
            log.error("mode change %s -> %s failed: %s", strategy, mode, exc)
            raise ModeError("mode_change_failed", f"{strategy} was not switched to {mode}: {exc}") from exc
        after = strategy_state(owner, strategy)
    log.warning("strategy mode: %s %s -> %s", strategy, before["mode"], after["mode"])
    return {**after, "changed": before["mode"] != after["mode"], "previous": before["mode"]}


def _require_clear(uid: str, label: str) -> None:
    reasons = _blockers(uid)
    if reasons:
        raise ModeBlocked("auto_execute_blocked",
                          f"{label} was not switched on - resolve these conditions first.", reasons)


# ─── reset ──────────────────────────────────────────────────────────────────
#: Rows in ``system_config`` that belong to a tenant (``{u}`` is the sim uid).
_CONFIG_KEYS = (
    "kite_engine_config_{u}", "kite_engine_positions_{u}", "kite_engine_auto_open_{u}",
    "kite_engine_daily_pnl_{u}", "kite_engine_signals_{u}",
    "gamma_move_config:{u}", "gamma_move_positions_{u}", "gamma_move_record_{u}",
    "gamma_move_legacy_positions_{u}", "snapback_config:{u}",
)


def _purge_config_rows(uid: str) -> int:
    from app.services import db

    keys = [pattern.format(u=uid) for pattern in _CONFIG_KEYS]
    with db.connection() as conn:
        conn.executemany("DELETE FROM system_config WHERE key=?", [(k,) for k in keys])
    return len(keys)


def _purge_order_books(uid: str) -> None:
    from app.services.kite_engine import execution_lease, fill_ledger, order_journal

    order_journal.clear_for_tests(uid)           # intents, observations and fills of this tenant only
    fill_ledger.clear_for_tests(uid)
    execution_lease.clear_for_tests(uid)


def _purge_snapback_books(uid: str, account_id: str) -> None:
    from app.services import snapback_live_positions, snapback_plan_store

    with _sqlite_file(snapback_live_positions.default_store().path) as conn:
        conn.execute("DELETE FROM snapback_live_positions WHERE uid=?", (uid,))
    with _sqlite_file(snapback_plan_store.shared_store().db_path) as conn:
        ids = [r[0] for r in conn.execute("SELECT plan_id FROM snapback_plans WHERE account_id=?", (account_id,))]
        for plan_id in ids:
            conn.execute("DELETE FROM snapback_plan_events WHERE plan_id=?", (plan_id,))
        conn.execute("DELETE FROM snapback_plans WHERE account_id=?", (account_id,))


@contextmanager
def _sqlite_file(path):
    conn = sqlite3.connect(str(path), timeout=15.0)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _forget_memory(uid: str) -> None:
    """Drop the in-process caches of this tenant so nothing re-writes what was just deleted."""
    from app.services import gamma_move_positions, gamma_move_runner, gamma_move_scanner, snapback
    from app.services.kite_engine import positions, state

    positions.reset(uid)
    state.reset(uid)
    gamma_move_positions.reset(uid)
    gamma_move_runner.clear(uid)
    gamma_move_scanner.clear_runtime(uid)
    gamma_move_scanner.clear_levels(uid)
    snapback._state.pop(uid, None)


def reset_simulation(owner: str, *, capital: float | None = None) -> dict:
    """Start the virtual tenant from nothing: ledger, positions, order intents, journal rows and config.

    Everything deleted is keyed by the tenant's own uid / account id (``sim:<owner>`` / ``SIM-...``);
    a live tenant's rows are never selected. The tenant is then booted again (account, control plane,
    daily-loss limits) so it is ready to be switched back on. Capital and ledger settings survive.
    """
    from app.services import trade_journal
    from app.services.simulation import sim_runtime

    owner = identity.owner_uid(owner)
    uid, account_id = tenant.scope_ids(owner)
    assert identity.is_sim_uid(uid) and identity.is_sim_account_id(account_id)
    cleared: Dict[str, object] = {}
    failed: Dict[str, str] = {}

    def step(name: str, fn: Callable[[], object]) -> None:
        try:
            cleared[name] = fn()
        except Exception as exc:  # noqa: BLE001 - keep going; the report says what did not clear
            failed[name] = f"{type(exc).__name__}: {exc}"
            log.error("simulation reset: %s failed: %s", name, exc)

    with _lock:
        for strategy in STRATEGIES:                              # nothing may originate while it is wiped
            try:
                _disarm(uid, strategy)
            except Exception as exc:  # noqa: BLE001
                failed[f"disarm_{strategy}"] = str(exc)
        _settle_sim_master(owner)
        account = sim_runtime.reset_owner(owner, capital=capital)  # the one step that must not be skipped
        step("journal_rows", lambda: trade_journal.purge_simulation(uid))
        step("order_books", lambda: _purge_order_books(uid))
        step("snapback_books", lambda: _purge_snapback_books(uid, account_id))
        step("config_rows", lambda: _purge_config_rows(uid))
        step("memory", lambda: _forget_memory(uid))
        step("tenant", lambda: tenant.boot_tenant(owner))        # account, limits on the (kept) virtual capital
        step("control_plane", lambda: _reset_control_plane(owner))
    return {"ok": not failed, "cleared": sorted(cleared), "failed": failed, "capital": float(account["capital"]),
            "cash": float(account["cash"]), "strategies": modes(owner)}


def _reset_control_plane(owner: str) -> None:
    """A tenant fenced as RECOVERY_REQUIRED by an unsettled order is clean once its books are empty."""
    from app.services import db

    uid, account_id = tenant.scope_ids(owner)
    db.set_execution_control(
        operator_state="RUNNING", recovery_state="CLEAN", reason_code="SIM_RESET",
        reason="virtual books reset by the owner", actor=tenant.ACTOR, uid=uid, account_id=account_id,
    )


__all__ = [
    "AUTO", "MANUAL", "MODES", "SIMULATION", "STRATEGIES", "ModeBlocked", "ModeError", "ModeForbidden",
    "check_mode", "check_strategy", "modes", "reset_simulation", "seed_strategy", "set_mode", "strategy_state",
]
