"""End-of-day close observation and finalisation.

Separated on purpose. The old model executed the whole end-of-day lifecycle inside a
one-minute window, so a backend that restarted at 15:31 lost the session's mark — and
the tempting repair, fetching a quote at 15:34 and calling it the close, fabricates
the number the strategy is scored on.

    15:29:00-15:30:00   observe: fetch and PERSIST close quotes as evidence
    >= 15:30:00         finalise: decide from what was persisted, and nothing else

A restart between the two finalises deterministically from stored evidence, or
records EOD_EVIDENCE_GAP. Both are honest; inventing a close mark is not.
"""

from __future__ import annotations

from app.core.async_work import run_blocking

import logging
import math
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))

EOD_EVIDENCE_GAP = "EOD_EVIDENCE_GAP"
EOD_PHASE = "EOD_MARK"

#: Kite's quote endpoint takes up to 500 instruments; stay well inside it.
_QUOTE_CHUNK = 200


def _window() -> tuple:
    from app.engines.snapback.policy import EXECUTION_POLICY

    start = time.fromisoformat(EXECUTION_POLICY.eod_observation_start)
    end = time.fromisoformat(EXECUTION_POLICY.eod_observation_end)
    return start, end


@dataclass
class CloseMarks:
    option: Optional[Dict[str, Any]] = None
    futures: Optional[Dict[str, Any]] = None
    #: The underlying's close. Optional: the economic marks are the option and the
    #: hedge, but the hedge is SIZED off this price, so it is used when observed and
    #: the hedge is simply held when it is not.
    underlying: Optional[Dict[str, Any]] = None
    gap_codes: List[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.option is not None and self.futures is not None


@dataclass
class EodObservationResult:
    observed: int = 0
    rejected: int = 0
    errors: List[str] = field(default_factory=list)


@dataclass
class EodFinalizationResult:
    status: str
    positions_finalized: int = 0
    gap_codes: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


def _provider_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_IST)


def select_close_marks(
    warehouse, *, opportunity_id: str, session_date: date,
) -> CloseMarks:
    """Latest ACCEPTED observation per leg whose PROVIDER time is inside the window.

    Provider time, not process wall time: a tick received at 15:30:04 but stamped
    15:29:58 is a valid close observation, and one stamped 15:30:04 is not.
    """
    start, end = _window()
    window_start = datetime.combine(session_date, start).replace(tzinfo=_IST)
    window_end = datetime.combine(session_date, end).replace(tzinfo=_IST)

    marks = CloseMarks()
    try:
        rows = warehouse.get_records_by_table(
            "quote_quality_events", opportunity_id=opportunity_id,
        )
    except Exception as exc:
        marks.gap_codes.append(f"eod_evidence_unreadable:{exc}")
        return marks

    best: Dict[str, Dict[str, Any]] = {}
    for raw in rows:
        row = dict(raw)
        if str(row.get("phase") or "") != EOD_PHASE:
            continue
        if int(row.get("accepted") or 0) != 1:
            continue
        stamped = _provider_dt(row.get("provider_timestamp"))
        if stamped is None or not (window_start <= stamped <= window_end):
            continue
        leg = str(row.get("leg") or "").upper()
        current = best.get(leg)
        if current is None or stamped > _provider_dt(current.get("provider_timestamp")):
            best[leg] = row

    marks.option = best.get("OPTION")
    marks.futures = best.get("FUTURES")
    marks.underlying = best.get("UNDERLYING")

    if marks.option is None:
        marks.gap_codes.append(f"missing_option_close_mark:{opportunity_id}")
    if marks.futures is None:
        marks.gap_codes.append(f"missing_futures_close_mark:{opportunity_id}")

    return marks


