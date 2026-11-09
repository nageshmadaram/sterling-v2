# Gamma canonical exit: pre-disarm identity refusal

**Review only.** Parent stack: PR #15 → PR #32 → this proposed increment.
No production approval, merge or broker transaction is implied.

## Safety problem

`GammaMoveRunner._execute_exit_locked()` previously proved positive broker and
canonical inventory, then cancelled a protective GTT **before** discovering
that a prior canonical exit for the same immutable signal identity was already
reserved. After a confirmed partial fill, the next remainder order reuses the
same `signal_id:reduce:exit` identity but has a different quantity. The
transactional journal correctly raises `immutable_intent_conflict`; however,
protection has already been unnecessarily disturbed before that rejection.

## Implemented defense in depth

`order_journal.identity_exists()` performs a read-only, exact-identity lookup
using the same account/user/strategy/signal/exchange/symbol/side columns as
`reserve()`. It recognizes an existing identity independently of generation
changes, including legacy keys. The query never inserts, claims, submits,
updates or projects an intent. An unreadable database raises, not false.

Before Gamma's protective stop may be cancelled, the runner now:

1. Validates attributed broker-held quantity, scoped canonical inventory and
   immutable account/signal identity as before.
2. Refuses any existing prior canonical exit identity and **leaves the
   existing stop intact**. It does not treat a prior `REJECTED`/`CANCELLED`
   intent as permission to reuse a signal; resolution requires new reviewed
   lifecycle design.
3. Refuses an unreadable journal using a distinct `canonical_exit_identity_unverified`
   result, leaving protection and broker orders untouched.
4. Retains the canonical journal's atomic reserve/claim and two fresh broker
   inventory reads after this new preliminary check. The preliminary lookup
   is **not** transactional with GTT cancellation; a concurrent reserve
   between preflight and send must still be rejected by the final journal
   gate. No guarantee of complete atomic protection transition is claimed.

## Focused synthetic negative evidence

- Prior immutable intent with a different requested quantity: protective GTT
  not cancelled, no broker order, and the prior journal row stays reserved.
- Unreadable journal: no protection cancellation and no broker order.
- Account/user/strategy/signal/exchange/symbol/side identity variations:
  no accidental cross-scope matches; lookup is nonmutating.
- Zero canonical inventory, prior UNKNOWN/no-resend, and malformed broker
  quantity regressions remain in the composed suites.

The scope changes no entry permission, broker adapter, broker order code,
account exposure state, journal transition or UI. It remains **NO-GO**.

## Remaining P0 dependencies

1. Design a new *distinct durable tranche identity* for confirmed terminal
   partial exits; project broker-trade evidence to canonical inventory before
   a subsequent reduction. Never create it during an UNKNOWN or unresolved
   original order, and never weaken `immutable_intent_conflict`.
2. Reconcile cross-strategy shared inventory mismatches and restore the
   positive daily-loss-budget regression under separately witnessed broker
   evidence; do not fabricate ledger records from a strategy position.
3. Prove restart, concurrent executor, broker stale-read, unknown ack and
   protective stop lifecycle with full CI and independent operational review.
4. Full parent PR #15 CI and 12 external safety controls are still blocked;
   draft work alone is not production or funded-trading authority.

## Exact-commit CI regression found and corrected

The first GitHub run for `57eff107` executed the Gamma synthetic workflow and
failed **one** of 299 tests:
`test_synchronised_broker_stop_is_not_rewritten_every_scheduler_pass`.
On a newly started Actions runner, `time.monotonic()` was still lower than the
protection recheck interval. The old expression treated a missing per-position
check timestamp as zero, wrongly suppressing the first broker-stop check.
The fix distinguishes **missing** from a real recorded timestamp and preserves
normal repeat-check throttling. A deterministic fresh-process test mocks a
positive 1-second uptime and proves the first check runs while the immediate
second check is throttled. This is a production-safety correctness fix, not a
skip or weakening of the failing test.

The first GitHub run is **FAILED** and never constitutes acceptance. Exact-head
CI must be re-run after this correction; a local green test is not enough.
