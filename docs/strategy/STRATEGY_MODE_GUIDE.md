# Sterling strategy defaults and mode guide

Inspected 24 October 2026 against release `6fac9a1e` and its strategy-mode-truth continuation. This describes executable rules and saved operator settings, not a profitability recommendation. Future market-session acceptance and research promotion remain open.

## What “mode” means

A strategy answers **why to enter and exit**. A horizon answers **which completed candles to read and how long a position may remain open**. A signal source answers **which instrument supplies the indicator**. Manual/AUTO answers **who requests entry**; it does not replace admission, sizing, protection or reconciliation. Practice/live answers **whether execution can reach the broker**. A displayed row alone does not guarantee any of these gates passed.

SuperTrend and Snapback have additional horizon choices. Gamma retains its original expiry-derived holds and trigger choices; its settings explain those holds without showing a five-horizon selector. EMA retains only its original Daily/Weekly operational profile, confirmed against the operator-linked source post. Existing position management must remain available when a profile is retired.

## Saved settings inspected before this continuation

Read-only SQLite inspection of owner `default` found: SuperTrend Swing/60m fallback, one-red, fast trail, price stop on, shorts off, AUTO off; Snapback enabled/AUTO on, daily Swing with index-futures hedge and 15-session horizon; Gamma enabled/AUTO on, 5m signal and 15m display; EMA enabled, PAPER, AUTO off, with all four existing profiles selected. These are a dated snapshot, not proposed default changes. No account credentials are included. The later instruction to remove EMA add-on modes supersedes retaining those extra selections; the linked source post confirms the original is Daily signals / Weekly context.

## Original SuperTrend: 1-hour Heikin-Ashi, three SuperTrends

The code default is **Swing → 60-minute closed candles → Heikin-Ashi**. The saved owner `default` currently also selects swing, with timeframe unset (therefore 60 minutes). A mode called Swing here does not mean daily candles.

| Line | ATR period | Multiplier |
|---|---:|---:|
| Fast | 21 | 1 |
| Mid | 14 | 2 |
| Slow | 7 | 3 |

Fast/mid/slow describe responsiveness associated with these configured lines, not ascending ATR period. The HA transformation supplies indicator OHLC; it does not create executable market prices. Raw market highs/lows are used to assess actual stop touches. The production configuration adapter retains the HA default; raw candles are an explicit offline engine override.

A **green entry arrow** is the first completed bar on which all three trends become bullish after the preceding bar was not fully bullish. It is not a separate fourth indicator, and three green lines remaining green do not create a new entry every hour. A forming candle can change before close and is not a confirmed scan entry. Warmup and fresh/current-contract checks apply.

For the underlying source, the default is long-only (`allow_short=false`): bullish transition leads to CE candidates. Enabling shorts explicitly permits bearish-underlying PE entries. This does not prohibit buying a PE whose own option premium has a bullish signal in derivatives mode.

### Stops, exits and target

The default is `exit_mode=one_red`, `trail_target=fast`, `price_stop_exit=true`, `exit_aligned_trail=false`. A position can end on the first opposing ST line or a price stop breach, whichever happens first. The price trail ratchets toward reduced risk and cannot loosen. Historical stop-touch evaluation uses the stop known at the previous close and raw subsequent high/low, avoiding use of a future indicator level.

Selectable counters are: one opposing line; two opposing lines; all three opposing lines; or all three plus a fresh counter-arrow (`three_red_signal`). With the tight price stop still enabled, price can exit before a looser counter. Selecting “three red” does not promise the trade will survive a breached stop. The separate experimental exit-aligned-trail setting alters the trail reference; it is off by default.

The original engine has **no fixed take-profit** (`take_profit=None`). A displayed Navigator target or explicitly supplied plan target must not be described as a native triple-ST target. Optional time-stop bars, ADX and ATR-percentile entry filters are off in the engine defaults. Protection, expiry and frozen mode deadlines can close a held position independently of a new arrow.

