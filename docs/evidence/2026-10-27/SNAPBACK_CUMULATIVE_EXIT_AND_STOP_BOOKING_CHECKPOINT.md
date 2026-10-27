# Snapback cumulative broker-exit and stop-child accounting — 2026-10-27

**Branch:** dev. **Release status:** NO-GO for new live entries; no profit certificate.

## Correctness defects

A broker order ID is not a cumulative filled quantity. A previous terminal-fill
projection recorded the ID under `*_exit_booked_ids` but did not save the amount
that was actually booked for that order. On reconnect a larger broker cumulative
fill for the same ID could be ignored as a duplicate, leaving Sterling believing
more inventory remained than Kite actually held. A replacement SELL based on
that false remainder can create a naked short.

A broker-triggered stop child in `option_exit_booked_ids` was also treated as
having no remaining working child order, without checking the current order
book status. A previously booked partial SELL can still be resting.

## Changes in this checkpoint

- `snapback_live_positions.LivePosition` now durably stores
  `option_exit_booked_quantities` and `hedge_exit_booked_quantities`
  alongside the existing lists of booked order IDs. JSON hydration remains
  backwards compatible through dataclass defaults.
- `snapback_exit_fill_projection.project_terminal_exit` persists the
  cumulative quantity on first positive terminal projection. Recovery of that
  ID only deduplicates if the new broker cumulative quantity **matches exactly**
  the persisted one. Changed or absent per-order quantities trigger an
  exception/reconciliation, not a new SELL or a guessed delta.
- `snapback_live_exits._stop_child_orders` must look up the previously
  booked GTT child in the broker order book again; already booked does not
  mean terminal. Missing/duplicated order-book rows remain inconclusive.
- `snapback_live_exits._book_stop_fills` stores exact cumulative quantities
  for new broker stop children. It refuses a changed cumulative quantity for
  any previously booked GTT child before writing the fill ledger and checks
  the invariant again inside the position-store transaction.
- Added tests for larger/smaller broker cumulative fills, old rows without
  quantity maps, option and hedge projection, missing/working already-booked
  stop children, valid dedupe, and new GTT quantity persistence.

Older live rows without the new per-order quantities are still **readable**,
but **ambiguous in-flight previously booked ACKs require operator reconciliation**.
There is no safe automatic inference of their historical per-order amount from
a list of IDs alone.

## Executed evidence

The pure projection module and its tests were reconstructed locally and
verified byte-for-byte using `git hash-object` against these committed
`dev` file blob IDs:

- `backend/app/services/snapback_exit_fill_projection.py` —
  `a6a4fc06b8907943936b4ccf97aaaef56918e049`
- `backend/tests/unit/test_snapback_exit_fill_projection.py` —
  `4891af34575771b062042021d0b2fe0ff89d2346`

`python -m pytest -q -p no:cacheprovider test_snapback_exit_fill_projection.py`
reported **37 passed** using isolated Python; no assertions failed. This
is a pure-module unit suite. Additional stop-child and broader manager
regression cases have been **committed but not executed** in the complete
Sterling app, and no actual Kite order, GTT, account, or broker receipt
was accessed.

## Required before promotion

Full-source test execution, monitored restart/partial-fill/cancel/protection
drills with an approved authenticated Kite account, complete broker exposure
reconciliation, and credible untouched after-cost forward performance for
the exact strategy identity remain **unverified**. Do not merge into main,
disable the safety guards, or claim profit/live functionality certification
based on these isolated checks. The operator is hospitalized; no on-host
commands should be required from them for this checkpoint.
