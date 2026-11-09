# Gamma terminal partial — ordered execution history (offline review)

**Base:** draft Sterling V2 PR #43, immutable SHA
`75a5fed7376133f74d829c095251acc7f24739f3`.

**Status:** DRAFT / NO-GO. No broker orders, GTT changes, canonical executable
child-intent reservation, or production migration. All twelve independent
production controls remain unapproved.

## Reproduced false positive

The signed-increment validator was introduced to prove every cumulative fill
rather than just the final quantity/value. However it then sorted the input
rows by `cumulative_quantity` before checking them. This masked an impossible
broker-event chronology: a 20-contract cumulative SELL recorded *before* a
10-contract cumulative SELL would be sorted back into 10, 20 and accepted.

Two new negative tests reproduced the defect on the previous exact commit:

1. A synthetic terminal 20/50 partial exit with valid 10/900, 20/1760 signed
   fills was modified so the 10-contract event's received timestamp came
   **after** the 20-contract event. The existing audit incorrectly returned
   `lineage_evidence_consistent_review_only`.
2. The same chronology inversion after a durable offline review hold let
   `hold_terminal_child_review` return `review_hold_replayed` even though the
   event evidence had been rewritten. The old digest sorted by quantity and
   omitted both event timestamps.

## Changes

- `valid_parent_sell_increments` now checks the event sequence in the order
  supplied by the canonical ledger reader, with strictly increasing cumulative
  quantities and values. It never sorts by the very value it is trying to
  validate.
- The transactional offline review uses the same event ordering as
  `fill_ledger.increments`: exchange timestamp where present, otherwise
  received timestamp, then received timestamp and unique row ID. This is
  re-verified inside the existing SQLite `BEGIN IMMEDIATE` writer transaction.
- `parent_sell_increment_fingerprint` preserves that event ordering and binds
  `exchange_ts_ms` and `received_ts_ms` as well as the previously recorded
  identity, quantity, value, signed delta and incremental price. Rewriting
  arrival chronology now invalidates the old immutable review hold.
- The previous correct two-fill history, crash/rollback, process concurrency,
  account isolation, duplicate-SELL refusal and no-resend UNKNOWN test coverage
  remain in the existing offline suite.

## Boundaries and further risks

These checks assess the integrity of **recorded** signed ledger events. A broker
can provide delayed/out-of-order reports; such disagreement is a reason to
reconcile rather than infer that a new SELL is safe. A positive result is not
broker authentication, an external timestamp attestation, or trading authority.
The review helper still returns `execution_authorized=False` unconditionally,
uses disposable SQLite only, and cannot submit or reserve a live order.

The actual second 30-contract SELL after a 20/50 terminal parent remains
**blocked**. Implementing it requires an independently approved canonical
parent-child atomic reservation, authenticated fresh broker inventory and GTT
status, account-wide safety fencing, restart and UNKNOWN no-resend evidence,
full parent PR #15 CI and independent acceptance of all twelve controls.

**Release: NO-GO / DRAFT / UNMERGED / UNDEPLOYED.**
