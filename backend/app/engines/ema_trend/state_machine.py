"""Fill-driven campaign reducer shared by runtime and historical replay.

No broker calls, wall-clock reads, or persistence happen in this module.
Callers atomically persist the mutated state and returned intent before sending.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_FLOOR
from .config import EmaTrendConfig, Profile, stable_id
from .contracts import Action, Campaign, Candidate, Feature, Lot, Vacancy
from .features import IST, continuous
from .setups import context_ok
from .sizing import used_campaign_risk

D = lambda v: Decimal(str(v))
TERMINAL = {"STOP", "SESSION_EXIT", "TIME_EXIT", "STRUCTURAL_EXIT", "FLATTEN", "ENTRY_TOO_SMALL", "RISK_EXIT"}


def make_action(c: Campaign, kind: str, side: str, quantity: int, index: int,
                at_ms: int, price: Decimal, components: dict[str, int] | None = None) -> Action:
    if quantity <= 0 or quantity%c.lot_size or price <= 0:
        raise ValueError("nonpositive action")
    action = Action(id=stable_id(c.id, kind, at_ms, c.revision), campaign_id=c.id,
                    kind=kind, side=side, quantity=quantity, index=index, at_ms=at_ms,
                    limit=price, components=components or {kind: quantity})
    if kind in TERMINAL and c.pending and c.pending.side == "BUY":
        if c.pending_close:
            raise ValueError("EMERGENCY_CLOSE_ALREADY_PENDING")
        c.pending_close = action
    else:
        c.pending = action
    c.state = "CLOSING" if kind in TERMINAL else "ACTION_PENDING"
    c.reason = kind
    return action


def finalize_entry(c: Campaign) -> None:
    if c.entry_terminal:
        return
    c.q0 = c.entry_filled_qty or c.qty
    c.p0 = c.entry_notional/c.entry_filled_qty if c.entry_filled_qty else c.vwap
    c.r_price = c.p0-c.initial_stop
    c.entry_terminal = True
    c.allowance10 = c.allowance21 = c.quarter
    c.below10 = c.below21 = 0
    if c.qty == 0 and not c.pending_close:
        c.state, c.reason = "CLOSED", "ENTRY_UNFILLED"
    elif c.q0 < 4*c.lot_size or c.r_price <= 0:
        c.state, c.reason = "CLOSING", "ENTRY_TOO_SMALL" if c.q0 < 4*c.lot_size else "RISK_EXIT"
    elif c.state != "CLOSING":
        c.state, c.reason = "OPEN", "ENTRY_FILLED"


def apply_fill(c: Campaign, action: Action, fill_id: str, qty: int,
               price, fee=0) -> bool:
    """Apply incremental, deduplicated canonical fill facts; ACK changes nothing."""
    price, fee = D(price), D(fee)
    if fill_id in c.seen_fill_ids:
        return False
    if qty <= 0 or qty%c.lot_size or not price.is_finite() or not fee.is_finite() or price <= 0 or fee < 0:
        raise ValueError("INVALID_FILL")
    if action.campaign_id != c.id or action.filled_quantity+qty > action.quantity:
        raise ValueError("FILL_OWNERSHIP_OR_QUANTITY_MISMATCH")
    if action.side == "SELL" and qty > c.qty:
        raise ValueError("SELL_EXCEEDS_OWNED_INVENTORY")
    if action.side == "BUY" and c.entry_terminal and c.qty+qty > c.q0:
        raise ValueError("ADD_EXCEEDS_INITIAL_CAPACITY")
    if action.side == "BUY":
        if action.kind == "ENTRY":
            c.entry_filled_qty += qty
            c.entry_notional += qty*price
        else:
            matching = {"RECLAIM_10": "WEAK_10", "RECLAIM_21": "WEAK_21"}.get(action.kind)
            eligible = [v for v in c.vacancies if v.qty and (matching is None or v.component == matching)
                        and (matching is None or action.source_episode is None or v.index == action.source_episode)]
            if sum(v.qty for v in eligible) < qty:
                raise ValueError("ADD_EXCEEDS_MATCHING_VACANCY")
            remaining = qty
            for vacancy in eligible:
                take = min(remaining, vacancy.qty)
                vacancy.qty -= take
                if vacancy.component == "WEAK_10":
                    c.allowance10 = min(c.quarter, c.allowance10+take)
                elif vacancy.component == "WEAK_21":
                    c.allowance21 = min(c.quarter, c.allowance21+take)
                remaining -= take
                if not remaining:
                    break
            c.last_add_price = price
        c.lots.append(Lot(fill_id=fill_id, qty=qty, price=price, fee_per_share=fee/qty))
    else:
        remaining = qty
        for lot in list(c.lots):
            take = min(lot.qty, remaining)
            net = take*(price-lot.price-lot.fee_per_share)-fee*D(take)/D(qty)
            c.realized_net += net
            c.realized_loss_debit += max(D(0), -net)
            lot.qty -= take
            remaining -= take
            if not lot.qty:
                c.lots.remove(lot)
            if not remaining:
                break
        # Component offsets are based on already filled action quantity.
        offset, remaining = action.filled_quantity, qty
        for component, amount in action.components.items():
            skip = min(offset, amount)
            offset -= skip
            take = min(amount-skip, remaining)
            if take:
                if component in {"WEAK_10", "WEAK_21", "TAKE_4R"}:
                    c.vacancies.append(Vacancy(component=component, qty=take, index=action.index))
                    if component == "WEAK_10":
                        c.allowance10 = max(0, c.allowance10-take)
                    elif component == "WEAK_21":
                        c.allowance21 = max(0, c.allowance21-take)
                    elif component == "TAKE_4R":
                        c.took_4r = True
                remaining -= take
            if not remaining:
                break
        c.last_reduction_index = action.index
    c.total_fees += fee
    c.seen_fill_ids.append(fill_id)
    action.filled_quantity += qty
    action.status = "FILLED" if action.filled_quantity == action.quantity else "PARTIAL"
    if c.pending and c.pending.id == action.id:
        c.pending = action
    if c.pending_close and c.pending_close.id == action.id:
        c.pending_close = action
    c.revision += 1
    if action.status == "FILLED":
        if c.pending and c.pending.id == action.id:
            c.pending = None
        if c.pending_close and c.pending_close.id == action.id:
            c.pending_close = None
        if not c.qty and action.side == "SELL":
            c.state, c.reason = ("CLOSING" if c.pending and c.pending.side == "BUY" else "CLOSED"), action.kind
            c.candidates.clear()
        elif c.state != "CLOSING":
            c.state = "OPEN" if c.entry_terminal else "ENTRY_PENDING"
    return True


def propose_stop(c: Campaign, f: Feature, tick: Decimal | None = None) -> Decimal | None:
    """Proposal only: caller must durably acknowledge monitor/broker coverage."""
    tick = tick or c.tick_size
    if tick <= 0 or not f.atr10 or not c.qty:
        return None
    lows = [p for p in f.pivots if p.side == "LOW" and p.occurred_index > c.entry_index
            and p.previous_price is not None and p.price > p.previous_price]
    if not lows:
        return None
    candidate = ((D(lows[-1].price)-max(tick, D(f.atr10)*D('.10')))/tick).to_integral_value(rounding=ROUND_FLOOR)*tick
    return candidate if candidate > c.stop and candidate < D(f.bar.close) else None


def decide(c: Campaign, f: Feature, previous: Feature | None, profile: Profile,
           config: EmaTrendConfig, allow_add: bool = True) -> Action | None:
    if c.state in {"CLOSED", "RECOVERY_REQUIRED"} or not c.qty:
        return None
    # Duplicate scans, ticks and restarted workers never replay a bar event.
    if f.bar.close_ms <= c.last_close_ms:
        return None
    c.last_index, c.last_close_ms, c.last_mark = f.index, f.bar.close_ms, D(f.bar.close)
    price = D(f.bar.close)
    if f.bar.low <= float(c.stop):
        # OHLC path is unknown; stop-first and adverse opening gap are explicit.
        price = min(c.stop, D(f.bar.open))
        if c.pending_close or (c.pending and c.pending.side == "SELL"):
            c.state, c.reason = "CLOSING", "STOP_PENDING_ORDER_RECONCILIATION"
            return None
        return make_action(c, "STOP", "SELL", c.qty, f.index, f.bar.available_ms, price)
    local = datetime.fromtimestamp(f.bar.close_ms/1000, IST)
    session_due = profile.force_close and local.strftime("%H:%M") >= profile.force_close
    from .config import gap_close_due
    session_due = session_due or gap_close_due(profile, local)
    # Any accidental next-day intraday holding closes at the first observation.
    if previous and profile.mode not in ("swing", "overnight"):
        session_due = session_due or local.date() != datetime.fromtimestamp(previous.bar.close_ms/1000, IST).date()
    time_due = profile.max_hold_bars is not None and f.index-c.entry_index >= profile.max_hold_bars
    if c.pending and c.pending.side == "BUY" and (session_due or time_due or c.state == "CLOSING"):
        if c.pending_close:
            return None
        kind = "SESSION_EXIT" if session_due else "TIME_EXIT" if time_due else "RISK_EXIT"
        return make_action(c, kind, "SELL", c.qty, f.index, f.bar.available_ms, price)
    if c.pending or c.pending_close:
        if session_due or time_due:
            c.state, c.reason = "CLOSING", "SESSION_EXIT_PENDING_RECONCILIATION"
        return None
    if session_due or time_due or c.state == "CLOSING":
        kind = "SESSION_EXIT" if session_due else "TIME_EXIT" if time_due else c.reason if c.reason in TERMINAL else "RISK_EXIT"
        return make_action(c, kind, "SELL", c.qty, f.index, f.bar.available_ms, price)
    if not c.entry_terminal or f.ema10 is None or f.ema21 is None:
        return None
    if previous and previous.ema10 is not None and previous.ema21 is not None:
        if previous.ema10 <= previous.ema21 and f.ema10 > f.ema21:
            c.bullish_epoch, c.bearish_epoch = f.index, -1
            c.candidates = [x for x in c.candidates if x.kind not in {"HIGHER_LOW", "PIVOT_BREAKOUT"}]
        if previous.ema10 >= previous.ema21 and f.ema10 < f.ema21:
            c.bearish_epoch = f.index
            c.candidates = [x for x in c.candidates if x.kind not in {"HIGHER_LOW", "PIVOT_BREAKOUT"}]
    # Prior pivot comparisons supplied by caller are captured in config-free
    # campaign evidence, not inferred from a future chart annotation.
    prior_high = f.prior_high or c.pivot_high
    prior_low = f.prior_low or c.pivot_low
    for pivot in f.pivots:
        if pivot.side == "HIGH":
            reference = pivot.previous_price if pivot.previous_price is not None else (
                prior_high.price if prior_high and prior_high.confirmed_index <= pivot.occurred_index else None)
            if (c.bearish_epoch >= 0 and pivot.occurred_index > c.bearish_epoch and reference is not None
                    and pivot.price < reference):
                return make_action(c, "STRUCTURAL_EXIT", "SELL", c.qty, f.index, f.bar.available_ms, price)
    if previous and not continuous(previous.bar, f.bar, profile.signal_minutes, c.symbol.split(":")[0],
                                   across_sessions=profile.mode == "overnight"):
        c.below10 = c.below21 = 0
    c.below10 = c.below10+1 if f.bar.close < f.ema10 else 0
    c.below21 = c.below21+1 if f.bar.close < f.ema21 else 0
    components = {}
    if c.below21 == 2 and c.allowance21:
        components["WEAK_21"] = min(c.quarter, c.allowance21)
    if c.below10 == 2 and c.allowance10:
        components["WEAK_10"] = min(c.quarter, c.allowance10)
    if not c.took_4r and price >= c.p0+4*c.r_price:
        components["TAKE_4R"] = c.quarter
    if components:
        available = c.qty
        clipped = {}
        for kind, amount in components.items():
            take = min(amount, available)
            if take:
                clipped[kind] = take
                available -= take
        c.candidates.clear()
        return make_action(c, "REDUCE", "SELL", sum(clipped.values()), f.index,
                           f.bar.available_ms, price, clipped)
    new_candidates = []
    bullish_price = f.bar.close > f.bar.open
    if previous and f.index > c.last_reduction_index >= 0:
        if previous.ema10 is not None and previous.bar.close <= previous.ema10 and f.bar.close > f.ema10:
            new_candidates.append(("RECLAIM_10", D(f.ema10)))
        if previous.ema21 is not None and previous.bar.close <= previous.ema21 and f.bar.close > f.ema21:
            new_candidates.append(("RECLAIM_21", D(f.ema21)))
        for p in f.pivots:
            reference = p.previous_price if p.previous_price is not None else (prior_low.price if prior_low and prior_low.confirmed_index <= p.occurred_index else None)
            if p.side == "LOW" and reference is not None and p.occurred_index > max(c.bullish_epoch, c.last_reduction_index) and c.bullish_epoch >= 0 and p.price > reference and f.bar.close > max(f.ema10, f.ema21):
                new_candidates.append(("HIGHER_LOW", D(p.price)))
        if prior_high and f.ema10 > f.ema21 and previous.bar.close <= prior_high.price and f.bar.close > prior_high.price+float(c.tick_size):
            new_candidates.append(("PIVOT_BREAKOUT", D(prior_high.price)))
    for p in f.pivots:
        if p.side == "HIGH":
            c.pivot_high = p
        else:
            c.pivot_low = p
    c.candidates = [x for x in c.candidates if f.index < x.expires_index]
    if bullish_price:
        for kind, barrier in new_candidates:
            epoch = c.bullish_epoch
            matching = {"RECLAIM_10": "WEAK_10", "RECLAIM_21": "WEAK_21"}.get(kind)
            if matching:
                episodes = sorted({v.index for v in c.vacancies if v.qty and v.component == matching
                                   and v.index < f.index and f"{kind}:{v.index}" not in c.used_catalysts})
                if not episodes:
                    continue
                epoch = episodes[0]
            catalyst = f"{kind}:{epoch}"
            if catalyst not in c.used_catalysts and not any(x.kind == kind for x in c.candidates):
                c.candidates.append(Candidate(kind=kind, index=f.index, expires_index=f.index+3,
                                              barrier=barrier, epoch=epoch))
    if not allow_add or not f.ready or not bullish_price or not context_ok(f, profile):
        return None
    if not profile.entry_start <= local.strftime("%H:%M") <= profile.entry_end and profile.mode != "swing":
        return None
    for kind in ("RECLAIM_10", "RECLAIM_21", "HIGHER_LOW", "PIVOT_BREAKOUT"):
        candidate = next((x for x in c.candidates if x.kind == kind), None)
        if not candidate:
            continue
        if kind in {"HIGHER_LOW", "PIVOT_BREAKOUT"} and (candidate.epoch != c.bullish_epoch or c.bearish_epoch > c.bullish_epoch):
            c.candidates.remove(candidate)
            c.reason = "STALE_CROSSOVER_EPOCH"
            continue
        if kind == "HIGHER_LOW" and (f.bar.low <= float(candidate.barrier) or f.bar.close <= max(f.ema10, f.ema21)):
            c.candidates.remove(candidate)
            c.reason = "HIGHER_LOW_STRUCTURE_INVALIDATED"
            continue
        if kind == "PIVOT_BREAKOUT" and f.ema10 <= f.ema21:
            c.candidates.remove(candidate)
            c.reason = "BREAKOUT_ALIGNMENT_INVALIDATED"
            continue
        level = D(f.ema10) if kind == "RECLAIM_10" else D(f.ema21) if kind == "RECLAIM_21" else candidate.barrier
        if kind == "PIVOT_BREAKOUT":
            level += c.tick_size
        if price <= level or price < max(c.p0, c.vwap, c.last_add_price):
            c.reason = "NO_AVERAGING_DOWN_OR_INVALID_CANDIDATE"
            continue
        matching = {"RECLAIM_10": "WEAK_10", "RECLAIM_21": "WEAK_21"}.get(kind)
        vacancy = sum(v.qty for v in c.vacancies if matching is None or
                      (v.component == matching and v.index == candidate.epoch))
        quota = c.q0-3*c.quarter if kind == "PIVOT_BREAKOUT" else c.quarter
        remaining_risk = max(D(0), c.budget-used_campaign_risk(c, config)-config.brokerage_per_order)
        per_unit_risk = max(D('.01'), price-c.stop+c.gap_reserve_per_share
                            +price*(config.fee_bps+config.slippage_bps)/D(10000)*2)
        count = min(quota, vacancy, c.q0-c.qty, int(remaining_risk/per_unit_risk))
        count=count//c.lot_size*c.lot_size
        if count < c.lot_size:
            c.reason = "CAPACITY_FULL_OR_RISK_EXHAUSTED"
            continue
        c.candidates.remove(candidate)
        catalyst = f"{kind}:{candidate.epoch}"
        c.used_catalysts.append(catalyst)
        action = make_action(c, kind, "BUY", count, f.index, f.bar.available_ms, price)
        action.source_episode = candidate.epoch if matching else None
        return action
    return None
