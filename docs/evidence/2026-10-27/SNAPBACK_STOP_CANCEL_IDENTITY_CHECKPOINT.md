# Snapback — triggered stop cancellation identity gate

Date: 2026-10-27. Scope: development branch only, **NOT** live or profitability certification.

## Defect repaired

The old `_stop_fill_pending` verification relied on a count of broker order-book
rows after cancelling a triggered protective stop. Two rows with the same
`order_id` could satisfy a count of two expected child IDs while the second
child remained absent/working. That would allow a replacement market exit
without proof that every original stop SELL could no longer execute.

The repaired path uses `verified_terminal_stop_children` to require **one
terminal order-book record per exact expected child order ID**. Missing, duplicate,
active, invalid and unreadable evidence prevents another market SELL. It also
refuses cancellation when the *original* stop child IDs are missing or malformed.

A related path previously permitted a triggered stop with an **unidentified**
child order to be booked as an unpriced option exit after broker-zero
observations. The unknown child could still be resting. The new path instead
retains broker-stop identity, moves to reconciliation and halts new entries.
Duplicate child IDs in the broker's source order book are treated as unknown,
not evidence of a uniquely attributable stop fill.

## Changes

- `backend/app/services/snapback_stop_cancel_verification.py`: pure
  expected-child-ID/terminal-state verifier, no network or broker side effects.
- `backend/app/services/snapback_live_exits.py`: refuse unsafe pre-cancel
  IDs; check every child post-cancel; reconcile unidentified triggered children.
- `backend/tests/unit/test_snapback_stop_cancel_verification.py` and
  `backend/tests/unit/test_snapback_exit_resolution_boundary.py`: regression
  cases for duplicates, missing children, active orders, bad IDs, ambiguity
  and broker-zero with unknown stop child.
- `scripts/snapback_offline_acceptance.sh`: include stop-cancellation unit tests.

## Executed evidence

The pure verifier was reconstructed byte-for-byte from its committed GitHub
source and checked using `git hash-object`:
`e01cd67c3fad1c6c77017b13a658852e17777c0d`.

Python compilation passed. **24 of 24 isolated behavioral tests passed** in
the available sandbox: invalid/missing/duplicate rows, nonterminal children,
bad expected IDs, valid terminal records with foreign orders ignored, and
invalid input types. These are standalone tests against the verifier module;
**the full Sterling integration tests and broker tests have not run**.

## Release disposition

**NO-GO for real-money entry enablement.** No real broker order was placed,
cancelled or retrieved; no running host was accessed. No independent
authoritative forward, after-cost profitability evidence was produced. Main
and existing entry safety gates must remain unchanged pending broker identity,
operational recovery and independent economic proof.