async def collect_eod_observations(
    *, client, cfg, warehouse, session_date: date, now_ist: datetime,
) -> EodObservationResult:
    """Fetch and persist close quotes for open positions. Decides nothing.

    One batched quote request covers every leg of every open position. A request
    per leg is an N+1 against a rate-limited endpoint, and -- worse -- it stretches
    the pass over seconds while the freshness verdict is judged against a clock read
    at the top of it, so the later quotes are refused as "future" timestamps and the
    session loses its close mark. The clock is therefore advanced from a monotonic
    timer and read again when each response ARRIVES.

    Quote keys come from the identity stored at signal time: a SENSEX position
    trades on BFO, and an ``NFO:`` key for it is an absent market, not a price.
    """
    from app.services.snapback import extract_raw_quote_event
    from app.services.snapback_market_data import (
        evaluate_quote_quality, evaluate_underlying_context_quote,
    )
    from app.services.snapback_quote_evidence import record_quote_attempt
    from app.services.snapback_venue import VenueUnknown, resolve_position_venue

    result = EodObservationResult()

    try:
        positions = await run_blocking(warehouse.get_active_paper_positions) or []
    except Exception as exc:
        result.errors.append(f"positions_unreadable:{exc}")
        return result

    base_ms = int(now_ist.timestamp() * 1000)
    started = _time.monotonic()

    def _now_ms() -> int:
        return base_ms + int((_time.monotonic() - started) * 1000)

    def _record(pos, leg, contract, event, decision, accepted, *, price=None) -> None:
        record_quote_attempt(
            warehouse, opportunity_id=str(pos["opportunity_id"]), phase=EOD_PHASE,
            leg=leg, contract_id=contract, required_for_economics=True,
            quote_present=bool(event),
            bid=(getattr(event, "best_bid", None) if leg != "UNDERLYING" else None),
            ask=(getattr(event, "best_ask", None) if leg != "UNDERLYING" else None),
            provider_timestamp=(
                datetime.fromtimestamp(
                    event.exchange_timestamp_ms / 1000.0, tz=timezone.utc
                ).astimezone(_IST).isoformat() if event else None
            ),
            age_ms=int(getattr(decision, "quote_age_ms", 0) or 0),
            accepted=accepted,
            # "missing_quote" is for a quote that never arrived. An accepted
            # observation has no reason codes, and must not be stored as missing.
            reason_codes=(["missing_quote"] if decision is None
                          else list(getattr(decision, "reason_codes", []) or [])),
            symbol=str(pos.get("symbol") or ""),
            spread_pct=getattr(decision, "spread_pct", None),
            execution_price=price,
        )

    planned = []  # (position, leg, contract, key)
    venue_cache: Dict[str, Any] = {}
    for raw in positions:
        pos = dict(raw)
        if str(pos.get("status") or "") == "EXIT_PENDING":
            # Its lifecycle was resolved intraday; it needs no close mark.
            continue
        opp = str(pos["opportunity_id"])
        option_symbol = str(pos.get("option_symbol") or "")
        futures_symbol = str(pos.get("futures_symbol") or "")
        try:
            venue = await run_blocking(resolve_position_venue, warehouse, pos)
            legs = []
            if option_symbol:
                legs.append(("OPTION", option_symbol, venue.derivative_key(option_symbol)))
            if futures_symbol:
                legs.append(("FUTURES", futures_symbol, venue.derivative_key(futures_symbol)))
            legs.append(("UNDERLYING", venue.cash_tradingsymbol, venue.spot_key()))
        except VenueUnknown as exc:
            # No venue means no key; say so on the evidence rather than guess NFO.
            result.errors.append(f"{opp}:venue:{exc}")
            for leg, contract in (("OPTION", option_symbol), ("FUTURES", futures_symbol)):
                if contract:
                    await run_blocking(_record, pos, leg, contract, None, None, False)
                    result.rejected += 1
            continue
        for leg, contract, key in legs:
            planned.append((pos, leg, contract, key))

    fetched: Dict[str, Any] = {}
    failed: Dict[str, str] = {}
    arrival_ms: Dict[str, int] = {}
    keys = list(dict.fromkeys(key for _, _, _, key in planned))
    for i in range(0, len(keys), _QUOTE_CHUNK):
        chunk = keys[i:i + _QUOTE_CHUNK]
        try:
            quotes = await client.get_quote(chunk)
            arrived = _now_ms()
            for key in chunk:
                fetched[key] = (quotes or {}).get(key) or {}
                arrival_ms[key] = arrived
        except Exception as exc:
            for key in chunk:
                failed[key] = str(exc)

    for pos, leg, contract, key in planned:
        opp = str(pos["opportunity_id"])
        if key in failed:
            result.errors.append(f"{opp}:{leg}:{failed[key]}")
            await run_blocking(_record, pos, leg, contract, None, None, False)
            result.rejected += 1
            continue

        event = extract_raw_quote_event(contract, fetched.get(key) or {})
        now_ms = arrival_ms.get(key, _now_ms())
        if event is None:
            decision = None
        elif leg == "UNDERLYING":
            decision = evaluate_underlying_context_quote(event, now_ms=now_ms)
        else:
            decision = evaluate_quote_quality(event, cfg, now_ms=now_ms)
        accepted = bool(decision and decision.accepted_for_execution)

        await run_blocking(_record,
            pos, leg, contract, event, decision, accepted,
            price=(float(event.last_price) if (accepted and leg == "UNDERLYING") else None),
        )
        if accepted:
            result.observed += 1
        else:
            result.rejected += 1

    return result


