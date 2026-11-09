# Gamma terminal-partial: atomic review hold prototype (OFFLINE ONLY)

**Base:** Sterling V2 draft PR #35 (`d58b38014c6fc9989dc2c0bf7c63f85718b5f11d`).

**Decision:** PREPRODUCTION **NO-GO**. Live broker execution remains disabled;
0/12 independent safety controls accepted. This change cannot submit any
broker order or re-arm/disarm any protective trigger. It is NOT an execution
claim or production migration.

## What this increment implements

`gamma_tranche_review_claims.hold_terminal_child_review()` writes only a
separate, offline review artifact into a **disposable test SQLite database**.
It refuses execution outright unless both `db.under_test()` and
`db.is_disposable_path(db._DB_PATH)` hold. There is no HTTP route, strategy
caller, broker adapter, canonical order-intent insertion, or order sender.
Every result—including successful holds and idempotent replay—exposes
`execution_authorized=False`.

After the existing signed-fills lineage proof and deterministic child identity
preview, the implementation uses `PRAGMA synchronous=FULL` and
`BEGIN IMMEDIATE` for a single SQLite writer transaction and re-reads *inside*
that transaction:

1. Immutable parent canonical identity, terminal `CANCELLED` status,
   `0 < filled < requested`, expected order ID and quantities, and projection
   acknowledgement; the original parent remains untouched.
2. All unresolved/UNKNOWN orders, reconciliation-required rows and pending
   projections for **any user** sharing this `account_id/exchange/symbol`.
3. Any nonzero or unreconciled canonical inventory belonging to another user
   for the same broker account and symbol. This also blocks a new holder that
   appears between initial inspection and the transaction.
4. Strict replayed inventory for the target user, matching exchange, exact
   remainder quantity and positive projection version.
5. Signed cumulative fill increments for the exact terminal parent order,
   matching account, user, symbol, exchange, side, quantity and cumulative
   value; synthetic strategy-position state is never treated as ledger truth.
6. Existence of the proposed canonical child order identity, and any existing
   review hold on that account/exchange/symbol.

Only after all rechecks pass does it insert into
`gamma_terminal_review_holds`. A dedicated uniqueness constraint prevents
multiple held review records for the same account/contract, even if different
users race. Each parent can have only one child review row. Review rows are
immutable (`REVIEW_HELD` only) and **never released/retried automatically**.
The persistent artifact is idempotent across repeated calls and DB reopen;
parallel callers yield one new hold and one replay, not two rows.

**Important boundary:** This is a *review-only claim*, not a canonical
`kite_order_intents` reservation or claim of broker permission. The review
schema is created only in a disposable offline database, not migrated into
production. Transactional SQLite evidence cannot serialize external broker
orders or future GTT activation. No code may use the successful review result
as a send authorization. The original `immutable_intent_conflict` and PR #33
pre-disarm refusal remain enforced.

## Verified failure modes covered by synthetic tests

- Original terminal partial (20/50, 30 remaining) with signed fill and 30
  reconciled contracts, positive claim, exact durable row and zero new orders.
- Same candidate after restart, and two concurrent calls: one new claim, one
  replay, exactly one durable review artifact.
- Cross-user unresolved SELL, unprojected terminal order, and another
  user's positive inventory in the same broker account/contract.
- Other user or inventory appearing **after** initial readiness inspection
  but **before** locked transaction, with no claim created.
- Changed parent order identity, changed current quantity, missing signed
  projection, unavailable SQLite and a simulated failed SQL INSERT; complete
  rollback, no false-positive success and no internal details leaked.
- Schema-level account-bucket uniqueness even for a conflicting manual SQL
  insertion.

## Next gate, still unresolved

Actual second-SELL processing must be a *different*, independently approved
feature. Before enabling an executable child order, design a production-grade
parent-child journal schema and account-wide **atomic canonical** intent
reservation (not this review table); independently verify broker inventory
immediately before and during submission; resolve GTT/child order status;
prove UNKNOWN response/no-resend and restart idempotency; validate fill-ledger
P&L and daily-loss accounting, cross-tenant access, concurrency and rollback;
re-run parent PR #15 full CI and witnessed operational acceptance.

**Neither a passing test suite nor a successful review hold authorizes a SELL.**
