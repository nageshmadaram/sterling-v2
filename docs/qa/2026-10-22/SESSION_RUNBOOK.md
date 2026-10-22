# Trading-session acceptance and trader walkthrough

This is the expected session from startup to final records. It is also a remaining acceptance checklist, not a claim that a real trading day was observed on 22 October. The market was already closed. Actual broker acceptance and fills were not tested. Automated order/exit scenarios used isolated state and synthetic broker transport.

## 1. Start and identify the book

1. Open the local app and enter KITE. Verify the operator, exact account and **real versus virtual money** before opening an order ticket. A connected data feed is not entry permission.
2. Record frontend build SHA, loaded backend SHA, config revision and the session date/timezone. Resolve QA-01 before certification. A frontend rebuild does not update a running Python engine.
3. Inspect Operations/readiness, unresolved order intents, broker positions, active orders and active GTTs. A zero Sterling position count does not prove broker flatness.
4. Inspect market session, feed age, signal source and candle interval. A prior close quote is not a new tick. An incomplete historical reconstruction is not a successful live scan.
5. Use read-only operator diagnostics from the repository root as appropriate:

```sh
./sterling.sh status
./scripts/sterlingctl status
./scripts/sterlingctl doctor
```

The tracked `sterlingctl` is under `scripts/`. During this audit no restart, safe-mode change, live arming or reconcile mutation was issued. If a check reports funds/IP mismatch, record it as excluded from this requested QA scope; do not disable the control in the production service.

**Acceptance artifacts:** `session-manifest.json`, read-only health/readiness snapshot, selected account identity, broker exposure observation, current release/config identity. These should be redacted before sharing.

## 2. Select strategy and mode

Keep separate axes visible:

- **Strategy:** SuperTrend / Snapback / Gamma Unwind / Price Rules. EMA Trend is an extra paper research surface.
- **Holding policy:** Ultra scalp / Scalp / Intraday / Overnight / Swing, only where implemented.
- **Execution authority:** MANUAL versus ALGO.
- **Book/source:** Real account / virtual account / historical replay.

Do not infer that “scanner running” means ALGO is armed. Price Rules' enable switch and per-plan arming are separate from the scanner's master ALGO. Do not switch real-account modes merely to exercise a browser test; change modes in an isolated test book with no broker transport.

| Policy in shared mode code | Default signal interval | Expected hold | Hard constraint to inspect |
|---|---|---|---|
| Ultra scalp | 1m | 1–10 min | 20 min; same-session cutoff |
| Scalp | 5m shared SuperTrend default | 10–45 min | 90 min; same-session cutoff |
| Intraday | 15m | 45 min–4 h | Session square-off, currently 15:20 IST in app policy |
| Overnight | 60m | 1–3 trading sessions | Execution policy freezes a three-session deadline and disallows weekend-gap carry |
| Swing | 60m shared mode default | 3–10 trading sessions | Frozen deadline, up to 15-session emergency ceiling in shared taxonomy |

Snapback scalp defaults to 1m, not 5m, and daily swing discovery is distinct. The stored per-position policy is authoritative; never stretch an existing holding by editing today's settings. Overnight additionally requires expiry buffer and broker protection. An unavailable calendar blocks new exposure.

**Acceptance artifact:** exact strategy/mode/interval/source/config revision and capability/refusal reason. Unsupported modes must be explicitly unavailable, not silently mapped to another mode.

## 3. Read the signal before a manual entry

Every actionable row should answer:

1. Which exact dated contract, exchange, call/put, strike, lot size and expiry?
2. Is this a new eligible entry, a watch, a still-active setup, a historical row, or an actual owned position?
3. Which completed candle authorized it? What are source time, receipt time and age?
4. What is the working entry price and current executable bid/ask/depth? Are spreads/metadata checks enabled?
5. Is SL in underlying points or option premium? Is it planned, pending or verified at the broker? What actually trails it?
6. Is the target real, absent, modelled or hypothetical? Does T1 move protection or realize profit?
7. What is the maximum requested size, premium outlay, stop risk, maximum premium loss, charges estimate and holding deadline?
8. What event will exit it, and where will a failed/partial/UNKNOWN exit appear?

Observed good refusals: Gamma WATCH rows disabled with “Only ENTRY tier may execute”; historical Snapback rows read-only; ended SuperTrend rows disabled after a trail breach. Current SuperTrend fast-mode freshness and AMO fallback defects mean the remaining actionable-looking buttons are not a release approval.

## 4. Strategy-specific entry and exit contract

### SuperTrend

