# Sterling V2: Gamma terminal-partial canonical child — offline, non-executable prototype

**Parent:** draft PR #44, SHA `1aa5802958c0d7000664332a8ae08060836be3f2`.
**Release:** DRAFT, NO-GO, no broker order submission or protective-stop modification.
**Production controls:** 0/12 independent approvals; no production migration, merge or deployment.

## P0 architecture and the actual remaining gap

When a broker confirms a terminal `CANCELLED` parent SELL with 20 filled from a
requested 50, the parent journal identity is immutable. A second SELL for 30
cannot be sent using that identity. The earlier review stack (`#34`–`#44`)
proves the parent’s signed fill and inventory locally and creates a durable
`REVIEW_HELD` record, but it deliberately does not create a canonical child
order intent or prove fresh broker/GTT state at a future send boundary.

This incremental patch introduces
`gamma_offline_canonical_child.reserve_offline_canonical_terminal_child`, a
**test-only** prototype that can persist a parent-linked canonical
`kite_order_intents` row with state `RESERVED`. It is NOT a production order
reservation, execution API or permission to send. The helper is not imported
or called by the Gamma runner, gateway or any broker path.

Its inputs are exact user/account/strategy/signal/exchange/symbol/product
identities. The function refuses all non-test or non-disposable SQLite paths,
rejects enabled/ambiguous `STERLING_LIVE_EXECUTION`, and first requires an
immutable terminal-partial `REVIEW_HELD` checkpoint. If the process stopped
between checkpoint and child reservation, a later offline retry can complete
the same identity without minting a second child.

Inside one `BEGIN IMMEDIATE`/full-synchronous transaction it rechecks:

- Parent terminal state, exact account/strategy/signal/order identity,
  acknowledged projection, positive fill and 30-contract remainder.
- All-user pending, UNKNOWN, unprojected or reconciliation-required journal
  orders on the same broker account/contract, with a documented exception for
  the exact already-reserved child during idempotent replay.
- Other-user inventory exposure, target inventory quantity and exact immutable
  projection version.
- Complete chronological signed SELL fill sequence and its previously pinned
  immutable review-history digest.
- Legacy and current canonical child identity conflicts. The parent is never
  rewritten, and a previously cancelled review child cannot be recreated.

For a valid synthetic 20/50 terminal partial, the resulting child payload
contains exact `parent_intent_key`, `parent_order_id`, full signed-fill digest,
`gamma_terminal_review_only: true`, and `execution_authorized: false`.

**Crucial fail-closed defense:** The actual canonical
`order_journal.claim_submission` CAS refuses `gamma_terminal_review_only`
intents, including direct/replayed calls. Thus even though this offline row is
`RESERVED`, it is **permanently non-submittable**. Generic canonical re-reserve
with a different payload is also rejected by immutable-intent conflict.
No broker-adapter call, network request, stop cancellation, HTTP endpoint, or
live execution switch is introduced by this prototype.

## Review tests and crash/race evidence

Tests use synthetic symbols and throwaway SQLite. They cover:

- Immutable parent→child lineage; `CANCELLED 20/50` leaves exactly one 30-unit
  child, while direct `claim_submission` returns false.
- Idempotent restarts and concurrent threads and **separate processes**:
  exactly one parent, one child and one `REVIEW_HELD` proof survive.
- Forced subprocess termination inside a SQLite `AFTER INSERT` trigger before
  commit: no ghost child survives, the prior review hold remains durable, and
  restart produces only the same canonical child identity.
- Cross-user pending/UNKNOWN evidence, tampered inventory/fill history,
  cancelled review identity, live-mode process and generic-payload replay all
  fail closed.

This is a staged engineering model of an atomic canonical *offline* claim, not
an approved production send protocol. Two separate transactions exist between
creating the review hold and the canonical child, but crash between them is
recoverable without creating a new identity. External broker state is not
serialized by the local SQLite transaction.

## Separate P0 frontend fix

The existing eight failures in parent PR #15's `GammaMoveBoard` Vitest suite
were reproduced on the current draft source. `StrategyBoardFooter` rendered
Gamma's date and trading-day labels both in the compact actions toolbar and
inside the History modal portal. Because both controls existed simultaneously,
accessible-name queries were ambiguous; it also exposed duplicate UX controls.

The footer now keeps only the Gamma date labels inside the History modal;
Scan/Help actions remain available in the compact toolbar. Other strategies'
footer behavior is unchanged. A direct regression confirms exactly one date
and trading-day input inside the modal and none outside. The full 283-file
frontend suite passed **2555/2555** locally before adding this explicit extra
regression, and the 30-test Gamma board suite passed with it. A new
non-authorizing GitHub Actions workflow runs `npm ci`, the required media CSS
build, complete TypeScript check and full Vitest tests for each relevant PR.

## Release blockers still open

This prototype cannot authorize a second broker SELL. Before any real 30-unit
reduction, independently implement/approve a **production** canonical
parent-child account-wide reservation with journal migration, fresh verified
Kite account holdings and resting GTT/child orders, atomic stop handoff,
UNKNOWN/no-resend/crash/recovery handling, signed inventory/P&L and tenant
isolation. Parent PR #15 also has other backend and cross-browser failures,
backup/restore and independent acceptance requirements. All 12 operational
production gates remain unapproved.

**No change in production authorization: LIVE BROKER EXECUTION NO-GO.**