### SuperTrend horizon differences

The three period/multiplier pairs and HA basis stay the same across these modes. The bar duration changes: ATR21 on 1-minute candles is not the same measurement as ATR21 on hourly candles. Each mode requires its own evidence; the hourly research does not validate the shorter modes.

| Horizon | Default signal candles | Allowed candle sizes | Expected holding budget | Canonical hard ceiling |
|---|---|---|---|---|
| Ultra scalp | 1m | 1m | 1–10 minutes | 20 minutes, also same-day square-off |
| Scalp | 5m | 1m / 3m / 5m | 10–45 minutes | 90 minutes, also same-day square-off |
| Intraday | 15m | 5m / 15m / 30m | 45 minutes–4 hours | Session square-off, 15:20 IST |
| Overnight | 60m | 30m / 60m | 1–3 trading sessions | Execution policy binds at the 3-session ceiling, 15:20 |
| Swing / original | 60m | 60m | 3–10 trading sessions | 15 trading sessions, 15:20 |

Expected holding windows are budgets, not minimum holding periods or profit targets. Stops/reversal can close immediately. Overnight's generic taxonomy has a five-session emergency limit, but `freeze_policy` deliberately uses its three-session expected ceiling; the table reflects the execution deadline. Overnight requires broker protection and expiry buffer, and refuses entry before a closed-session gap. The deadline is frozen at entry; changing Settings does not extend an existing position.

### SuperTrend signal-source differences

| Source | Where the three SuperTrends run | Entry interpretation | Exit evidence |
|---|---|---|---|
| Spot | Underlying | Fresh bullish underlying transition selects CE; bearish PE only if enabled | Shared underlying rule, mapped premium protection |
| Derivatives | Each option contract's own premium candles | Fresh bullish premium transition buys that CE or PE | Each contract's own stop, reds, dates and reason |
| Confluence | Underlying plus option premium | Fresh underlying signal plus currently bullish qualifying premium | Shared underlying setup plus executable premium protection |
| Navigator | Separate originating strategy/plan | Follow its originating plan | Do not present its explicit target as triple-ST maths |

“Spot” names the source; it does not mean an index itself is an executable option. Premium mapping from an underlying move is an estimate, distinct from a broker fill or broker stop acknowledgment. Derivative groups summarize multiple contracts: inspect the exact contract, because one leg can be closed while another remains active. Unknown legacy contract dates cannot authorize a new entry.

## Gamma-Unwind: original rules only

Gamma is an **OI-unwind / option-premium breakout** strategy. It is not triple-ST and has no Ultra scalp/Scalp/Overnight selector. Its source-backed default entry is `live_breakout`; `closed_5m` is an explicitly experimental alternative trigger, not a new holding mode. The 15-minute display chart does not change its 5-minute arming rules or turn it into a 15-minute strategy.

The watchlist is built around daily supply/demand zones and nearby eligible option strikes. The top-two OI selection, liquidity, direction, zone and unwind gates apply. Candidate/watch/near-entry tiers are not equivalent to executable ENTRY.

A closed 5-minute bar arms the original E2 cycle when A+B+C+H hold:

- A: OI has fallen sufficiently from its session peak: configured 8% minus 1 percentage point tolerance, effectively 7%; 12% is the strong threshold.
- B: latest volume exceeds the inclusive 20-bar mean and at least two of the last three bars exceed it.
- C: a six-bar consolidation fits the configured ATR range, with the specified downward-drift allowance.
- H: range is at least two ticks and there is at most one zero-volume slot in the consolidation.

E1 then requires a live premium tick above the armed consolidation high, forming-bar volume above the closed-bar SMA20, and the final entry gates. E2 is an internal watch state. A tick-level OI rebound does not independently cancel an already armed cycle: the original A condition arms E2. Later rearm has its own extra OI-drop rule.

