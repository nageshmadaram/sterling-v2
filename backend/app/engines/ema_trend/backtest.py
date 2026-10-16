"""Event replay over the runtime reducer. All OHLC fills are explicitly modelled."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Sequence

from .config import EmaTrendConfig, PROFILES, canonical_mode, stable_id, descriptor
from .contracts import Bar, Campaign
from .features import IST, build_features
from .setups import evaluate_setups
from .sizing import size_plan, used_campaign_risk
from .state_machine import apply_fill, decide, finalize_entry, make_action

D = lambda value: Decimal(str(value))


def run_replay(symbol: str, mode: str, bars: Sequence[Bar], context_bars: Sequence[Bar],
               config: EmaTrendConfig, *, uid: str = "research", run_id: str | None = None,
               activation_ms: int | None = None) -> dict:
    mode = canonical_mode(mode)
    profile = PROFILES[mode]
    if not uid or ":" not in symbol or symbol not in config.symbols:
        raise ValueError("replay requires an explicit user and configured equity symbol")
    if len(bars) < 250:
        raise ValueError("WARMING_UP: at least 250 signal bars required")
    features = build_features(bars, context_bars)
    setups = evaluate_setups(symbol, mode, features)
    run_id = run_id or stable_id("replay", uid, symbol, mode, config.config_hash,
                                  [b.model_dump() for b in bars], [b.model_dump() for b in context_bars])
    cash = config.allocation_inr
    peak, day_start = cash, cash
    current_day = None
    halted = False
    max_drawdown = D(0)
    campaigns: list[Campaign] = []
    fills: list[dict] = []
    actions: list[dict] = []
    decisions: list[dict] = []
    equity_curve: list[dict] = []
    active = None
    cooldown_until = -1
    last_close_index = -1

    def fill(c, action, price, stamp):
        nonlocal cash
        qty = action.quantity-action.filled_quantity
        fee = D(qty)*price*config.fee_bps/D(10000)+config.brokerage_per_order
        fid = stable_id(run_id, action.id, "modelled_fill")
        apply_fill(c, action, fid, qty, price, fee)
        cash += (D(qty)*price-fee) if action.side == "SELL" else -(D(qty)*price+fee)
        fills.append({"id": fid, "action_id": action.id, "campaign_id": c.id,
                      "side": action.side, "quantity": qty, "price": str(price),
                      "fee": str(fee), "at_ms": stamp, "evidence_class": "MODELLED_OHLC",
                      "kind": action.kind})
        actions.append(action.model_dump(mode="json"))
        if action.kind == "ENTRY":
            finalize_entry(c)

    for i, f in enumerate(features):
        b = f.bar
        local = datetime.fromtimestamp(b.close_ms/1000, IST)
        day = local.date()
        if day != current_day:
            day_start = cash+(active.qty*D(b.open) if active else D(0))
            current_day = day
            # Drawdown halt is persistent for a run; daily halt resets next session.
            halted = peak > 0 and (peak-day_start)/peak >= config.drawdown_fraction
        if active and active.pending and b.open_ms >= active.pending.at_ms:
            action = active.pending
            price = D(b.open)*(1+config.slippage_bps/D(10000) if action.side == "BUY" else 1-config.slippage_bps/D(10000))
            if action.side == "BUY" and price > action.limit:
                action.status = "CANCELLED"
                actions.append(action.model_dump(mode="json"))
                active.pending = None
                if action.kind == "ENTRY":
                    finalize_entry(active)
                else:
                    active.state = "OPEN"
                decisions.append({"at_ms": b.available_ms, "reason": "LIMIT_NOT_FILLED", "action_id": action.id})
            elif action.side == "BUY" and price*action.quantity > cash:
                action.status = "REJECTED"
                active.pending = None
                active.state = "CLOSED" if not active.qty else "OPEN"
                decisions.append({"at_ms": b.available_ms, "reason": "CASH_EXHAUSTED", "action_id": action.id})
            else:
                fill(active, action, price, b.open_ms)
        if active and not active.qty and active.state == "CLOSED":
            cooldown_until = i+profile.cooldown_bars
            last_close_index = i
            active = None
        if active and active.qty:
            active.last_mark = D(b.close)
            mark = cash+active.qty*D(b.close)
            breaker = (day_start > 0 and day_start-mark >= day_start*config.daily_loss_fraction) or (peak > 0 and peak-mark >= peak*config.drawdown_fraction)
            if breaker:
                halted = True
            # Resting stop exists before bar completion. Adverse gap/stop-first path;
            # this is an explicit model, never an actual broker fill assertion.
            if b.low <= float(active.stop) and not active.pending:
                action = make_action(active, "STOP", "SELL", active.qty, i, b.available_ms,
                                     min(active.stop, D(b.open)))
                fill(active, action, action.limit*(1-config.slippage_bps/D(10000)), b.available_ms)
            elif breaker and not active.pending:
                make_action(active, "RISK_EXIT", "SELL", active.qty, i, b.available_ms, D(b.close))
            elif not active.pending:
                action = decide(active, f, features[i-1] if i else None, profile, config, allow_add=not halted)
                if action and action.side == "BUY":
                    qty_by_cash = int(cash/(action.limit*(1+config.fee_bps/D(10000))))
                    if qty_by_cash <= 0:
                        active.pending = None
                        active.state = "OPEN"
                        decisions.append({"at_ms": b.available_ms, "reason": "CASH_EXHAUSTED"})
                    else:
                        action.quantity = min(action.quantity, qty_by_cash)
        if active and active.state == "CLOSED":
            cooldown_until, last_close_index = i+profile.cooldown_bars, i
            active = None
        setup = setups[i]
        if setup.status == "armed" and (activation_ms is None or b.available_ms >= activation_ms):
            reason = "COOLDOWN_OR_OLD_CROSS"
            session_ok = profile.entry_start <= local.strftime("%H:%M") <= profile.entry_end
            if not active and not halted and i > cooldown_until and (setup.cross_index or 0) > last_close_index:
                if mode != "swing" and not session_ok:
                    reason = "OUTSIDE_ENTRY_WINDOW"
                else:
                    plan = size_plan(config, setup.ceiling, setup.stop, f.atr10, cash)
                    reason = plan["reason"]
                    if plan["quantity"]:
                        active = Campaign(id=stable_id(run_id, setup.id, config.config_hash), uid=uid,
                                          account_id=f"replay:{run_id}", tenant_kind="REPLAY",
                                          symbol=symbol, mode=mode, product=profile.product,
                                          config_hash=config.config_hash, config_revision=0,
                                          config_snapshot=config.model_dump(mode="json"), budget=plan["budget"],
                                          gap_reserve_per_share=config.gap_reserve_atr*D(f.atr10),
                                          initial_stop=setup.stop, stop=setup.stop, entry_index=i+1,
                                          bullish_epoch=setup.cross_index or -1,
                                          pivot_high=f.prior_high, pivot_low=f.prior_low)
                        action = make_action(active, "ENTRY", "BUY", plan["quantity"], i,
                                             b.available_ms, setup.ceiling)
                        active.state = "ENTRY_PENDING"
                        campaigns.append(active)
            elif halted:
                reason = "RISK_BREAKER_HALTED"
            decisions.append({"signal_id": setup.id, "at_ms": b.available_ms, "reason": reason,
                              "index": i, "path": setup.path})
        equity = cash+(active.qty*D(b.close) if active else D(0))
        peak = max(peak, equity)
        drawdown = max(D(0), peak-equity)
        max_drawdown = max(max_drawdown, drawdown)
        equity_curve.append({"at_ms": b.available_ms, "equity": str(equity), "drawdown": str(drawdown)})
    completed = [c for c in campaigns if c.state == "CLOSED" and c.entry_terminal and c.q0 > 0]
    wins = [c.realized_net for c in completed if c.realized_net > 0]
    losses = [-c.realized_net for c in completed if c.realized_net < 0]
    net = sum((c.realized_net for c in completed), D(0))
    return {"id": run_id, "strategy_id": "ema_trend", "mode": mode, "symbol": symbol,
            "config_hash": config.config_hash, "rule_hash": descriptor()["rule_hash"],
            "dataset_hash": stable_id([b.model_dump() for b in bars], [b.model_dump() for b in context_bars]),
            "execution_vehicle": "EQUITY", "product": profile.product,
            "evidence_status": "INCONCLUSIVE", "live_eligible": False,
            "fill_model": "MODELLED_NEXT_OPEN_ADVERSE_STOP_FIRST_WITH_ESTIMATED_FEES",
            "limitations": ["OHLC_DOES_NOT_PROVE_SPREAD_DEPTH_QUEUE_OR_FILLABILITY",
                            "FEE_SCHEDULE_NOT_BROKER_VERIFIED", "NO_HOLDOUT_OR_FORWARD_PROMOTION_PROOF"],
            "campaigns": [c.model_dump(mode="json") | {"quantity": c.qty, "current_vwap": str(c.vwap)} for c in campaigns],
            "fills": fills, "actions": actions, "decisions": decisions, "equity_curve": equity_curve,
            "metrics": {"completed_campaigns": len(completed), "open_campaigns": sum(c.state != "CLOSED" for c in campaigns),
                        "net_realized": str(net), "estimated_fees": str(sum((c.total_fees for c in campaigns), D(0))),
                        "win_rate": len(wins)/len(completed) if completed else None,
                        "expectancy": str(net/len(completed)) if completed else None,
                        "profit_factor": str(sum(wins, D(0))/sum(losses, D(0))) if losses else None,
                        "max_drawdown": str(max_drawdown), "starting_capital": str(config.allocation_inr),
                        "ending_equity": equity_curve[-1]["equity"] if equity_curve else str(cash)}}