def finalize_eod_session(
    *, cfg, warehouse, session_date: date, finalized_at: datetime,
) -> EodFinalizationResult:
    """Apply the end-of-day lifecycle from persisted close evidence only."""
    from app.services.snapback_session_ledger import (
        SessionStatus, append_session_evidence_gap, update_session_phase,
    )

    session_key = session_date.isoformat()
    result = EodFinalizationResult(status="COMPLETE")

    try:
        positions = warehouse.get_active_paper_positions() or []
    except Exception as exc:
        result.status = "FAILED"
        result.errors.append(f"positions_unreadable:{exc}")
        append_session_evidence_gap(warehouse, session_key, "eod_finalization_exception")
        update_session_phase(warehouse, session_key, eod_phase_status=SessionStatus.FAILED)
        return result

    for raw in positions:
        pos = dict(raw)
        opp = str(pos["opportunity_id"])

        if str(pos.get("status") or "") == "EXIT_PENDING":
            continue

        if warehouse.has_daily_mtm_for_session(opp, session_key):
            # Finalisation is idempotent: one mark per session, ever.
            continue

        marks = select_close_marks(
            warehouse, opportunity_id=opp, session_date=session_date,
        )
        if not marks.complete:
            result.status = "FAILED"
            result.gap_codes.extend(marks.gap_codes)
            for code in marks.gap_codes:
                append_session_evidence_gap(warehouse, session_key, code)
            continue

        try:
            apply_daily_mtm_from_observed_marks(
                opportunity_id=opp, position=pos, option_mark=marks.option,
                futures_mark=marks.futures, cfg=cfg, warehouse=warehouse,
                session_date=session_key, underlying_mark=marks.underlying,
            )
            result.positions_finalized += 1
        except Exception as exc:
            log.exception("EOD finalisation failed for %s: %s", opp, exc)
            result.status = "FAILED"
            result.errors.append(f"{opp}:{exc}")
            append_session_evidence_gap(warehouse, session_key, "eod_finalization_exception")

    if result.status == "FAILED" and not result.gap_codes:
        result.gap_codes.append(EOD_EVIDENCE_GAP)

    update_session_phase(
        warehouse, session_key,
        eod_phase_status=SessionStatus.COMPLETE if result.status == "COMPLETE" else SessionStatus.FAILED,
    )
    return result


