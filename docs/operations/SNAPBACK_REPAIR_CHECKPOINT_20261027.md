# Snapback repair checkpoint — 27 October 2026

**Worktree target:** draft PR #272 (`codex/implementation-spec-20261026`), not `main` or `dev`.  
**Trading posture:** NO new real-money orders, and NO assertion of certified profitability.

## Fault addressed

The 22 October option-candle research artifact reported three per-contract
`object of type 'int' has no len()` exceptions in both ultra-scalping and
scalp modes (RELIANCE, HDFCBANK and SBIN put contracts). The underlying
`replay_intraday` contract reports `rejected_plans` as an integer count;
an old aggregation treated it as a collection.

The modified runner now calls `app.engines.snapback.research_reporting`
instead of inlining the unsafe length calculation. That module accepts
the current nonnegative integer count (and older list-shaped values), rejects
malformed counts, and validates all other replay metrics before reporting.

Additional corrections in `backend/scripts/backtest_snapback_timeframes.py`:
- Preserve raw duplicates in minute-tape inputs until full paired-session
  coverage has audited them; do not silently deduplicate bad evidence.
- Reuse identical ultra/scalp market-data reads to reduce broker historical
  API requests, without raising request concurrency or rate limits.
- Count **every requested underlying and CE/PE contract**. Missing historical
  tokens, quotes, expiry data and contract metadata become explicit SKIPPED rows,
  not invisible denominators.
- Parse actual ISO exchange expiry dates; `None`, nonsense and expired options
  cannot become the next expiry via lexical string comparison.
- Refuse fractional token/lot-size values and choose the same nearest valid
  strike regardless of instrument-dump ordering.
- Mark open positions/unresolved replays as incomplete; exclude their realized
  P&L from complete-contract totals while retaining their per-contract row.
- Report contract errors, skipped contracts, unresolved positions and incomplete
  paired-session coverage independently. Display totals explicitly as
  **observed-candle simulation**, never real broker fill profits.
- A new report uses exclusive creation; a rerun cannot silently overwrite a
  dated research artifact.

## Verification truth

- The standalone `research_reporting.py` and
  `test_research_reporting.py` source/test blobs were copied byte-for-byte
  into an isolated offline Python test environment, with Git blob hashes
  checked against the branch. **24 pure-reporting unit test cases passed** there.
- The user previously ran **108/108 focused tests** on the earlier source
  commit `c83f44b0` (not the current Snapback commit).
- These isolated tests **do not** constitute an executed end-to-end historical
  Kite replay, full backend CI, browser test or live broker lifecycle test.
- The original October 22 JSON artifact has **not** been rewritten, because
  its missing contract outcomes cannot be reconstructed without rerunning the
  historical data. Positive old totals remain non-promotable until full
  coverage, sample selection and costs can be independently reproduced.
- GitHub Actions remained blocked before runner assignment on the last
  observed PR attempts (zero assigned runners/steps). Check each new head
  separately when the Actions account is repaired.

## Still required for a functioning, certified release

1. Rerun the script in an authorized read-only Kite environment, with supported
   historical data and adequate disk space; inspect all per-contract statuses
   and verify the former exceptions do not recur. It must not submit orders.
2. Run the new and existing Snapback engine, dispatcher, hedge/protection,
   journal-idempotency, exit, fault-injection and restart recovery tests
   on the exact release SHA; do not conflate tested unit code with a tested host.
3. Obtain dated, exchange-correct option instrument masters and complete
   historical/forward option quote tapes. Retrospective current-contract
   selection and modelled swing prices are not promotion-eligible.
4. Freeze hypotheses before OOS tests; apply all fees, spreads, latency,
   slippage, missed-fill/limit/stop stress; require independent positive after-
   cost bounds and meaningful forward observation. Positive small-sample
   replays are not a profitability guarantee.
5. Obtain real Zerodha static-egress, account identity, durable backup/restore,
   order-fill-protection and reconciliation evidence, and separate owner
   authorization before any tiny controlled real-money release.

**Safety invariant:** master entry halt stays OFF for new risk while all
monitoring and risk-reducing owned-position maintenance remain available.


## Second pass — intraday data and live fill authority

Additional source changes (still unmerged and unapproved):

- Hourly bars are not valid if they would close after 15:30 IST, even
  when their opening timestamp is during the session. Both
  `core.research_coverage.paired_session_coverage` and the independent
  `snapback.intraday._validate` reject this case. New tests cover the
  15:15 hourly truncation as well as intact full-interval bars.
