# Gamma review-only journal evidence: simulation cleanup boundary

Base: draft PR #46, immutable review head
1448b61885305cfe7059e5b2643e3b85360385d0.
Scope: order-journal cleanup only. No broker order capability, strategy
runner, GTT control, production migration or deployment is introduced.

## Reproduced defect

The Gamma terminal-partial child is a test-only canonical intent carrying
gamma_terminal_review_only=true. The normal journal mutation functions
reject it, but order_journal.clear_for_tests(uid) still deleted the child's
intent and associated observations. This function is also used by the
simulation mode reset path, so its name is not itself an effective guard.

Deleting a review child while retaining the separate
gamma_terminal_review_holds checkpoint destroys immutable audit evidence
and prevents trustworthy replay. The original negative test demonstrated
that the cleanup completed without an exception.

## Remediation and test evidence

Under the journal's existing durable BEGIN IMMEDIATE write transaction,
cleanup first checks each owned intent with the same explicit
review-only immutability predicate. If any review child is present it fails
closed with gamma_offline_review_intent_immutable, before deleting any
journal observation, fill or intent. Malformed/corrupt source rows also fail
closed. No standalone execution permission or process-level bypass exists.

Two focused tests verify:

1. A tenant with a review-only child cannot delete its journal history,
   signed fill evidence, review hold or child; no submission is authorized.
2. An unrelated ordinary simulation tenant can still clear its own
   canonical journal rows without touching the protected review child.

Local validation: 484 backend Gamma/canonical/order-journal tests passed
before the test was strengthened to assert preservation of a real legacy
journal-fill row. The two focused cleanup tests passed again after that
strengthening. Exact-commit GitHub Actions must rerun the complete suite.

This is not a transactional guarantee across all simulation reset
subsystems, which may use additional databases. Full reset orchestration
requires a separate preflight/rollback audit.

## Open blockers

An actual Gamma remainder SELL is not implemented or authorized.
Fresh authenticated Kite inventory, protective GTT status, a production
atomic child reservation, UNKNOWN no-resend proof across restart, rollback
and independent acceptance of the twelve release controls remain required.

Review draft only. No main merge, no deployment, no live broker execution.