- Three differently parameterized SuperTrend lines on the configured Heikin-Ashi candle basis; source may be spot, derivative, both or confluence.
- Manual ticket goes through `/api/v1/kite/orders` to the canonical manual authority; detail entry has `/api/v1/kite/engine/order`. Both must bind the exact server plan and mode.
- Size must be a valid dated-contract lot multiple and satisfy both manual risk budget and caps. Despite a UI hint saying “automatic”, the current `cfg.max_lots` is also checked for live manual entries.
- Initial stop is taken from the strategy plan. Premium/underlying translation must preserve CE/PE delta sign. TSL must tighten without increasing risk.
- Exit may be price stop, one/two/three red SuperTrend lines, three red plus counter-signal, time stop, frozen mode deadline, expiry rule or explicit owned exit. UI “3R” here means red lines, not a three-times-risk profit target.
- There is normally **no fixed SuperTrend profit target**. Do not invent one in the calculator, clipboard or order ticket.
- Never treat order acknowledgement as filled or protected. Server ownership continues if the browser closes.

**Required records:** signal ID/generation/source/mode, canonical intent, entry order, confirmed fill ledger, position, protective GTT + child IDs, exit order/fills and journal row. QA-02/03/07/08 must be repaired.

### Snapback

- Daily swing setups and minute-bar modes have different freshness and entry windows. Modelled or unresolved contracts are not executable live plans.
- Manual browser entry publishes only the signal ID to `/api/v1/snapback/plans`, receives a server plan revision, and arms that exact revision at `/plans/{id}/arm`. The dispatcher owns execution, hedge, protection and reconciliation.
- A successful arm response means **plan armed/scheduled**, not bought. Continue observing plan state and actual orders/fills.
- Minute modes use their persisted premium stop/target/lock/trail and bar/holding budgets. Swing uses its configured mean/horizon/runner rules and hedge lifecycle. Display those actual rules rather than a generic one-size-fits-all exit.
- A swing option exit and any owned hedge close must reconcile together. A pending hedge or unknown hedge close remains a blocker, not a flat portfolio.
- Current closed-market AMO caption overstates support; use the server's refusal. No generic ticket should bypass this strategy's authority.

**Required records:** signal, server plan/revision, arm request/idempotency key, option and hedge intents/fills, owned stops, exit outcome, cost/realized P&L provenance and finalization evidence.

### Gamma Unwind

- Distinguish watchlist membership, hidden E2, E1 breakout and the four-gate final tier. A watch is not a trade.
- Manual entry uses the strategy's candidate arm action, not arbitrary ticket fields. Only 4/4 ENTRY may execute. Intraday live requires broker/both stop; live swing is deliberately refused.
- Targets use tick-rounded entry-based levels: **T1 +20%, T2 +40%**.
- **At T1 the implemented reference lifecycle moves the stop to entry; it does not define a partial profit-booking fraction.** T2 closes. The guide must not promise cash profit at T1. Intraday cutoff currently uses 15:20 IST. Swing/reference lifecycle additionally has day/expiry/D68 rules but is not approved for live entry.
- Missing pre-T1 swing premium SL must stay absent. Do not accept a default calculator stop as strategy protection.
- A triggered GTT is not necessarily a completed exit. Check its child, fills, remaining units and replacement stop before another sell.

**Required records:** watch/E2/E1 identity and gate snapshot, candidate revision, entry intent/fill, actual broker stop, T1 state transition/stop modification, T2/SL/TSL exit and the journal. Record partial historical recovery separately.

### Price Rules

- Exact listed long index option, lots, positive stop distance, optional target and manual/price comparator. Each rule is drafted and explicitly armed with revision checking.
- “Manual after arming” fires through its server lifecycle; it is not a second unrestricted raw-order path. A one-shot condition must not refire after acknowledgement, restart or a lost response.
- Protection is an owned static broker stop/OCO as applicable, based on confirmed fills. No automatic trailing-stop or five-mode holding deadline is established by this panel; do not promise either.
- Partial exit choices are 25/50/75/100% of owned remainder with broker-lot/quantity constraints enforced by the server. Stop cancellation/child resolution must precede competing sell; residual quantity must be protected again.
- Trigger validity controls how long an unfilled rule can wait. It does not automatically square off an already-filled position at 15:20.
- Remove Escape liquidation before release. Use deliberate owned-position exit controls.

**Required records:** draft/revision, rule expiry/comparator/watch instrument, arm transition, entry intent/order/fills, owned GTT and protected units, each partial exit and final state. Actual CSV currently has columns for these identities and gross realized P&L but no populated rows were available.

## 5. After-market request verification

For a safe engineering test, intercept broker transport and use synthetic credentials/state. Record exact request method/path/form and deterministic response for each branch:

| Branch | Required behavior |
|---|---|
| Explicit valid AMO | `/orders/amo`, bounded LIMIT, exact contract/lot/product/validity; queued status |
| Regular order denied only because session closed | No silent carry instruction; explicit reviewed AMO policy required |
| Stale, forming or wrong-mode signal | Zero broker requests regardless of variety |
| Funds rejection | Definitive refusal, no successful-fill fiction, no uncontrolled retry; excluded account condition in this audit |
| IP rejection | Report exact broker refusal; no retry/bypass; excluded account condition in this audit |
| Lost reply | UNKNOWN durable intent, reconcile, no resend |
| Accepted but unfilled | Requested size separate from filled size; no GTT for unowned units |
| Cancel queued AMO | Correct `/orders/amo/{id}` cancel route; terminal confirmation required |

