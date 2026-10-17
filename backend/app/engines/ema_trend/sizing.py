"""Exact decimal cash/risk sizing; no minimum-quantity rounding up."""
from decimal import Decimal, ROUND_FLOOR
from .config import EmaTrendConfig
from .contracts import Campaign

D = lambda value: Decimal(str(value))


def used_campaign_risk(campaign: Campaign, config: EmaTrendConfig) -> Decimal:
    exit_fees = sum((lot.price*lot.qty*config.fee_bps/D(10000) for lot in campaign.lots), D(0))
    return (campaign.realized_loss_debit
            + sum((lot.qty*(max(D(0), lot.price-campaign.stop)+lot.fee_per_share
                           +campaign.gap_reserve_per_share
                           +lot.price*config.slippage_bps/D(10000)) for lot in campaign.lots), D(0))
            + exit_fees + (config.brokerage_per_order if campaign.qty else D(0)))


def size_plan(config: EmaTrendConfig, entry, stop, atr, available_cash,
              used_risk=0, *, lot_size: int=1) -> dict:
    if isinstance(lot_size,bool) or not isinstance(lot_size,int) or lot_size<1:
        raise ValueError("INVALID_QUANTITY_STEP")
    entry, stop, atr, available_cash, used_risk = map(D, (entry, stop, atr, available_cash, used_risk))
    if any(not v.is_finite() for v in (entry, stop, atr, available_cash, used_risk)):
        raise ValueError("non-finite sizing input")
    if entry <= stop or stop <= 0 or atr <= 0 or available_cash < 0 or used_risk < 0:
        return {"quantity": 0, "reason": "INVALID_RISK_INPUT", "budget": D(0)}
    budget = min(config.allocation_inr*config.risk_fraction,
                 max(D(0), config.allocation_inr*config.strategy_risk_fraction-used_risk))
    fixed_reserve = config.brokerage_per_order*8
    # Reserve a complete rebuild-and-liquidate path, not only entry brokerage.
    per_unit_cost = entry*(config.fee_bps+config.slippage_bps)*D(4)/D(10000)
    per_unit_risk = entry-stop + config.gap_reserve_atr*atr+per_unit_cost
    risk_qty = int(max(D(0), (budget-fixed_reserve)/per_unit_risk).to_integral_value(rounding=ROUND_FLOOR))
    cash_qty = int(max(D(0), (min(available_cash, config.allocation_inr)-fixed_reserve)
                           /(entry*(1+config.fee_bps/D(10000)))).to_integral_value(rounding=ROUND_FLOOR))
    quantity = min(risk_qty, cash_qty)//lot_size*lot_size
    estimate = fixed_reserve+quantity*per_unit_cost
    reason = "AUTHORIZED"
    if quantity < 4*lot_size:
        quantity, reason = 0, "ZERO_ALLOCATION_OR_INSUFFICIENT_RISK_CASH"
    elif estimate > budget*D('.15'):
        quantity, reason = 0, "COST_BUDGET_EXCEEDED"
    return {"quantity": quantity, "reason": reason, "budget": budget,
            "entry": entry, "stop": stop, "planned_r": entry-stop,
            "estimated_cost": estimate, "fee_schedule": config.fee_schedule,
            "capital_required": quantity*entry+quantity*entry*config.fee_bps/D(10000)+config.brokerage_per_order}