- Direct intraday replay with an empty underlying or option tape fails
  validation; it cannot return a superficially `complete=True` zero-signal
  result.
- Dated research reports are serialized completely to an exclusive
  temporary file, fsynced, and atomically hard-linked to a final path
  without overwriting existing evidence. Disk-full/interrupted attempts
  cannot leave partial final JSON. Three added reporting tests cover
  no-overwrite, malformed JSON, and simulated disk-write failure.
- `SnapbackExecutionAdapter.submit_order` now **returns UNKNOWN rather
  than FILLED** when a confirmed real broker fill cannot be booked into
  both the ledger and the Snapback position registry. It retains known
  filled quantity and order ID, engages the entry kill switch, and requires
  reconciliation; the executor can still attempt protection of confirmed
  exposure without resending the entry.
- If post-fill fee authority refresh fails, the adapter also returns
  UNKNOWN with known filled quantity/order ID and halts new entries.
- A reconciliation-required or unsettled fill cannot have its canonical
  projection prematurely acknowledged. Dedicated mock-broker tests
  cover these cases, plus the normal booked-fill path.

**Verification:** Pure reporting module and test file were copied
byte-for-byte from the latest GitHub blobs into an isolated Python test
environment; their Git hashes matched the GitHub blobs, and 24/24
unit cases passed. The new intraday engine, shared coverage and
`SnapbackExecutionAdapter` integration tests have been added but **not
executed** on a real Sterling Python environment. Do not mark them passed
from this document. No real fills, trading orders, Kite sessions, deployment,
full restore or statistically defensible forward-profit evidence was observed.

**Current decision:** release R1/R2/R3 **BLOCKED**; real-money new entries
remain OFF pending executed exact-head regressions and independent sign-off.


## Third pass — broker post-submit evidence integrity

Further changes on the draft review branch:

- Post-submission broker receipts with missing, negative, fractional, Boolean,
  over-ordered or otherwise impossible filled quantities; nonfinite, missing,
  zero or negative prices for a positive fill; and missing resolver objects
  now return `UNKNOWN` with the broker order ID and any positively known
  quantity retained. New entries are halted; no contradictory fill is booked,
  no journal projection is acknowledged, and no entry is blindly retried.
- A settled fill now requires a genuine broker-terminal order state, not
  simply `filled_quantity == expected_quantity` while status remains OPEN.
  Quantity-known but lifecycle-unresolved exposure remains eligible for
  **protection and reconciliation**, not for a success claim.
- Valid terminal partial fills can be reported as FILLED_PARTIAL only when
  canonical projection evidence is coherent and durably acknowledged.
- The canonical projection version must be an actual positive integer and
  `mark_projected` must explicitly return True. False/stale versions,
  exceptions and absent versions leave the response UNKNOWN, halt new entries
  and preserve broker order identity.
- An unsettled or reconciliation-conflicted order always engages the
  Snapback entry kill switch at the adapter boundary, independently of
  what the lower-level resolver did.
- A zero-fill order is terminal only with a settled CANCELLED or REJECTED
  broker outcome; inconsistent statuses cannot be silently converted into a
  rejection, which might allow an unsafe follow-up entry.
- Regression tests cover all these cases using mocked canonical submissions
  and broker fill resolutions.

**Verification restriction:** These broker-adapter regressions were committed
but are **not marked executed**: current hosted GitHub Actions jobs are failing
before a runner executes steps. Previously verified 108 focused tests and
24 isolated pure-reporting tests refer to older, different source scopes.
Nothing has been merged or submitted to the broker; technical/live and
economic certifications remain blocked.


## Fourth pass — account binding and multi-process position integrity

Additional **unmerged** draft-branch changes:
- Snapback fill registry writes now bind `snapback:<plan_id>` to the
  persisted plan's exact `owner_uid`, `account_id`, option/future
  symbol, exchange and order direction. A plan tag alone is not
  ownership authority. Unknown, unbound, cross-tenant or cross-contract
  evidence is not attached to another owned position.
- Incoming cumulative broker fills cannot reduce the quantity stored
  for either the option or the futures hedge, or replace an existing
  distinct broker order ID. Mismatches return UNKNOWN through the
  adapter's dual-booking failure handler; new entries halt while an
  authorized operator reconciles the broker, ledger and positions.
- These ownership and monotonic checks are repeated *inside*
  `LivePositionStore.mutate`'s `BEGIN IMMEDIATE` SQLite transaction,
  not merely checked by a pre-transaction `get`.
