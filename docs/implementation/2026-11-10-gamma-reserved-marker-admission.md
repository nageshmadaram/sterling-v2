# Gamma offline review marker: public journal admission boundary

**Parent:** draft PR #48, exact commit `fea38df9226c0377f9f380d9dd7dee6a8c68a1ca`.
**Purpose:** preserve non-executable review evidence without granting ordinary journal callers a way to mint it.
**Release:** review-only, unmerged, undeployed; **NO-GO** live broker execution, **0/12** independent release controls accepted.

## Reproduced defects

The offline Gamma terminal-partial helper is the only intended creator of canonical
`gamma_terminal_review_only` child intents. It verifies the disposable SQLite test DB,
non-live environment, durable review hold, exact parent signed fill history, inventory
version and competing exposure before inserting directly under a SQLite writer lock.

However, the generic `order_journal.reserve` accepted an arbitrary payload containing
`gamma_terminal_review_only`, bypassing every such review precondition. A caller could
produce a permanent non-submittable, non-resettable intent without evidence of a real
offline review child. In addition, `claim_submission` and the mutation guard treated
only literal JSON boolean `true` as protected: persisted malformed markers such as
`false`, `0`, `null` or string `"true"` remained executable.

Ten red-first cases reproduced these precise source-level behaviors on the parent:
six ordinary reservations with reserved marker variants were admitted; four malformed
legacy records could claim a submission. This is an internal API safety finding, not
evidence of a public HTTP exploit or an actual broker order.

## Changes

- The generic `order_journal.reserve` refuses any payload containing the reserved
  marker **before** searching existing records or inserting a new intent. This prevents
  both minting and replay through an insufficiently gated path.
- All already-persisted intents carrying the marker, irrespective of its value, are
  rejected by journal state mutation and `claim_submission`. Cleanup refuses them too.
- The offline helper retains its exact parent/child evidence checks and replay; it
  remains the sole permitted writer of the review-only child.
- Existing simulation reset tests now inject an explicitly synthetic *stored* marker
  with a test-only fixture, instead of incorrectly relying on the generic journal API.
  Normal simulation reset remains functional and is separately tested.
- Replaced the former generic-reserve replay expectation with two assertions: generic
  replay must fail; the strictly offline helper must replay the same immutable child.

## Evidence

- Red-first: 10/10 new negative cases failed against the exact PR #48 baseline.
- Green focused: 11/11 marker/replay tests passed after correction.
- Local offline Gamma/canonical/journal suite: **494 passed**.
- Local simulation mode/API/boot: **103 passed, 5 warnings** (deprecations/route warning).
- Governance checks and exact-head remote workflow outcomes must be recorded separately;
  passing local tests is not a release certification.

## Remaining blockers

This does not implement the executable production partial-remainder SELL, genuine fresh
Kite account and protective GTT proofs, atomic stop handoff, UNKNOWN no-resend across
external broker uncertainty, durable cross-database reset fencing, full parent PR #15
regression, real-device acceptance, host restore drill, or external review approvals.
No live broker credentials, orders, GTTs, production DB, release merge or deployment.
