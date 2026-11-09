# Gamma terminal-partial child identity: non-authorizing review proposal

**Parent:** Sterling V2 draft PR #34, `3c6995096a4a2e63071fe4814cdb40f95bed2094`.

**Release state:** source-only/offline review. No new order path, journal mutation,
protective-stop cancellation, authentication bypass, merge, or deployment.
Production and live routing remain **NO-GO**. None of the twelve independent
production controls is accepted.

## Reproduced cross-user gap and correction

An earlier source-only readiness report checked pending journal intents only
for the current `uid`. Multiple users can share an authenticated broker
account and the same contract: an unresolved SELL owned by a different user
would not appear in that query, even though it could change shared account
inventory. A negative regression reproduced the false positive.

`order_journal.account_bucket_has_unresolved` now returns only a Boolean for
**all** users of the given `account_id/exchange/symbol` under one durable DB
read. It includes unresolved states, `reconciliation_required`, and
`projection_pending` even if the order is terminal. Invalid account/contract
identity and unreadable storage raise; callers fail closed. It never reveals
other users' identities, modifies journal rows, or treats the result as an
atomic broker admission gate.

## Stable identity proposal (not a durable reservation)

`preview_terminal_child_identity` returns a read-only candidate for a future
terminal-partial continuation. It must first pass the parent PR #34 lineage
check, including the account-wide pending-intent test. The candidate's
SHA-256 domain-separated input binds:

- Immutable original user/account/strategy/signal/exchange/symbol/product
- Parent canonical journal key and acknowledged broker order identifier
- Original requested quantity, terminal cumulative SELL quantity and remaining
  positive quantity

It explicitly **excludes** wall-clock time, strategy scheduler generation,
reason text and inventory replay version. A restart, repeat review, harmless
ledger re-projection or changed operator reason cannot mint a different child
identity and thereby evade no-resend controls. The digest is used only to
construct `child_signal_id` and the *proposed* canonical `intent_key`, matching
the canonical `:reduce:exit` suffix. The function checks the exact identity
against existing journal rows and refuses reuse, **including previously
cancelled, rejected, or zero-fill rows**.

Both review results explicitly return `execution_authorized=False` for every
possible outcome. They do not reserve an order or call the broker. The
candidate is not durable until a future separately reviewed atomic
parent-child journal reservation exists; no such transaction is added here.

## Offline regression evidence

Disposable SQLite tests cover:

- Another user with a pending SELL on the same broker account/contract;
  incorrect owner-local readiness rejected
- Other user's terminal but `projection_pending=1` order remains a blocker
- Missing/UNKNOWN parent, unmatched signed quantity and cumulative value,
  unreconciled inventory and unavailable journal all refuse review
- Repeated/after-module-reload deterministic candidate key; no journal writes
- Same candidate refused after reserving or cancelling the child identity
- Concurrent pending intent observed between first audit and preview recheck
- Wrong account, strategy, symbol, product and cross-scope false positives

This evidence is *local and synthetic* until exact-head CI succeeds.

## Still missing before ANY real second SELL

1. Source-of-truth broker account and contract positions read under verified
   authentication, immediately before and at the eventual send, including
   possible resting GTT child, rejected, partial and UNKNOWN orders.
2. A reviewed database schema and **atomic** parent-child tranche reservation,
   with an account/contract-wide competing-intent exclusion protocol.
3. Crash/restart/execution race testing, immutable identity enforced by the
   journal, no broker resend on UNKNOWN, durable acknowledgements, fill-ledger
   P&L/basis correctness and reconciled protective stop transition.
4. Full parent PR #15 CI, staging/restore evidence and all independent
   approvals. Even a review-only green test does not constitute authorization.

The existing Gamma runner still **refuses** repeated exits after a partial
fill and leaves protection intact; this review change does not remove that
P0 functional blocker.
