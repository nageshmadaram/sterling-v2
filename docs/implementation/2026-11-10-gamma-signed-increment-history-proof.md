# Gamma terminal partial: complete signed-execution provenance (offline review)

**Base:** Sterling V2 draft PR #42, immutable commit
`dd06ff8f43869ba34121f2f8e22e575148c6d94c`.

**Decision:** REVIEW ONLY. No merge, deployment, broker API access, GTT changes,
real order reservation, or live execution. Independent approval remains 0/12.

## Reproduced gaps

The existing passive Gamma lineage audit checked only the final cumulative
quantity/value of a terminal parent SELL and that its final increment's
`signed_quantity` was negative. It could therefore report a reviewable
20/50 terminal partial exit while a stored increment claimed `signed=-1`
for a 20-contract fill, or while the cumulative value was 1760 but the
increment price was 1 instead of 88. Both negative cases reproduced a
**false-positive read-only result** before the implementation.

For multi-increment fills, previous checks also ignored whether every
cumulative quantity and cumulative notional increased strictly, whether
`signed_quantity == -(current_cumulative - previous_cumulative)`, and whether
incremental price matched the value/quantity difference. The final cumulative
20/1760 alone cannot prove that earlier fills were correctly represented.

A related replay gap was demonstrated with two coherent cumulative fills:
10/900, then 20/1760. After the original review hold was recorded, rewriting
the first fill to 10/910 and adjusting both incremental prices to 91 and 85
kept the *final* 20/1760, account inventory version, and parent journal
projection version unchanged. The original hold incorrectly returned
`review_hold_replayed` against this different historical evidence.

## Implementation

- A pure `valid_parent_sell_increments` helper now verifies all matching
  execution records: exact account/user/contract/order identity, SELL side,
  permitted provenance source, strictly increasing cumulative quantities and
  values, negative signed quantity equal to each increment, finite positive
  derived price, and final cumulative evidence matching the canonical parent.
- Both the read-only `inspect_terminal_partial_exit` and the atomic, disposable
  SQLite `hold_terminal_child_review` use the same validation; the latter
  rechecks inside its existing `BEGIN IMMEDIATE` writer transaction after
  initial preflight. The source-only audit and transactional review can never
  authorize execution.
- The disposable review table now captures `parent_increment_digest`: a
  deterministic SHA-256 digest of the *complete ordered signed execution
  sequence*, with immutable row identities, cumulative values, signed deltas,
  price, order ID and source. A replay compares the digest alongside previously
  pinned inventory and parent journal revisions. Any changed sequence fails
  closed with `existing_review_claim_evidence_changed` and leaves the original
  review hold immutable. This is an integrity comparison, **not** a digital
  signature, broker authentication or order-admission token.
- Older disposable schemas without the digest are rejected rather than
  implicitly upgraded. This table is never migrated into production.
- CI source triggers include the new read-only helper, and existing Gamma
  recovery tests continue to run under `STERLING_LIVE_EXECUTION=0`.

## Negative and positive evidence

- Originally failing tests for inconsistent signed quantity and price.
- A valid terminal 20/50 partial with two 10-contract increments remains
  reviewable (`execution_authorized=False`).
- Tampered first incremental quantity, price and value all fail closed even
  when the last cumulative row still matches the parent.
- A ledger inconsistency inserted after initial review but before the locked
  SQL transaction is detected; no review claim is inserted.
- Source-consistent historical reprice of two increments cannot silently
  replay the original immutable review hold.
- Existing canonical journal UNKNOWN, GTT, restart, concurrent review,
  independent process rollback and all-user account isolation tests remain
  within the offline suite.

## Still unresolved

The second SELL for 30 after a confirmed 20/50 partial exit **cannot yet be
executed safely**. A production-grade canonical child reservation and account
fence, authenticated fresh broker quantities, protective order reconciliation,
UNKNOWN/no-resend and crash tests, parent PR #15 full CI and independent
12-control acceptance are required. Offline ledger proof is necessary but
insufficient. Release remains **NO-GO**.