- Initial option fill registration uses exclusive `insert_new` rather
  than `put`'s `ON CONFLICT DO UPDATE`; a concurrent different-owner
  position with the same plan ID cannot be silently overwritten.
- After canonical submission, malformed reference prices leave a
  discoverable UNKNOWN broker order rather than causing an uncaught
  exception. Where the broker returns a confirmed valid quantity/price,
  registry booking can proceed for protective management; the
  reference anomaly still prevents projection acknowledgment and
  forces reconciliation.
- Added regressions for wrong user/account, wrong symbol/exchange/side,
  stale/changed order IDs, futures-leg quantities, races between
  `get` and `mutate`, and same-plan SQLite insert collision.

**Certification remains BLOCKED.** No GitHub-hosted runner executed
the newly added tests on the current SHA. No authorized real-money
order/fill/stop/hedge/exit proof or statistically positive after-cost
forward results were obtained. The owner is medically unavailable to
provide account/broker approval. Keep master new-entry halt OFF.


## Fifth pass — ledger quarantine before position mutation

Additional defensive changes on the **draft** branch:

- Snapback validates a confirmed fill against the persisted plan
  **before writing** to the shared signed fill ledger. Cross-account/user,
  wrong exchange or option contract, invalid side, missing plan, stale
  cumulative fill and changed broker order ID all refuse ledger mutation.
  Existing receipt ownership and risk-stop checks remain in place.
- Late option/hedge entry messages cannot silently reopen a position in
  `CLOSED`, `EXITING` or unknown lifecycle states. Entry evidence from
  a `RECONCILING` position remains admissible for broker-recovery work.
  Check both before ledger write and inside SQLite's `BEGIN IMMEDIATE`
  position mutation.
- The stored position's option contract and exchange must match the
  persisted source plan, not just the incoming broker fact.
- If the signed fill ledger **quarantines contradictory cumulative
  evidence**, Snapback now refuses to overwrite the owned position with
  that contradictory quantity or price. Unknown broker outcomes remain
  unresolved and new entries halt.
- A ledger write I/O failure (as distinct from quarantined conflicting
  evidence) still attempts to persist already-confirmed owned broker
  exposure in the position registry to support broker-stop/exit recovery,
  and then halts new entries.
- Added regressions for foreign pre-ledger messages, valid broker
  exchange timestamps, closed/exiting-state postbacks, transactional
  lifecycle races, registry option-contract drift, conflict quarantine,
  and ledger disk-failure recovery behavior.

**Current verification:** The new integration tests exist in GitHub
but were not executed against the exact head. GitHub-hosted workflows
still report zero assigned runner/zero executed steps. Do not infer
production functionality or profitable forward results from these
code-only changes. No real-money orders, live safety gate enablement,
branch merge or trading certification was performed.


## Sixth pass — cumulative recovery and cross-tenant persistence

The broker adapter now regards only a newly `accepted` cumulative
execution increment or an exact `duplicate` as suitable input for
Snapback's position projection. A prior `older_cumulative` observation,
non-quantity outcome or missing/unknown ledger disposition cannot
rewrite the position registry, even if the signed ledger has not
quarantined the entire account. Such outcomes require reconciliation
and halt fresh entries. An exact duplicate may still repair a previously
missing registry after a crash, without resending the broker order.

The durability store's general-purpose `put` upsert now refuses a
same-ID record when the owning `uid`, `account_id` or `option_symbol`
differs from the stored record. This blocks accidental owner or
instrument reassignment through recovery/admin paths that do not use
`insert_new`. Existing-identity status/quantity changes remain
permitted, while every write preserves the same primary position ID.

Additional focused regression cases cover: accepted/duplicate versus
older/unknown ledger disposition; valid exchange timestamps; forbidden
cross-owner/account/contract upserts; and same-owner updates.

**Verification classification:**
- SQLite's `INSERT ... ON CONFLICT DO UPDATE ... WHERE ...` identity
  fence was checked separately against an isolated in-memory SQLite
  database: 5/5 statement-level checks passed (no Sterling imports).
- The actual Snapback adapter/store integration tests introduced in
  this and earlier passes are **not yet executed at the latest SHA**.
- GitHub hosted CI jobs still report `runner_id=0` and zero steps, so
  their failed status cannot be mistaken for test evidence.
- Neither technical/broker certification nor after-cost forward-profit
  certification has been established. Do not merge or enable new live
  entries. Account owner authorization and actual broker/market
  measurements remain mandatory.


