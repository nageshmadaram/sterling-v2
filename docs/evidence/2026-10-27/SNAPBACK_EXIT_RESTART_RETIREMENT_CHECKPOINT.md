# Snapback restart recovery: filled exit ACKs, cancellation counter and final closure

**Date:** 2026-10-27. **Branch:** dev. **Release verdict:** NO-GO for new live entries.

## Defects corrected

1. **A fully booked exit could be skipped on restart.** A crash between the SQLite
   terminal fill projection and durable broker ACK/journal-projection retirement
   can leave `option_exit_qty == option_qty` (or hedge equivalent) **and** a still
   stored `option_exit_order_id`. Previously `_run_exit` skipped the leg when
   its local quantity was flat and could advance to `CLOSED` without resolving
   the persisted order identity. Now any outstanding option or hedge order
   always enters `_resolve_exit` first, including when local quantity is full.
2. **Leg resolution could reject a legitimate recovery.** The preflight
   previously required `requested <= total - already_exited`, which is false
   after the same order's receipt is already durably projected. The source now
   accepts this exact already-booked broker order ID for idempotent ACK recovery,
   without authorizing a new order or new projection. A new unknown order with
   an inconsistent quantity still fails closed.
3. **A terminal zero-fill order could increment its attempt twice.** Earlier
   code incremented `exit_attempts` while projecting an empty cancelled/rejected
   receipt; a crash before ACK retirement would project the empty receipt again.
   The projection function is now side-effect-free for zero fills; the attempt
   increments **once inside the same transaction that retires the order ID**.
4. **Final closure could erase a newly working order.** `_finalise` previously
   cleared both exit IDs without verifying the freshly mutated DB row still
   matched the broker-flat snapshot. It now refuses closure if a different
   account, contract, fill/value, incomplete hedge, remaining broker ACK or
   position state appears before its transaction, entering reconciliation
   instead. It also blocks a disappeared row.
5. When a fired broker stop and an unresolved market option exit coexist at
   restart, the routine now requires reconciliation rather than assuming
   either order is safe to resend.

## Tests and exact-source evidence

- Updated `backend/tests/unit/test_snapback_exit_fill_projection.py` to require
  that repeated zero-fill projection leaves quantity and attempt count intact.
- Added mocked application boundary cases for fully projected option+hedge
  recovery, blocked finalisation while projection pending, idempotent full-fill
  retirement, one-time cancellation retirement following a simulated disk
  failure, triggered-stop/market-order overlap, and stale final-close snapshot.
- **Executed:** Python 3.13 standalone suite with **30 passed, 0 failed**
  against an exact reconstructed copy of the committed
  `backend/app/services/snapback_exit_fill_projection.py` Git blob
  `61800fe266c0cbe875b14ca105a46bb37b233aa7`, verified using
  `git hash-object`. The 30-case local harness checks real pure-module
  behavior for option and hedge exit ownership, incomplete receipt conditions,
  idempotence, excess quantities and zero-fill replay.
- **Not executed:** the repository's full integration/boundary suites (including
  the new mocked manager tests), an authenticated broker session, actual order
  and stop management, host deployment, or independent positive forward returns.
  The local isolated harness was reconstructed for this verification, not the
  repository's entire test environment.

## Operational disposition

There is **NO** live functionality certificate and **NO** verified after-cost
profitability. Do not enable new real-money entries, merge dev into main, weaken
the frozen economic gate or claim these isolated unit tests prove broker order
behavior. The app's operator is hospitalized and need not copy/run commands
for this checkpoint.