| Original hold | Selection | Stop and exits |
|---|---|---|
| Intraday | Contract DTE ≤ 1 | Base-low premium stop; T1 +20% moves stop toward entry; T2 +40% closes; 15:20 square-off |
| Swing | DTE > 1 / unknown follows conservative swing classification | Original underlying D68 daily-close zone stop before T1; T1 entry protection, T2, configured fifth session / expiry |

Targets are tick-rounded upward. T1 does not invent a partial-booking fraction; the supplied rule did not define one. Actual fill price supplies live target economics.

**Capability boundary:** live Gamma swing entry is currently blocked (`reference_swing_protection_unavailable`) because its original pre-T1 rule has no broker premium stop. The app does not invent a swing stop to force admission. Live intraday requires broker/both protection; monitor-only cannot originate it. Enabled AUTO is an entry request permission, not proof an otherwise blocked signal will trade. Gamma's original rules remain unchanged by this continuation.

## Snapback: daily swing versus the shorter modes

Snapback is a **mean-reversion/fade** strategy; SuperTrend follows trends. The default is daily Swing, not hourly HA. Default power/AUTO are off in code; the saved owner has both enabled. Research promotion is still unmet, independently of the saved switches.

### Original daily Swing

A close exceeds the strictly prior 20-session high and sits at least 1.5 ATR above its EMA20. The default bearish-market EMA50 gate filters the book. The selected vehicle is a bought put to fade the upside extension. Buying calls on downside extensions requires `allow_fade_down`, off by default. Volatility rank, universe, contract liquidity/spread/expiry and sizing gates still apply; extension alone does not place an order.

The default contract target delta is 0.70, monthly expiry 40–60 DTE. The configured default hedge is index futures; it consumes margin separately from option premium. Hedged and unhedged results cannot be pooled as the same strategy.

The saved swing exits are 35% premium stop, 15-session horizon, no immediate premium-percent trail (`premium_trail_pct=0`). At the horizon, a position at least 1.5× its entry premium can become a runner; the default runner giveback is 25% of its recorded closing peak. The live swing manager checks premium protection every observation and advances horizon/runner/expiry decisions at its end-of-day mark (15:15–15:25), with missed-pass recovery. This is not a fixed +5-point target or a generic 1h SuperTrend exit. Inspect the exact frozen plan, including expiry buffer and hedge closure; CLOSED requires both legs reconciled flat.

### Additional Snapback horizons

These use a different entry hypothesis: the prior closed candle extended outside a 20-observation, 2-standard-deviation Bollinger band, and the latest closed candle reverses back inside on the relevant side of the mean. EMA9 confirmation and ADX filtering are optional, not always-on proof. The default fade is an upside extension/reversal buying PE. Relative-volume and contract/cost gates remain distinct.

| Selection | Settings-selector preset | Non-runner / runner bar limits | Carry |
|---|---|---|---|
| Ultra scalp | 1m | 20 / 20 | No |
| Scalp | 1m (editable 1/3/5m) | 20 / 60 | No |
| Intraday | 15m (editable 5/15/30m) | 16 / 24 | No |
| Overnight | 60m (editable 30/60m) | 18 / 18 | Bounded by three-session policy, protection and closed-session restrictions |
| Swing | Daily, separate rules | 15-session horizon then configured runner | Yes, strategy-specific expiry rules |

These are UI selection presets, not automatic rewrites of every saved field whenever a config is read. For example this account has saved `scalp_timeframe_minutes=3` while selected mode is Swing; that dormant number does not turn the daily scanner into a 3-minute scanner. Editing the short candle size also changes the duration represented by a bar cap; frozen mode deadlines still bind first.

Short-mode baseline economics: requested target +5 option-premium points, stop −4, trail 2, profit lock +1, with costs and minimum net reward/risk. The effective target may be raised to pay costs. It is a soft runner trigger, not guaranteed limit execution; a weak retracement can exit, a strong continuation can run under its own trail/time cap. Points mean option premium, not index points. The default entry window is 09:35–14:45 and square-off 15:15, stricter than the generic 15:20 ceiling. The reviewed published plan and actual live lifecycle take precedence over a generic mode label or OHLC replay example.