## Seventh pass — cost-stress and economic evidence integrity

The recorded October 22 ultra-scalping result of **INR 19,944**
and scalp result of **INR 19,630** are historical simulated
aggregate totals from **two profitable option contracts** (NIFTY and
BANKNIFTY puts). The same research table includes three contract
processing errors per mode and one incomplete-contract skip, so those
positive numbers **do not** demonstrate a repeatable, diversified
after-cost trading edge.

New `research_reporting.incremental_execution_cost_stress` accepts
only verified-for-consistency trade-level simulation records. It
deducts EXTRA hypothetical 0.5, 1.0 and 2.0 option premium
points per traded unit, **on top of** the replay's original estimated
spread/slippage/fees. It records exact unrounded net P&L, the
trade-volume-weighted break-even extra cost, and mode-wide stress
results only when **every completed contract** has consistent
trade-level records. Missing trade history, mismatched P&L totals,
invalid/zero lot quantities and nonfinite P&L explicitly produce
`available=false` rather than invented stressed profits. Regression
tests cover rounding bias, profitable baseline becoming negative
under extra costs, inconsistent receipts and mode-wide refusal on
partial evidence.

**This does not certify profitability.** It does not produce
independent actual bid-ask, impact, order rejection, brokerage,
expiry/assignment, lost quote or forward out-of-sample receipts.
The current historical script chooses a contract from the available
instrument master at run time; that is not an archive of actual
date-effective option-chain selection. Until chronological
date-effective selection, independent forward data, broker cost
receipts and statistically positive after-cost outcomes are verified,
Snapback R3 remains BLOCKED.

Exact-head Sterling test execution and account/broker release proofs
remain pending; no live order was enabled or sent.


## Eighth pass — reproducible trade-level historical evidence

The historical options replay report now includes a deliberately
minimal **simulated closed-trade audit trail** per contract: quantity,
entry/exit time and premium, exit reason, estimated total costs and
realized net P&L. `closed_trade_audit` checks the equality between
gross price movement minus estimated costs and each trade's net P&L;
also verifies chronological exit order, finite prices/costs, positive
quantities, and equality of the summed net P&L with the replay's own
aggregate. It serializes the receipts deterministically with SHA-256.

The hash is for tamper detection and repeatable computation. **It is
not a broker digital signature, compliance approval or actual fill.**
A malformed or mismatched trade-level record refuses the affected
contract instead of inventing profitable receipts. Regression tests
cover reproducibility, invalid time/quantity/prices/costs, inconsistent
totals and missing trade detail.

The new fields are emitted only by a **future read-only historical
replay**; the existing October 22 artifact is not rewritten or
retroactively populated with invented trade data. A mode-wide
additional-cost stress result is withheld entirely when any contract
fails, is skipped, remains unresolved, or lacks full paired-session
coverage. Profitable surviving contracts may still appear as a
diagnostic subset, not an accepted strategy.

The exact-new-head end-to-end execution and forward
cost/performance work are still pending. New real-money entry
authorization remains OFF.


## Ninth pass — live new-entry limit-price discipline

The Snapback live executor formerly fell back from a missing/invalid
LIMIT order type or price to an unbounded MARKET order for the option
or futures hedge. This can be a severe execution-cost risk in thin
derivative contracts, quite apart from any backtest edge.

Production `mode=LIVE` plans, plus any legacy plan submitted using
a real broker transport, now require a finite positive LIMIT price
and `order_type=LIMIT`. Invalid/missing, zero, negative, NaN, infinity
or unsupported order types return `REJECTED_PLAN` **before calling
the canonical order service**; they cannot silently convert into
market buys or sells. New unit tests cover both option and hedge
legs, invalid prices, unsupported types, fresh hedge repricing, and
the legacy-mode bypass.

This correction affects **new exposure-increasing entry/hedge
submission only**, not the independent risk-reducing exit/stop
manager. A production-provided fresh futures hedge repricer must also
supply the current quote. If it fails, the executor refuses the hedge
order rather than silently using a stale plan-era limit. The
already-confirmed option remains under its separate protection and
hedge-recovery path; no new hedge order is sent without fresh price
evidence. Tests cover fresh-quote absence and correct overrides.

A LIMIT order is not a guaranteed fill, and market data
liquidity, limit tick size, price band, charge schedule, broker
account, outbound IP and real order/protection lifecycle still
require on-host verification.

New live orders remain OFF. No broker orders or profitable forward
trades were recorded by this code change; the new tests are committed
but not exact-head executed.
