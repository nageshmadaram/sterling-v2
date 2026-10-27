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