The completed audit captured valid AMO/cancel wire mapping and stale-AMO behavior in mocks. Real broker acceptance was not exercised. A successful API acknowledgement, insufficient balance or a market-closed message is not proof that an order is terminal or harmless.

## 6. Manage the session and failures

For every held position, show actual fill basis, requested/filled/remaining quantity, stop ownership/verification, pending child order, active trail, next exit event and frozen deadline.

- Repeated click/reload: same intent and no duplicate entry.
- Partial entry: protect filled units, keep unfilled portion explicit.
- Missing stop or failed replacement: show filled exposure as unprotected/pending; repair or exit through its owner, never a second browser stop.
- Stop gapped through its limit: observe the fired child; do not call it filled; use bounded cancellation/recovery and owned-remainder exit after the grace policy.
- Manual exit racing SL/TSL: one owner/claim, no extra sell and no naked short.
- Disconnect/restart: retain durable intent/fills, reconcile broker truth and maintain exits while new entry is blocked.
- Loss halt/kill/safe mode: new exposure blocked; owned exits and protection remain available. A halt is not broker flatness.
- Unknown price/fill/fees: keep UNKNOWN. Do not fill gaps with stop prices, LTP or zero fees and present them as realized results.

These paths have significant passing automated coverage. Actual live network loss, device notification delivery and exchange fill behavior were not induced in this running service.

## 7. Alerts and notifications

Inspect the full semantic chain: signal/watch → arm/publish → queued/ack → partial/full fill → protection confirmed/failed → trail/target event → exit pending/filled → realized outcome → recovery.

Every event should contain strategy, mode, exact contract, account alias, event/source time, quantity, intent/order/position ID and a next action. Dedupe by stable lifecycle identity, not display text. Informational watch alerts should not look like actionable entry signals. Critical protection/recovery errors should remain visible until resolved.

In this audit, Telegram configuration was inspected and outbox/sink logic tested with mocks. The real TEST button was not pressed; push permission, Telegram receipt, sound, background delivery and deep-link routing on an actual phone are **not verified**. Include controlled delivery receipts in the final operator acceptance without sending account data to an unintended recipient.

## 8. Close the session and reconcile artifacts

1. Identify each intraday position's actual hard exit. For carry positions, explicitly review expiry, overnight/weekend rules and broker-held protection.
2. Reconcile entry/exit fills and active orders with broker net position. Do not stop the backend merely because the browser says the session ended.
3. Confirm that no triggered GTT child or pending AMO can create unexpected later exposure. Cancel/retire only through the authorized owner and verify terminal broker state.
4. Persist final mode policy, timestamps, exit reason, remaining quantity and any unresolved items. A timeout is not a finished trade.
5. Export journal/history and check joins and arithmetic. Retain the original broker identity separately from display formatting.

| Artifact | Must join to | Minimum invariant |
|---|---|---|
| Session manifest | Runtime + config + account + source | Immutable release/config/timezone identity |
| Candle/quote | Instrument + exchange + source time | Completed authorized bar; source/age are explicit |
| Signal | Candle + strategy/mode/generation | Stable ID, eligible/ineligible reason and exact levels |
| Published plan | Signal + revision + quantity | Reviewed server-owned payload |
| Order intent | Plan + idempotency + risk approval | One immutable send claim; unknown response never replayed |
| Broker order | Intent + broker ID | Variety/product/type/lot/side/price match |
| Fill ledger | Broker order/trade IDs | Cumulative fills never exceed requested or go backwards |
| Position | Confirmed fills + frozen policy | Actual owned remainder, not setup status |
| Protection | Position + GTT + child IDs | Verified protected quantity no greater than owned remainder |
| Exit | Position + protection cancellation + SELL fills | No double sell; closed only after confirmed remainder zero |
| Trade journal | Signal/intent/fills/exit | Exit reason/time/price and gross/net/unknown-fee provenance |
| Alert | Lifecycle event + delivery attempt | Deduped, state-correct, delivery separate from creation |
| CSV/copy/export | Same authoritative journal/plan | No invented SL/R/P&L; exact units and explicit gross/net label |

For a long option, confirmed gross realized P&L is `sum(exit_filled_qty * (exit_fill_price - weighted_entry_fill_basis))`, accounting correctly for partial fills and costs. Do not use the signal's theoretical entry or stop as a substitute for missing fills. Fees and slippage need their own evidence and labels. Snapback hedge P&L must remain separately attributable before aggregation.

## 9. Remaining real-session acceptance

Required before claiming complete production certification: the fixed selected release, authenticated current backend diagnostics, a live-data session for each **supported** mode, broker acknowledgements and fill/postback reconciliation, actual protective-stop/child semantics, populated exports, device delivery receipts and responsive-browser review. Unsupported Gamma/Price Rules combinations stay outside the certification claim. These outstanding items are not waived by the funds/IP exclusions.