### Signal-explanation fix in this continuation

Previously short-mode signals always claimed EMA9 confirmation and an ADX pass even when both optional filters were off. The fix retains the Bollinger reversal reason, includes EMA9 only when used, and includes ADX plus its configured cutoff only when used (including an explicitly lowered threshold). Signal selection, sizing, stops and saved controls do not change. Regression cases cover disabled filters, enabled filters, and a lowered cutoff.

## EMA: original Daily/Weekly only

The operator identified [Trading With Mind's source post](https://x.com/apoorvsinghasr/status/2110394084598960273), read directly on 24 October 2026. It explicitly makes daily EMA signals the execution driver and Weekly SuperTrend the higher-timeframe context. It is not the older hourly engine. No Intraday, Scalp or Ultra scalp modes belong to this operational strategy.

The source framework uses EMA10/21, DEMA50 and ST(10,3). A bullish EMA cross arms an entry opportunity; a retest or tight 3–5-day consolidation breakout needs bullish confirmation and a stop below the relevant swing low. Profit management reduces quarters at 4R and persistent weakness below each EMA, with the final structural exit following a bearish cross and confirmed lower high. Add-backs follow renewed EMA strength, higher-low structure or pivot breakout, subject to risk capacity. Weekly ST supplies context rather than replacing daily entries. Fixed risk, no averaging down, no widening stops and estimated costs remain relevant.

The current engine supplies explicit operational definitions for pivots, confirmation, setup expiry, rounding, sizing and partial fills; those details are implementation decisions where natural-language source rules are underspecified. Its original Daily/Weekly profile and indicator maths are unchanged. It remains **practice/PAPER only**, with server-side live refusal.

This continuation removes extra profile selectors and quick-start modes, advertises only the original profile in the operational API, refuses retired-mode config/scan/plan/arm/readiness/replay requests, and rejects them in the unified backtest path before market access. Background scans/automatic entries select only the original. Legacy held campaigns retain their profile-specific management and exits; new add-backs are refused, and a reserved, unsent retired-profile BUY is cancelled internally rather than submitted. Submitted/uncertain orders are still reconciled, never discarded.

Legacy mathematics/types remain for recorded data and held-position interpretation. Read-only history, charts, exports and saved replay results remain accessible. The config response labels retired stored selections; reads do not rewrite the database. Applying settings through the API persists only the original profile and preserves other settings. This is mode retirement, not deletion of trading history or a claim of full literal conformance to an underspecified social-media post.

## Reading the numbers: simple examples

These illustrate configured rules before fees/slippage; they are not quotes or promised fills.

| Strategy | Example | Meaning |
|---|---|---|
| Original SuperTrend | Three lines become green on a closed hourly HA bar | A fresh alignment event. Remaining green next hour is not another entry; there is no automatic fixed +20% target. |
| Gamma | Confirmed premium entry ₹100 | T1 is ₹120 and T2 ₹140 before tick alignment. T1 moves protection toward entry; the reference does not specify an invented partial quantity. |
| Snapback short mode | Premium ₹100, requested target +5 and stop distance 4 | Initial requested levels ₹105 / ₹96; the reviewed effective target may rise to cover costs. The target can become a runner trigger. |
| Snapback Swing | Premium ₹100, default stop 35% | Premium-stop level ₹65. At its horizon, ₹150 qualifies for the default 1.5× runner condition. A later recorded closing peak ₹200 with 25% giveback implies ₹150, subject to the frozen live rules. |
| Original EMA | 100 shares, entry ₹100, initial stop ₹95 | 1R is ₹5/share; +4R is ₹120. A base-quarter request is 25 shares. Actual confirmed shares and partial fills remain authoritative. |

## Manual entry-to-exit checklist

1. Confirm authenticated broker state, global control, strategy and selected horizon/source. A logged-in profile does not by itself establish exposure; read positions/orders/protection.
2. Inspect the exact signal's completed-bar time, contract, expiry, fresh/current eligibility, entry, initial stop, current trail, target provenance and exit reason. A running old signal is not a fresh entry arrow.
3. Review the actual published execution plan: lots/quantity, premium cash, stop risk, costs, required hedge margin, protection and frozen deadline. Re-review if the plan changes.
4. Submit through the strategy's owned canonical path. Inspect admission result and broker API response; acknowledgment, rejected AMO and successful fill are different outcomes.
5. Manage the confirmed owned position. Watch stop acknowledgment, signal counter, trail, strategy target/time/expiry and partial/unknown states. An entry halt must not disable exits.
6. Confirm broker exposure and all owned legs are flat, then journal actual fills/rejections, exit cause, costs and notifications. A board row marked ended is not proof the account is flat.

## Pending acceptance: cannot be replaced by source tests

The prior implementation ledger remains authoritative for each tracked item. Open requirements are two consecutive real Gamma recovery days with a midday restart/watch proof; a complete same-release market-load session; real Snapback restored rows and AUTO retention; actual notification receipt; authoritative option/OI/fill data and independent holdout/exposure evidence. Hosted CI billing is externally blocked. The chosen temporary Cloudflare tunnel is retained and reports degraded status truthfully when providers are unavailable. Funds/static-IP problems remain excluded from the user's QA scope; existing risk gates are preserved.

AMO rejection can prove a request reached the broker; it cannot prove a fill, protective trigger or trading edge. No broker mutation is needed for the explanation fix.

## Source map

- `backend/app/engines/sterling_kite_engine/config.py`, `regime.py`, `exits.py`, `engine.py`: original HA/triple-ST maths and exits.
- `backend/app/services/kite_engine/service.py`, `scanner.py`, `monitor.py`: production adapter, completed candles, source/contract logic and supervision.
- `backend/app/core/horizon.py`, `mode_execution.py`, `trade_horizon.py`: shared budgets and immutable execution deadlines.
- `backend/app/engines/gamma_move/config.py`, `trigger.py`, `exit.py`; `backend/app/services/gamma_move_runner.py`: Gamma source defaults, E2/E1, original holds and live capability gate.
- `backend/app/engines/snapback/config.py`, `strategy.py`, `intraday.py`; `backend/app/services/snapback_live_exits.py`: daily/short hypotheses, premium economics and live exit lifecycle.
- `frontend/src/components/SnapbackSettings.tsx`: actual selector presets.
- `backend/app/engines/ema_trend/config.py`, `setups.py`, `state_machine.py`: current EMA profiles and campaign lifecycle.
- `docs/qa/2026-10-23/open-gaps/IMPLEMENTATION_LEDGER.md` and `docs/qa/2026-10-24/board-contract-provenance/CONTINUATION.md`: fixes versus remaining observed acceptance.

## In-app default labels and configuration comparison

Settings and the signal workspace display **Default / Original** separately from **Selected**. A default tag identifies the factory/original strategy; it is not proof that the account currently uses that mode. Expand **Compare modes and configuration** for preset intervals, permitted overrides and holding limits. The selected line reports effective candle size and candle type. Settings readouts follow the draft until Apply; board readouts show saved settings. A row or open position can carry a different frozen mode. The board MODES menu only filters displayed rows. EMA and Gamma show **Default · Original only**, without additional horizon choices.

For a beginner: **OHLC** means actual open, high, low and close prices for the candle interval. **Heikin-Ashi** transforms those values to read the trend; its displayed values are not executable prices. **Signal timeframe** is the interval the strategy evaluates; a chart's display interval can be separate. **ATR period 21** means 21 candles of the selected signal interval, not always 21 hours. **Bars** in a Snapback hold limit use that mode's candle size, and earlier stops, expiry and session ceilings still apply. Default does not mean active, validated or recommended for every account.
