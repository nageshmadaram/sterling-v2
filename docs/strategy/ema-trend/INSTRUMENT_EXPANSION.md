# EMA Trend instrument and contract expansion

Requested 2026-10-17: shared selection experience plus actual derivative execution.

The selected contract's own ordinary OHLC drives EMA/DEMA/Supertrend. An index is a non-tradable reference: choose its actual listed futures or long-option contract. No synthetic index fill, automatic rollover, naked option selling, or substitution of underlying/index prices for contract premiums. Config stores exchange-qualified exact instruments; token, lot, tick, expiry and type are resolved from the broker, never trusted from browser quantities.

Implementation sequence:
1. Exact-instrument selector in Connect using current broker instrument search, grouped instrument/contract controls and explicit capability reasons.
2. NSE/BSE equity plus NFO/BFO futures and long CE/PE contract validation; expiry-day new entries blocked and expiry exposure scheduled for closure. CNC/MIS for equities, NRML/MIS for derivatives.
3. Whole-lot admission, reductions and add-backs. At least four lots for a new staged campaign; quarter=floor(initial lots/4) lots. No rounding risk upward. Futures reserve full notional conservatively; options reserve premium. Existing canonical virtual exchange/journal/ledger remain authoritative.
4. Propagate frozen contract metadata through setup, plan, campaign, chart, replay, history/export and UI. Historical replay must use exact contract bars and explicit broker metadata, not infer lot sizes.
5. Commodity integration requires additional work: the existing canonical virtual exchange supports NSE/BSE/NFO/BFO only and assumes a P&L multiplier of 1. MCX also requires verified session/calendar, contract multiplier, fees, expiry/delivery policy and commodity cash/margin accounting. Until those are implemented and tested, list actual MCX contracts with an explicit execution blocker; never mark commodity execution supported from a selector alone.
6. Independent tests for non-unit lots, partials, remainder tranche, risk/cash, metadata changes, expiry, exact-contract replay and product/UI parity. Preserve cash-equity fixtures and live-disabled boundary.

Primary sources: [Kite instrument metadata](https://kite.trade/docs/connect/v3/market-data-and-instruments/), [Kite position multipliers](https://kite.trade/docs/connect/v3/portfolio/), [Kite order margin API](https://kite.trade/docs/connect/v3/margins/), [broker explanation of MCX multipliers outside instruments.csv](https://kite.trade/forum/discussion/10244/api-showing-wrong-lot-size). These references describe adapter requirements, not economic evidence.

Status: strategy version 1.1.0. NSE/BSE equity and NFO/BFO FUT/CE/PE paper paths implemented with canonical whole-lot entry/reduction/close tests, broker selector, metadata fences, conservative capital reservation and exact-contract replay. Commodity execution is not implemented or accepted. No live release is authorized by this expansion.
