"""Trade Journal API: what each strategy did, with live and simulated trades side by side.

Everything here is read-only (GET). A caller sees the trades of the tenant they are acting as:
in the LIVE view their own account, in the SIMULATION view (``X-Sterling-View: simulation``) the
virtual tenant. ``mode=live|simulation|all`` picks explicitly; asking for a tenant other than your
own, or for ``all`` (live and simulation combined), is for the owner only.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import UserContext, get_current_user
from app.services import trade_journal as journal
from app.services import trade_journal_stats as stats
from app.services.simulation import identity

router = APIRouter(prefix="/journal", tags=["journal"])

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MODES = ("live", "simulation", "all")


def _owner(user: UserContext) -> bool:
    """The authenticated owner: the admin, or the owner acting in the SIMULATION view."""
    return user.role == "admin" or identity.is_sim_uid(user.user_id)


def scope(user: UserContext, mode: Optional[str]) -> Tuple[str, List[str]]:
    """``(mode label, tenant uids)`` the caller may read for ``mode``.

    The default follows the view: a simulation caller reads SIMULATION, anyone else LIVE. Another
    tenant than your own (``simulation`` from a live view, or ``all``) needs the owner. The uids are
    derived here and never taken from the request, so a caller cannot name someone else's tenant.
    """
    own = str(user.user_id)
    owner_uid = identity.owner_uid(own)
    requested = (mode or "").strip().lower() or ("simulation" if identity.is_sim_uid(own) else "live")
    if requested not in MODES:
        raise HTTPException(400, detail={"code": "invalid_mode", "message": "mode must be live, simulation or all"})
    if requested == "live":
        return "LIVE", [owner_uid]
    if requested == "simulation":
        if not identity.is_sim_uid(own) and not _owner(user):
            raise HTTPException(403, detail={"code": "owner_only", "message": "Simulation is available to the owner only."})
        return "SIMULATION", [identity.sim_uid(owner_uid)]
    if not _owner(user):
        raise HTTPException(403, detail={"code": "owner_only", "message": "Combined live and simulation view is for the owner only."})
    return "ALL", [owner_uid, identity.sim_uid(owner_uid)]


def _date(value: Optional[str], name: str) -> Optional[str]:
    if value in (None, ""):
        return None
    if not _DATE.match(value):
        raise HTTPException(400, detail={"code": "invalid_date", "message": f"{name} must be YYYY-MM-DD"})
    return value


def _filters(strategy: Optional[str], date_from: Optional[str], date_to: Optional[str]) -> Dict[str, Any]:
    if strategy and strategy.lower() not in journal.STRATEGIES:
        raise HTTPException(400, detail={"code": "invalid_strategy",
                                         "message": f"strategy must be one of {', '.join(journal.STRATEGIES)}"})
    return {"strategy": strategy.lower() if strategy else None, "date_from": _date(date_from, "date_from"),
            "date_to": _date(date_to, "date_to")}


def _rows(user: UserContext, mode: Optional[str], strategy: Optional[str], date_from: Optional[str],
          date_to: Optional[str]) -> Tuple[str, List[Dict[str, Any]]]:
    label, uids = scope(user, mode)
    return label, journal.rows_for_stats(uids, **_filters(strategy, date_from, date_to))


def _by_mode(rows: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(str(r.get("mode") or ""), []).append(r)
    return out


@router.get("/scope")
async def get_scope(user: UserContext = Depends(get_current_user)) -> Dict[str, Any]:
    """Which views this caller may read and which one is the default."""
    default = "SIMULATION" if identity.is_sim_uid(user.user_id) else "LIVE"
    owner = _owner(user)
    return {"default_mode": default, "modes": ["live", "simulation", "all"] if owner else ["live"],
            "can_view_all": owner, "strategies": list(journal.STRATEGIES),
            "exit_reasons": list(journal.CANONICAL_EXIT_REASONS)}


@router.get("/trades")
async def list_trades(
    mode: Optional[str] = Query(None, description="live | simulation | all (default: the current view)"),
    strategy: Optional[str] = Query(None), outcome: Optional[str] = Query(None, description="win | loss | open"),
    date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None), exit_reason: Optional[str] = Query(None),
    sort: str = Query("newest", description="newest | oldest | pnl_desc | pnl_asc | hold_desc | symbol | strategy"),
    limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
    user: UserContext = Depends(get_current_user),
) -> Dict[str, Any]:
    label, uids = scope(user, mode)
    if outcome and outcome.lower() not in journal.OUTCOMES:
        raise HTTPException(400, detail={"code": "invalid_outcome", "message": "outcome must be win, loss or open"})
    if sort not in ("newest", "oldest", "pnl_desc", "pnl_asc", "hold_desc", "symbol", "strategy"):
        raise HTTPException(400, detail={"code": "invalid_sort", "message": "unknown sort"})
    page = journal.list_trades(uids, sort=sort, limit=limit, offset=offset, outcome=outcome.lower() if outcome else None,
                               symbol=symbol or None, exit_reason=exit_reason or None,
                               **_filters(strategy, date_from, date_to))
    return {"mode": label, **page}


@router.get("/trades/{trade_id}")
async def get_trade(trade_id: str, mode: Optional[str] = Query(None),
                    user: UserContext = Depends(get_current_user)) -> Dict[str, Any]:
    """One trade in full: every figure, the signal that caused it, the story and the ordered timeline.

    Looked up only inside the caller's tenants, so another tenant's id answers 404, not 403."""
    label, uids = scope(user, mode)
    trade = journal.get_trade(trade_id, uids)
    if trade is None:
        raise HTTPException(404, detail={"code": "trade_not_found", "message": "No such trade in this view."})
    return {"mode": label, "trade": trade}


@router.get("/summary")
async def get_summary(mode: Optional[str] = Query(None), strategy: Optional[str] = Query(None),
                      date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None),
                      user: UserContext = Depends(get_current_user)) -> Dict[str, Any]:
    """Headline numbers: trades, wins, win rate, gross/net P&L, avg win/loss, profit factor,
    expectancy, best/worst trade, max drawdown, average hold, capital used. ``by_mode`` splits them
    per mode; ``summary`` is the combined figure for what the caller asked for."""
    label, rows = _rows(user, mode, strategy, date_from, date_to)
    return {"mode": label, "summary": stats.summary(rows),
            "by_mode": {m: stats.summary(group) for m, group in _by_mode(rows).items()}}


@router.get("/by-strategy")
async def get_by_strategy(mode: Optional[str] = Query(None), date_from: Optional[str] = Query(None),
                          date_to: Optional[str] = Query(None),
                          user: UserContext = Depends(get_current_user)) -> Dict[str, Any]:
    """The strategy comparison table: which one makes money, and what costs each the most."""
    label, rows = _rows(user, mode, None, date_from, date_to)
    return {"mode": label, "strategies": stats.by_strategy(rows),
            "by_mode": {m: stats.by_strategy(group) for m, group in _by_mode(rows).items()}}


@router.get("/exit-reasons")
async def get_exit_reasons(mode: Optional[str] = Query(None), strategy: Optional[str] = Query(None),
                           date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None),
                           user: UserContext = Depends(get_current_user)) -> Dict[str, Any]:
    """How trades end: count and P&L per canonical exit reason."""
    label, rows = _rows(user, mode, strategy, date_from, date_to)
    return {"mode": label, "reasons": stats.exit_reasons(rows)}


@router.get("/equity")
async def get_equity(mode: Optional[str] = Query(None), strategy: Optional[str] = Query(None),
                     date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None),
                     user: UserContext = Depends(get_current_user)) -> Dict[str, Any]:
    """Cumulative net P&L per closed trade and per day (``by_mode`` gives one curve per mode)."""
    label, rows = _rows(user, mode, strategy, date_from, date_to)
    return {"mode": label, **stats.equity(rows),
            "by_mode": {m: stats.equity(group) for m, group in _by_mode(rows).items()}}


@router.get("/calendar")
async def get_calendar(mode: Optional[str] = Query(None), strategy: Optional[str] = Query(None),
                       date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None),
                       user: UserContext = Depends(get_current_user)) -> Dict[str, Any]:
    """Net P&L for each day a trade closed."""
    label, rows = _rows(user, mode, strategy, date_from, date_to)
    return {"mode": label, "days": stats.calendar(rows)}


@router.get("/skips")
async def get_skips(mode: Optional[str] = Query(None), strategy: Optional[str] = Query(None),
                    code: Optional[str] = Query(None), date_from: Optional[str] = Query(None),
                    date_to: Optional[str] = Query(None), limit: int = Query(50, ge=1, le=500),
                    offset: int = Query(0, ge=0),
                    user: UserContext = Depends(get_current_user)) -> Dict[str, Any]:
    """Signals the strategies declined or were blocked on, with the reason in plain English."""
    label, uids = scope(user, mode)
    f = _filters(strategy, date_from, date_to)
    page = journal.list_skips(uids, strategy=f["strategy"], code=code or None, date_from=f["date_from"],
                              date_to=f["date_to"], limit=limit, offset=offset)
    return {"mode": label, **page}
