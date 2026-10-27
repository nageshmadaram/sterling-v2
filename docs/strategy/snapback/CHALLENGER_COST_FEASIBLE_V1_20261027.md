# Snapback cost-feasible challenger v1 — independent research identity

Created 2026-10-27. **Do not merge its results into the incumbent strategy's
track record.** Baseline `replay_intraday` remains unchanged when
`candidate_policy="baseline"`. The challenger uses
`candidate_policy="snapback_intraday_cost_feasible_v1"`. It is RESEARCH ONLY;
it cannot promote or activate a live broker lane.

## Fixed hypothesis / no post-result optimisation

The baseline raises a target to meet net reward-to-risk, but a higher target
can be economically meaningless in an option contract whose *prior observed
option-premium movement* rarely reached such a move. We hypothesise that
refusing these low-headroom entries will improve **after-cost** risk-adjusted
performance, possibly at the cost of much lower trade count and missed
outliers. This is a hypothesis, not an observed edge.

Frozen v1 rules, selected **before** any new report result:

1. Start with exactly the original Snapback intraday setup, candidate option,
   1-lot limits and existing baseline plan. Do not rewrite earlier signals.
2. Add **0.50 option premium points per unit, per round trip** to the baseline
   execution costs and account for it in *every closed trade's simulated P&L*.
3. Recompute target so stressed `net_reward_risk >= 1.00`.
4. Recompute stop risk with the additional cost; reject if the stressed risk
   exceeds the original available session/trade risk budget.
5. Compute a 75th-percentile favorable option-premium excursion from
   **only prior completed same-session windows** (sliding windows over the
   previous 512 closed option candles at most). The window uses
   `scalp_max_hold_bars`, with a lower bound of 2 bars. Require at least
   **12** contiguous prior observed same-session windows; no forward bars,
   interpolation or synthetic option marks. Refuse if target exceeds this
   historic excursion ceiling or if history is insufficient.
6. NEVER treat the historic candle high as executable bid/depth or guarantee
   a fill. No futures-hedge P&L is modelled in this option-only challenger,
   so `whole_strategy_net_inr` stays unavailable in hedged configurations.

This is deliberately *one* challenger, not a swept grid chosen by whichever
version looks profitable in a small historical sample. Any later parameter
change needs a different ID and untouched forward observations.

## Execution and comparison

The read-only `backend/scripts/backtest_snapback_timeframes.py` report now
calculates baseline and challenger on the **same** synchronized option/spot
tapes and instrument selection, retaining rejected, skipped, errored and
incomplete contracts. Its `minute_challenger_research` report key shows
separate mode totals and trade-level audit records. Baseline results remain
under `minute_modes_real_options`.

The historical script currently chooses from the broker's present-day
instrument master. That is **not historical date-effective selection** and
its backtest is **not** an untouched independent prospective test. The
research output explicitly records this limitation, and no profitability
promotion follows automatically.

## Tests and evidence

- In the isolated Python environment, `git hash-object` of the pure
  `challenger.py` source was exactly
  `6f7ea9e82bf9837f15493a6eb1e147d84e10b564`, matching the GitHub
  blob fetched from the branch. Five independent synthetic unit checks
  **passed** using this byte-identical module: accepted/stressed fee
  accounting; unacceptable cost-vs-move; excess daily risk; too little
  history; refusal of post-entry candle data.
- The repository also has integration/regression cases in
  `backend/tests/engines/snapback/test_challenger_cost_feasible.py`.
  They check replay baseline isolation, no future leak and actual closed
  trade P&L debiting. **These complete Sterling integration tests have
  not been run in the authenticated workstation environment.**
- The offline test entry point now includes this new file:
  `bash scripts/snapback_offline_acceptance.sh`, with
  `STERLING_LIVE_EXECUTION=false`. The command does not send orders.

## Predeclared comparison / automatic failure criteria

Compare each strategy mode separately and include every originally eligible
instrument/session, even its failure/no trade. Do not select only profitable
contracts. Report baseline and challenger contract count, completed fills
(modelled vs actual), zero-signal rate, mean and total *after-cost* P&L,
Sharpe on source sessions, max MTM drawdown, day-clustered confidence
interval, 1x/2x/3x cost stress, out-of-sample performance, and top-1%
winner dependency. Missing paired futures execution costs and missing
date-effective contract history remain **failures**, not zero costs.

The production release standard remains
`docs/evidence/PROMOTION_POLICY.md`: at least 300 authoritative
completed trades in this exact lane, 60 complete independent sessions,
confidence lower bound > 0, robustness at 1x/2x/3x actual costs,
drawdown <=10% of allocated strategy capital, zero unresolved exposure,
and independently verified broker stop/exit/recovery and operator approval.

**Current status: NEW CHALLENGER UNDER RESEARCH. NO PROFITABILITY CLAIM.
NO BROKER CERTIFICATION. LIVE NEW ENTRIES OFF.**