def _remaining_calendar_days(position: Dict[str, Any], session_date: str) -> int:
    """Calendar days from this session to the contract's own expiry.

    The expiry is stored on the position, so it is read from there. Counting down
    from ``entry_dte`` instead drifts by every weekend and holiday the position
    has sat through, and the expiry buffer is a calendar rule.
    """
    session = date.fromisoformat(str(session_date)[:10])
    try:
        return (date.fromisoformat(str(position.get("option_expiry") or "")[:10]) - session).days
    except ValueError:
        pass
    entry_dte = int(position.get("entry_dte") or 45)
    try:
        entered = datetime.fromisoformat(
            str(position.get("entry_timestamp") or "").replace("Z", "+00:00")
        ).astimezone(_IST).date()
        return entry_dte - max(0, (session - entered).days)
    except ValueError:
        return entry_dte


def apply_daily_mtm_from_observed_marks(
    *,
    opportunity_id: str,
    position: Dict[str, Any],
    option_mark: Dict[str, Any],
    futures_mark: Dict[str, Any],
    cfg,
    warehouse,
    session_date: str,
    underlying_mark: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """The end-of-day state transition, driven by already-observed marks.

    One implementation, shared by the live finalizer, replay and fixtures, so the
    lifecycle cannot drift between them. It records the mark, rebalances the hedge,
    evaluates the frozen exit rules AND EXECUTES THE VERDICT: a position that reached
    its horizon, its expiry buffer or an end-of-day stop is closed here, at the
    observed close bids. The hedge is sized off the OBSERVED underlying close and the
    contract's remaining life; with no observed close it is held, never resized off the
    entry spot.
    """
    from app.engines.snapback.intraday_models import RawQuoteEvent
    from app.engines.snapback.policy import runner_should_exit_for_expiry
    from app.engines.snapback.pricing import bs_delta
    from app.services.snapback_prospective_collector import SnapbackProspectiveCollector

    collector = SnapbackProspectiveCollector(warehouse=warehouse)

    option_bid = float(option_mark.get("bid") or 0.0)
    futures_bid = float(futures_mark.get("bid") or 0.0)
    futures_ask = float(futures_mark.get("ask") or futures_bid)
    option_stamp = str(option_mark.get("provider_timestamp") or "")

    fut_event = RawQuoteEvent(
        contract_id=str(position.get("futures_symbol") or ""),
        exchange_timestamp_ms=int(
            datetime.fromisoformat(
                str(futures_mark.get("provider_timestamp"))
            ).timestamp() * 1000
        ),
        received_at_ms=int(datetime.now(_IST).timestamp() * 1000),
        best_bid=futures_bid, best_ask=futures_ask,
        bid_quantity=int(futures_mark.get("visible_quantity") or 1),
        ask_quantity=int(futures_mark.get("visible_quantity") or 1),
        last_price=futures_bid,
    )

    opt_sym = str(position.get("option_symbol") or "")
    opt_type = "PE" if "PE" in opt_sym else "CE"
    strike = float(position.get("option_strike") or 0.0)
    entry_spot = float(position.get("entry_spot") or 0.0)
    rem_dte = _remaining_calendar_days(position, session_date)

    observed_spot = 0.0
    if underlying_mark is not None:
        try:
            observed_spot = float(underlying_mark.get("execution_price") or 0.0)
        except (TypeError, ValueError):
            observed_spot = 0.0
    if not math.isfinite(observed_spot):
        observed_spot = 0.0
    try:
        iv = float(position.get("entry_iv"))
    except (TypeError, ValueError):
        iv = 0.0
    if not math.isfinite(iv):
        iv = 0.0

    # The hedge is only resized when both what sizes it was observed: today's
    # underlying close, and the vol the position was entered at. Otherwise it is held.
    can_size = observed_spot > 0 and iv > 0
    spot = observed_spot if observed_spot > 0 else entry_spot
    if can_size:
        delta_raw = float(bs_delta(spot, strike, max(1, rem_dte) / 365.0, iv,
                                   call=(opt_type == "CE")))
        option_delta = -abs(delta_raw) if opt_type == "PE" else abs(delta_raw)
    else:
        option_delta = 0.0
        log.warning("EOD hedge held for %s: %s", opportunity_id,
                    "no observed underlying close" if observed_spot <= 0
                    else "entry vol unknown")

    mtm = collector.rebalance_and_mtm(
        opportunity_id=opportunity_id,
        session_date=session_date,
        symbol=str(position.get("symbol") or ""),
        current_spot=spot,
        current_option_delta=option_delta,
        option_bid=option_bid,
        futures_quote_event=fut_event,
        option_entry_price=float(position.get("option_entry_price") or 0.0),
        option_quantity=int(position.get("option_qty") or 0),
        current_futures_lots=int(position.get("current_futures_lots") or 0),
        futures_lot_size=int(position.get("futures_lot_size") or 0),
        causal_beta=float(position.get("causal_beta") or 1.0),
        prior_realized_futures_pnl=float(position.get("realized_futures_pnl") or 0.0),
        prior_avg_futures_entry_price=float(position.get("avg_futures_entry_price") or 0.0),
        option_quote_event=None,
        cfg=None,
        peak_source="EOD_CLOSE",
        hold_hedge=not can_size,
        exit_rules=cfg,
    )

    exit_reason = mtm.get("exit_reason")
    if not exit_reason and runner_should_exit_for_expiry(remaining_calendar_days=rem_dte):
        # The canonical buffer, shared with the backtest rather than re-stated here.
        exit_reason = "EXPIRY_APPROACHING"
    mtm["exit_reason"] = exit_reason
    mtm["closed"] = False
    if not exit_reason:
        return mtm

    # LATCH before closing. The MTM above is the session's idempotency key, so a
    # failure in the close would otherwise leave an exit that was decided, never
    # executed and never retried. EXIT_PENDING hands it to the intraday risk
    # monitor, which completes a latched exit on its next cycle.
    warehouse.set_paper_position_pending_exit(
        opportunity_id=opportunity_id, pending_exit_reason=exit_reason,
        pending_exit_option_bid=option_bid,
        pending_exit_ts=option_stamp or datetime.now(timezone.utc).isoformat(),
    )

    fresh = warehouse.get_paper_position(opportunity_id) or position
    open_lots = int(fresh.get("current_futures_lots") or 0)
    lot_size = int(fresh.get("futures_lot_size") or position.get("futures_lot_size") or 65)
    if iv <= 0:
        # Counterfactual diagnostics are priced at the entry vol; never a substitute.
        log.critical("EOD exit for %s (%s) latched but not booked: entry vol unknown",
                     opportunity_id, exit_reason)
        return mtm
    collector.close_opportunity(
        opportunity_id=opportunity_id,
        symbol=str(position.get("symbol") or ""),
        exit_reason=exit_reason,
        entry_ts=str(position.get("entry_timestamp") or ""),
        exit_ts=option_stamp or datetime.now(timezone.utc).isoformat(),
        entry_spot=entry_spot or spot,
        exit_spot=spot,
        selected_strike=strike,
        entry_dte=int(position.get("entry_dte") or 45),
        exit_dte=max(0, rem_dte),
        iv_proxy=iv,
        option_entry_price=float(position.get("option_entry_price") or 0.0),
        option_exit_bid=option_bid,
        futures_entry_price=float(fresh.get("avg_futures_entry_price") or futures_bid),
        futures_exit_bid=futures_bid,
        option_quantity=int(position.get("option_qty") or 0),
        futures_quantity=open_lots * lot_size,
        nifty_futures_entry=float(fresh.get("avg_futures_entry_price") or futures_bid),
        nifty_futures_exit=futures_bid,
        option_type=opt_type,
        accumulated_costs=float(mtm.get("accumulated_costs") or fresh.get("accumulated_costs") or 0.0),
    )
    mtm["closed"] = True
    return mtm
