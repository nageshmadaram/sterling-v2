# Gamma terminal-partial review: replay must preserve proof revisions

**Base:** Sterling V2 draft PR #39 at
`ffae663014886a1c5687eeb10f679b9e7d10dcc8` (which includes draft PR #38,
`4a812400a219f426c01dfb18fdc236e030ffda19`). The PR #39 broker-snapshot
changes are retained unchanged; this increment touches only the offline
review-claim provenance path and its tests.

**Status:** OFFLINE REVIEW ONLY, NO-GO for broker orders or release.

## Confirmed regression

A durable `gamma_terminal_review_holds` row originally stored the canonical
inventory replay version, but the idempotent replay path compared only account,
contract, parent broker ID and remaining quantity. This allowed
`review_hold_replayed` after a newer inventory projection replaced the old
version **without changing its net quantity**. A second reproduced failure
occurred after the parent exit's projection version advanced while its
`CANCELLED` 20/50 fill and order identity remained unchanged. Neither
scenario proves the held review evidence is still current.

Both negative tests failed against the previous commit with the unexpected
`review_hold_replayed` result. A later source-consistent price/value
correction of the parent signed SELL was added as a third regression.

## Implementation

The disposable-test-only review table now captures all three independent
parts of the durable review provenance:

- `inventory_version`: the exact accepted canonical account/symbol inventory
  projection version already stored in the previous schema.
- `parent_projection_version`: the exact accepted parent canonical order
  projection version, newly stored with `CHECK > 0`.
- `parent_observed_value`: the verified parent cumulative SELL value from
  signed fill-ledger evidence, newly stored with `CHECK > 0`.

The replay branch compares each recorded value with the **current values in
the same `BEGIN IMMEDIATE` write transaction** after rechecking the parent
state, all-user unresolved orders and exact signed inventory. A mismatch
returns `existing_review_claim_evidence_changed` and
`held_for_review=False`; it never silently renews a stale proof or invents a
new child intent. The original review row remains immutable for independent
inspection. Identical evidence after a process restart still returns the same
idempotent replay, and concurrent callers still create only one row.

The new fields apply only to new disposable test databases. No automatic
migration or production database schema change is made. An older test DB
missing these columns must fail closed rather than being upgraded without
review.

## Verification scope

Regression tests exercise same-quantity inventory re-projection, parent
projection version advancement, and a mutually consistent signed fill/journal
value correction. The source-only review helper still has no Kite calls,
canonical intent reservation, strategy integration, GTT mutation, HTTP route,
execution authority or live approval. Its response always includes
`execution_authorized=False`.

An exact-head GitHub Actions run must validate the full offline Gamma and
canonical test suite before accepting the draft changes. A passing suite is
not a broker authorization or independent production review.

## Still open

The real 30-contract remainder after a confirmed terminal 20/50 SELL remains
**blocked**. Future production work needs a separately approved atomic
canonical parent-child order-intent schema, fresh authenticated broker/GTT
state under account-wide fencing, durable no-resend UNKNOWN recovery, correct
signed inventory and P&L projection, parent PR #15 complete acceptance and
all 12 independent controls. No review record may authorize a live SELL.

**Release decision: DRAFT / UNMERGED / UNDEPLOYED / LIVE BROKER NO-GO.**
